from __future__ import annotations

import ast
import logging
from typing import Dict, List, Set, Tuple

from poc_it.generador.utils_imports import extraer_from_imports, extraer_imports


logger = logging.getLogger(__name__)


def codigo_python_valido(codigo: str) -> bool:
    """
    Devuelve True si el código Python parsea correctamente (AST válido).
    """
    try:
        ast.parse(codigo)
        return True
    except Exception:
        return False


def validar_proyecto(files: List[Dict[str, str]]) -> bool:
    """
    Valida sintácticamente todos los archivos .py generados.

    Además de AST:
    - Detecta patrones conocidos que rompen en runtime (p.ej. Pydantic v2 BaseSettings).
    """
    for f in files:
        path = f.get("path", "")
        content = f.get("content", "") or ""

        if path.endswith(".py"):
            if not codigo_python_valido(content):
                return False

            # Guardrail: Pydantic v2 rompe `from pydantic import BaseSettings`
            if "from pydantic import BaseSettings" in content:
                return False

    return True


def validar_paths_generados(
    files_generados: List[Dict[str, str]],
    allowed_paths: Set[str],
) -> Tuple[bool, List[str]]:
    errores: List[str] = []
    for f in files_generados:
        p = (f.get("path") or "").replace("\\", "/")
        if not p:
            errores.append("Archivo sin 'path'")
            continue
        if p not in allowed_paths:
            errores.append(f"El modelo devolvió un path no permitido: {p}")
    return (len(errores) == 0), errores


def validar_imports_internos(
    files_generados: List[Dict[str, str]],
    allowed_paths: Set[str],
) -> Tuple[bool, List[str]]:
    """
    Política:
    - Imports internos deben ser absolutos desde app.* (si importan módulos del proyecto)
    - Si aparece 'from services' / 'import services' u otros módulos de primer nivel no permitidos, se marca error.
    - Resolución básica: si importan app.x.y, se verifica existencia del archivo correspondiente en allowed_paths.

    Extra (crítico):
    - Verifica coherencia de símbolos para `from app.x.y import Z`:
      si el target module existe dentro del proyecto, Z debe existir en ese archivo (AST),
      evitando errores tipo ImportError "cannot import name ...".
    """
    errores: List[str] = []

    # Mapa módulo->path disponible (solo para app.*)
    mod_to_path: Dict[str, str] = {}
    for p in allowed_paths:
        if p.startswith("app/") and p.endswith(".py"):
            mod = p[:-3].replace("/", ".")  # app/services/a.py -> app.services.a
            mod_to_path[mod] = p

    # Índice path->content para chequear símbolos
    src_by_path: Dict[str, str] = {}
    for f in files_generados:
        fp = (f.get("path") or "").replace("\\", "/")
        if fp.endswith(".py"):
            src_by_path[fp] = f.get("content") or ""

    for f in files_generados:
        p = (f.get("path") or "").replace("\\", "/")
        if not p.endswith(".py"):
            continue

        src = f.get("content") or ""
        imports = extraer_imports(src)

        # 1) Validación de módulos importados
        for imp in imports:
            if imp.startswith("app."):
                # Validación resoluble
                if imp in mod_to_path:
                    continue
                # Permitir imports a paquetes (app.services) si existe __init__.py
                pkg_path = imp.replace(".", "/") + "/__init__.py"
                if pkg_path in allowed_paths:
                    continue
                # Si el import apunta a un módulo esperado del proyecto (allowed_paths),
                # pero ese fichero aún no está presente en `files_generados`, lo tratamos
                # como dependencia ausente para que el repair loop lo pida.
                expected_path = imp.replace(".", "/") + ".py"
                if expected_path in allowed_paths:
                    errores.append(f"{p}: dependencia interna ausente (no generada aún): {expected_path}")
                else:
                    errores.append(f"{p}: import interno no resoluble: {imp}")
            else:
                # Señales típicas de fallo estructural
                if imp.split(".")[0] in ("services", "endpoints", "config"):
                    errores.append(f"{p}: import inválido (debe ser desde app.*): {imp}")

        # 2) Validación de símbolos importados: from app.* import X
        for mod, sym in extraer_from_imports(src):
            if not mod.startswith("app."):
                continue

            target_path = mod_to_path.get(mod)
            if not target_path:
                # si es paquete, no validamos símbolo (podría ser export en __init__.py)
                continue

            target_src = src_by_path.get(target_path)
            if target_src is None:
                # El archivo existe en el plan (allowed_paths) pero no está presente en el estado actual.
                # Esto debe disparar repair loop del lote correcto, no un error de símbolo.
                errores.append(
                    f"{p}: dependencia interna ausente (no generada aún): {target_path}"
                )
                continue

            try:
                tree = ast.parse(target_src)
            except Exception:
                # AST ya se valida por otra vía; aquí no añadimos ruido
                continue

            defined: Set[str] = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined.add(node.name)
                elif isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            defined.add(t.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    defined.add(node.target.id)

            if sym not in defined:
                # Debug útil: en fallos típicos (p.ej. `router`) necesitamos ver qué
                # se consideró "defined" realmente, y una muestra del fichero target.
                if sym == "router":
                    snippet = "\n".join((target_src or "").splitlines()[:80])
                    logger.debug(
                        "Falta símbolo 'router' en %s (%s). Definidos=%s target_len=%s "
                        "src_by_path_keys=%s Snippet(80l):\n%s\n---",
                        mod,
                        target_path,
                        sorted(list(defined))[:30],
                        len(target_src),
                        len(src_by_path),
                        snippet,
                    )
                errores.append(
                    f"{p}: from-import inválido: '{sym}' no existe en {mod} ({target_path})"
                )

    return (len(errores) == 0), errores
