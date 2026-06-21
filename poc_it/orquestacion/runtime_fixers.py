from __future__ import annotations
"""
Fixers deterministas para fallos típicos detectados por la verificación runtime.

Objetivo:
- Evitar depender del LLM para errores mecánicos (wiring) recurrentes.
- Aplicar parches pequeños, repetibles y seguros basados en heurísticas estáticas.

Diseño:
- Cada fixer recibe el `runtime_detail` (stdout/stderr del verificador) y la `estructura`
  (map path->content con paths POSIX dentro del proyecto, p.ej. app/endpoints/products.py).
- Devuelve un patch {path: content} si puede corregir el problema, o None si no aplica.

Reglas:
- No tocar archivos fuera de app/ y tests/ (salvo requirements*.txt si es estrictamente necesario).
- Cambios quirúrgicos: tocar lo mínimo para corregir el fallo.
- 1 fixer por iteración (el caller decide el loop).
"""

from dataclasses import dataclass
import ast
import re
from typing import Dict, Optional


@dataclass(frozen=True)
class FixResult:
    patched_files: Dict[str, str]
    message: str


# Ejemplo de error:
# TypeError: ProductService.create_product() missing 1 required positional argument: 'product'
# Generalización: <Class>.<method>() missing ... positional argument ...
_INSTANCE_METHOD_CALLED_AS_CLASS_RE = re.compile(
    r"TypeError:\\s*([A-Za-z_][A-Za-z0-9_]*)\\.([A-Za-z_][A-Za-z0-9_]*)\\(\\)\\s*missing\\s+\\d+\\s+required positional argument",
    re.IGNORECASE,
)


def _iter_py_files_under_app(estructura: Dict[str, str]):
    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("app/") or not path.endswith(".py"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        yield path, content


def _is_method_defined_as_instance(class_node: ast.ClassDef, method_name: str) -> bool:
    """
    True si dentro de la clase existe un def/async def con ese nombre y su primer argumento es 'self'
    y NO tiene decorator @classmethod/@staticmethod.
    """
    for item in class_node.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name != method_name:
            continue
        decorators = {getattr(d, "id", None) for d in item.decorator_list if isinstance(d, ast.Name)}
        if "classmethod" in decorators or "staticmethod" in decorators:
            return False
        if not item.args.args:
            return False
        first = item.args.args[0].arg
        return first == "self"
    return False


def _find_class_def(tree: ast.AST, class_name: str) -> Optional[ast.ClassDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    return None


def _find_import_from(tree: ast.AST, class_name: str) -> Optional[str]:
    """
    Devuelve el módulo importado si encuentra: from X import <class_name>
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == class_name:
                    return node.module
    return None


def _rewrite_calls_in_function(fn_src: str, class_name: str, method_name: str) -> str:
    """
    Reescribe dentro del texto de una función:
      await Class.method(args...)  ->  service = Class(); await service.method(args...)
      Class.method(args...)        ->  service = Class(); service.method(args...)

    Nota: operación por texto con regex para minimizar complejidad y porque queremos un patch mecánico.
    """
    # Detectar si ya hay una instancia creada (service = ClassName()) para no duplicar
    if re.search(rf"\\b\\w+\\s*=\\s*{re.escape(class_name)}\\(\\)\\s*$", fn_src, flags=re.MULTILINE):
        # Si ya hay instancia, solo cambiamos Class.method -> <var>.method cuando sea evidente
        return fn_src

    # Elegir nombre de variable que no choque demasiado
    var = "service"

    # Inserta `service = ClassName()` al inicio del bloque try (si existe) o al inicio de la función.
    lines = fn_src.splitlines(True)
    insert_at = None
    indent = ""
    for i, ln in enumerate(lines):
        m = re.match(r"^(\\s*)try\\s*:\\s*$", ln)
        if m:
            indent = m.group(1) + "    "
            insert_at = i + 1
            break
    if insert_at is None:
        # primera línea con indent de body (después de def ...)
        for i, ln in enumerate(lines):
            if re.match(r"^\\s*def\\s+|^\\s*async\\s+def\\s+", ln):
                continue
            if ln.strip() == "":
                continue
            m = re.match(r"^(\\s+)", ln)
            indent = (m.group(1) if m else "    ")
            insert_at = i
            break
        if insert_at is None:
            insert_at = len(lines)
            indent = "    "

    inst_line = f"{indent}{var} = {class_name}()\n"
    lines.insert(insert_at, inst_line)
    new_src = "".join(lines)

    # Reescrituras de llamada (await y no-await)
    new_src = re.sub(
        rf"\\bawait\\s+{re.escape(class_name)}\\.{re.escape(method_name)}\\(",
        f"await {var}.{method_name}(",
        new_src,
    )
    new_src = re.sub(
        rf"\\b{re.escape(class_name)}\\.{re.escape(method_name)}\\(",
        f"{var}.{method_name}(",
        new_src,
    )
    return new_src


def fix_instance_method_called_as_class(runtime_detail: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """Fix determinista para el caso: método de instancia llamado como si fuera de clase.

    1) Detecta el patrón en runtime_detail: TypeError: Class.method() missing ...
    2) Busca endpoints bajo app/ que:
       - importen esa Class desde algún módulo
       - llamen a Class.method(...)
    3) Busca el módulo de la clase (si está en estructura) y valida que method es de instancia (self) y no classmethod.
    4) Parchea el endpoint para instanciar la clase y usar la instancia.
    """
    if not runtime_detail:
        return None
    m = _INSTANCE_METHOD_CALLED_AS_CLASS_RE.search(runtime_detail)
    if not m:
        return None
    class_name = m.group(1)
    method_name = m.group(2)

    patched: Dict[str, str] = {}

    # Si podemos, validar que en el módulo del servicio el método es realmente de instancia.
    # (si no podemos validar, igual aplicamos heurística por seguridad: el error ya es concluyente)
    method_is_instance = None  # True/False/None

    # Buscar endpoints que importen esa clase y tengan la llamada Class.method(
    for path, content in _iter_py_files_under_app(estructura):
        if not path.startswith("app/endpoints/") and not path.startswith("app/api/") and not path.startswith("app/routers/"):
            # aún así, podría estar en app/endpoints.py; mantenemos heurística simple:
            pass

        if f"{class_name}.{method_name}(" not in content:
            continue

        try:
            tree = ast.parse(content)
        except Exception:
            continue

        mod = _find_import_from(tree, class_name)
        if mod and method_is_instance is None:
            # intentar localizar el archivo del módulo importado en estructura
            mod_path = mod.replace(".", "/") + ".py"
            if mod_path in estructura and isinstance(estructura.get(mod_path), str):
                try:
                    svc_tree = ast.parse(estructura[mod_path])
                    cls = _find_class_def(svc_tree, class_name)
                    if cls is not None:
                        method_is_instance = _is_method_defined_as_instance(cls, method_name)
                except Exception:
                    method_is_instance = None

        # Si sabemos que NO es instancia, no parcheamos
        if method_is_instance is False:
            continue

        # Parchear: instanciar y cambiar llamadas
        # Hacemos patch a nivel de función: simplificamos operando sobre el archivo completo
        new_content = content
        # Solo hacemos rewrite si hay llamadas directas. Insertar instancia en cada función afectada sería ideal,
        # pero para mantenerlo simple insertamos una instancia por función con una heurística textual.
        # Estrategia: reescribir todas las apariciones Class.method( y await Class.method( dentro del archivo.
        # Insertamos `service = Class()` dentro de cada endpoint function que contenga la llamada.
        # Implementación: split por def/async def y re-ensamblar.
        parts = re.split(r"(\\n(?=async\\s+def\\s+|def\\s+))", new_content)
        rebuilt = []
        for part in parts:
            if f"{class_name}.{method_name}(" in part:
                rebuilt.append(_rewrite_calls_in_function(part, class_name, method_name))
            else:
                rebuilt.append(part)
        new_content2 = "".join(rebuilt)

        if new_content2 != content:
            patched[path] = new_content2

    if not patched:
        return None

    return FixResult(
        patched_files=patched,
        message=f"Rewrote {class_name}.{method_name}(...) to instance call by creating {class_name}() in {len(patched)} file(s)",
    )


def apply_first_matching_fixer(runtime_detail: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    for fx in (fix_instance_method_called_as_class,):
        res = fx(runtime_detail, estructura)
        if res:
            return res
    return None
