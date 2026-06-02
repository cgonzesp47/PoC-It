"""
PoC-it – Extractor determinista de "facts" desde el código generado (sin LLM).

Objetivo
--------
Generar un resumen estructurado (JSON/dict) del proyecto generado para:

- Alinear tests con el CÓDIGO real (evitar monkeypatch de símbolos inexistentes).
- Generar README_MANUAL coherente con stubs detectados y variables de entorno realmente usadas.
- Ser robusto frente a no-determinismo: el LLM puede generar distintos códigos para el mismo SPEC.

Este extractor NO intenta "entender" semántica completa; prioriza:
- Endpoints reales (FastAPI/APIRouter)
- Dependencias declaradas (Depends)
- Heurísticas de stubs (TODO/placeholder, dependencia inyectada sin uso, returns triviales)
- Variables de entorno explícitas (os.getenv)

Se recomienda ejecutarlo sobre `estructura_generada: Dict[path, content]` que ya tiene PoC-it
tras materializar los archivos, pero también soporta lectura desde disco si se desea.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple


# ----------------------------
# Modelos internos (facts)
# ----------------------------

@dataclass
class ObservedCallFact:
    receiver_param: str
    method_name: str
    arg_names: List[str]
    awaited: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["arg_names"] = d["arg_names"] or []
        return d


@dataclass
class EndpointFact:
    path: str
    method: str
    func_name: str
    module_path: str  # path del fichero, p.ej. app/endpoints/upload.py
    status_code: Optional[int] = None  # status_code declarado en el decorator (si existe)

    # Contrato de request (body) — mejor esfuerzo
    request_body_param: Optional[str] = None  # nombre del parámetro body (si existe)
    request_model: Optional[str] = None  # nombre de clase Pydantic si se detecta (p.ej. ProductCreate)
    request_required_fields: List[str] = None  # required del schema si se puede inferir
    request_optional_fields: List[str] = None  # optional del schema si se puede inferir

    # Contrato de request (params) — mejor esfuerzo (sin ejecutar imports)
    path_params: List[str] = None  # nombres de parámetros en la ruta, p.ej. ["id"]
    query_params_required: List[str] = None  # params query requeridos detectados (Query(...))
    query_params_optional: List[str] = None  # params query opcionales detectados (Query(default=...))
    header_params_required: List[str] = None  # Header(...)
    header_params_optional: List[str] = None  # Header(default=...)
    cookie_params_required: List[str] = None  # Cookie(...)
    cookie_params_optional: List[str] = None  # Cookie(default=...)
    form_params_required: List[str] = None  # Form(...)
    form_params_optional: List[str] = None  # Form(default=...)
    file_params_required: List[str] = None  # File(...)
    file_params_optional: List[str] = None  # File(default=...)

    # Contrato de respuesta (extraído del decorator y/o schema)
    response_model: Optional[str] = None  # nombre/expr del response_model en decorator (sin resolver import)
    response_model_required_fields: List[str] = None  # campos required del schema si se puede inferir
    response_model_optional_fields: List[str] = None  # campos opcionales del schema si se puede inferir

    depends: List[str] = None  # símbolos Depends(x) (solo nombre/expr; NO resoluble para imports)
    depends_imports: List[str] = None  # FQN resoluble (p.ej. "app.database.get_db") cuando se puede inferir
    injected_params: List[str] = None  # nombres de params del handler que parecen DI
    uses_injected: bool = False  # si se usa alguna variable inyectada
    stub_signals: List[str] = None  # evidencias de stub
    observed_calls: List[ObservedCallFact] = None  # llamadas sobre inyectados (p.ej. svc.create_product(db, product))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["depends"] = d["depends"] or []
        d["depends_imports"] = d.get("depends_imports") or []
        d["injected_params"] = d["injected_params"] or []
        d["stub_signals"] = d["stub_signals"] or []
        d["observed_calls"] = [c.to_dict() for c in (self.observed_calls or [])]
        d["request_required_fields"] = d["request_required_fields"] or []
        d["request_optional_fields"] = d["request_optional_fields"] or []
        d["path_params"] = d["path_params"] or []
        d["query_params_required"] = d["query_params_required"] or []
        d["query_params_optional"] = d["query_params_optional"] or []
        d["header_params_required"] = d["header_params_required"] or []
        d["header_params_optional"] = d["header_params_optional"] or []
        d["cookie_params_required"] = d["cookie_params_required"] or []
        d["cookie_params_optional"] = d["cookie_params_optional"] or []
        d["form_params_required"] = d["form_params_required"] or []
        d["form_params_optional"] = d["form_params_optional"] or []
        d["file_params_required"] = d["file_params_required"] or []
        d["file_params_optional"] = d["file_params_optional"] or []
        d["response_model_required_fields"] = d["response_model_required_fields"] or []
        d["response_model_optional_fields"] = d["response_model_optional_fields"] or []
        return d


@dataclass
class StubFact:
    file: str
    symbol: str  # función o clase
    signals: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {"file": self.file, "symbol": self.symbol, "signals": list(self.signals)}


@dataclass
class POCFacts:
    endpoints: List[EndpointFact]
    env_vars_explicit: List[str]
    stubs: List[StubFact]
    imports: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "endpoints": [e.to_dict() for e in self.endpoints],
            "env_vars_explicit": sorted(set(self.env_vars_explicit)),
            "stubs": [s.to_dict() for s in self.stubs],
            "imports": sorted(set(self.imports)),
        }


# ----------------------------
# Helpers AST / parsing
# ----------------------------

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)  # type: ignore[attr-defined]
    except Exception:
        return ""


def _extract_env_vars_from_source(source: str) -> List[str]:
    """
    Heurística: detectar os.getenv("X") y os.environ.get("X").
    """
    envs: List[str] = []
    # Nota: no escapar el backslash en raw-string salvo que quieras un backslash literal.
    for m in re.finditer(r"os\.getenv\(\s*['\"]([A-Z0-9_]+)['\"]", source):
        envs.append(m.group(1))
    for m in re.finditer(r"os\.environ\.get\(\s*['\"]([A-Z0-9_]+)['\"]", source):
        envs.append(m.group(1))
    return envs


def _collect_imports(tree: ast.AST) -> List[str]:
    out: List[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.append(a.name)
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            for a in n.names:
                if mod:
                    out.append(f"{mod}.{a.name}")
                else:
                    out.append(a.name)
    return out


def _is_depends_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name) and node.func.id == "Depends":
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr == "Depends":
        return True
    return False


def _depends_symbol_from_call(node: ast.Call) -> Optional[str]:
    if not node.args:
        return None
    arg0 = node.args[0]

    # DriveService.get_service (Attribute) o module.get_db
    if isinstance(arg0, ast.Attribute):
        return _safe_unparse(arg0) or None

    # get_db (Name)
    if isinstance(arg0, ast.Name):
        return arg0.id

    # Caso habitual en codegen: Depends(DriveService.get_service) llega como Call(DriveService.get_service, ...)
    # o incluso Depends(DriveService.get_service()) si el modelo "ejecuta" la factory.
    if isinstance(arg0, ast.Call):
        return _safe_unparse(arg0.func) or None

    return _safe_unparse(arg0) or None


def _router_decorator_info(
    dec: ast.AST,
) -> Optional[Tuple[str, str, Optional[int], Optional[str], Optional[str]]]:
    """
    Devuelve (method, path, status_code, response_model_expr, response_model_name)
    si el decorator es router.<method>("<path>", status_code=..., response_model=...).

    - response_model_expr: ast.unparse(...) del kw, sin resolver imports
    - response_model_name: mejor esfuerzo de nombre de clase (p.ej. "ProductResponse")
    """
    if not isinstance(dec, ast.Call):
        return None
    if not isinstance(dec.func, ast.Attribute):
        return None
    method = dec.func.attr.lower()
    if method not in _HTTP_METHODS:
        return None
    # primer arg normalmente es path
    if not dec.args:
        return None
    if isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
        status_code: Optional[int] = None
        response_model_expr: Optional[str] = None
        response_model_name: Optional[str] = None

        for kw in dec.keywords or []:
            if not isinstance(kw, ast.keyword):
                continue
            if kw.arg == "status_code" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                status_code = kw.value.value
            if kw.arg == "response_model":
                response_model_expr = _safe_unparse(kw.value) or None
                if isinstance(kw.value, ast.Name):
                    response_model_name = kw.value.id
                elif isinstance(kw.value, ast.Attribute):
                    response_model_name = kw.value.attr  # schemas.ProductResponse -> ProductResponse

        if response_model_name is None and response_model_expr:
            response_model_name = response_model_expr.split(".")[-1]

        return method.upper(), dec.args[0].value, status_code, response_model_expr, response_model_name
    return None


def _has_stub_comment(source: str) -> bool:
    # Heurística barata: TODO / Implement ... logic / placeholder / stub
    patterns = [
        r"\\bTODO\\b",
        r"\\bFIXME\\b",
        r"\\bplaceholder\\b",
        r"\\bstub\\b",
        r"Implement\\s+.*\\s+logic",
        r"not implemented",
    ]
    return any(re.search(p, source, flags=re.IGNORECASE) for p in patterns)


def _function_returns_trivial_dict(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for n in ast.walk(func):
        if isinstance(n, ast.Return):
            v = n.value
            if isinstance(v, ast.Dict) and len(v.keys) == 0:
                return True
    return False


def _function_uses_name(func: ast.AST, name: str) -> bool:
    for n in ast.walk(func):
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load):
            return True
    return False


def _call_target(node: ast.AST) -> Optional[Tuple[str, str]]:
    """
    Si node representa una llamada del tipo <receiver>.<method>(...),
    devuelve (receiver, method). Si no, None.

    Nota: También soporta llamadas sobre CLASES importadas (p.ej. `ProductService.create_product(...)`)
    siempre que el receiver sea un nombre simple (ast.Name).
    """
    if not isinstance(node, ast.Call):
        return None
    fn = node.func
    if not isinstance(fn, ast.Attribute):
        return None
    if not isinstance(fn.value, ast.Name):
        return None
    return fn.value.id, fn.attr


def _extract_arg_names(call: ast.Call) -> List[str]:
    out: List[str] = []
    for a in call.args or []:
        if isinstance(a, ast.Name):
            out.append(a.id)
        else:
            # No intentamos serializar expresiones complejas; dejamos string.
            s = _safe_unparse(a)
            out.append(s or "expr")
    return out


def _extract_observed_calls(
    func: ast.AST,
    injected_params: List[str],
    *,
    candidate_receivers: Optional[List[str]] = None,
) -> List[ObservedCallFact]:
    """
    Extrae llamadas relevantes dentro del endpoint sobre:
    - parámetros inyectados (Depends): `svc.create_product(...)`
    - y, en general, sobre "receivers" candidatos (clases/servicios importados): `ProductService.create_product(...)`

    Esto es CRÍTICO para alinear tests cuando el codegen usa servicios como métodos de instancia pero
    los invoca como si fueran métodos estáticos/clase (bug habitual), o cuando el wiring no inyecta svc.

    Ejemplo:
      await ProductService.create_product(db, product)
    -> ObservedCallFact(receiver_param="ProductService", method_name="create_product", arg_names=["db", "product"], awaited=True)
    """
    injected = set(injected_params or [])
    receivers = set(candidate_receivers or [])
    receivers |= injected

    calls: List[ObservedCallFact] = []

    for n in ast.walk(func):
        awaited = False
        call_node: Optional[ast.Call] = None

        if isinstance(n, ast.Await) and isinstance(n.value, ast.Call):
            awaited = True
            call_node = n.value
        elif isinstance(n, ast.Call):
            call_node = n

        if call_node is None:
            continue

        tgt = _call_target(call_node)
        if not tgt:
            continue
        receiver, method = tgt
        if receiver not in receivers:
            continue

        calls.append(
            ObservedCallFact(
                receiver_param=receiver,
                method_name=method,
                arg_names=_extract_arg_names(call_node),
                awaited=awaited,
            )
        )

    # Deduplicar preservando orden.
    # Nota: durante el walk podemos ver el mismo Call tanto "crudo" como dentro de un Await.
    # Para tests/mocks, nos interesa una entrada única por firma; si aparece awaited en algún sitio,
    # preferimos awaited=True.
    by_sig: Dict[tuple[str, str, tuple[str, ...]], ObservedCallFact] = {}
    order: List[tuple[str, str, tuple[str, ...]]] = []

    for c in calls:
        sig = (c.receiver_param, c.method_name, tuple(c.arg_names))
        if sig not in by_sig:
            by_sig[sig] = c
            order.append(sig)
        else:
            # si alguna vez fue awaited, conservar awaited=True
            if c.awaited and not by_sig[sig].awaited:
                by_sig[sig].awaited = True

    return [by_sig[sig] for sig in order]


def _build_pydantic_schema_index(estructura_generada: Dict[str, str]) -> Dict[str, Dict[str, List[str]]]:
    """
    Índice ultra simple de schemas Pydantic por nombre de clase.
    Devuelve: { "ProductResponse": { "required": [...], "optional": [...] } }

    Heurística:
    - Solo analiza ficheros app/schemas*.py o cualquier .py bajo app/ que contenga clases BaseModel.
    - Considera "required" si la anotación no tiene default y no es Optional/Union con None.
    - Considera "optional" si hay default o si el tipo incluye None (muy aproximado).
    """
    index: Dict[str, Dict[str, List[str]]] = {}

    for path, content in estructura_generada.items():
        # normalizar separadores independientemente de cómo venga el dict
        norm_path = path.replace("\\", "/")
        if not norm_path.endswith(".py"):
            continue
        if not norm_path.startswith("app/"):
            continue
        # reducir ruido: típicamente schemas.py, pero permitimos schemas.py y cualquier módulo de schemas
        if "schema" not in norm_path and "schemas" not in norm_path:
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        try:
            tree = ast.parse(content)
        except Exception:
            continue

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue

            # ¿Hereda de BaseModel? (soporta herencia indirecta: Product(BaseModel) o Product(ProductBase))
            inherits_basemodel = False
            base_names: List[str] = []
            for b in node.bases or []:
                s = (_safe_unparse(b) or "").strip()
                if not s:
                    continue
                base_names.append(s)

                # BaseModel, pydantic.BaseModel
                if s == "BaseModel" or s.endswith(".BaseModel") or s.endswith("BaseModel"):
                    inherits_basemodel = True

            if not inherits_basemodel:
                # herencia indirecta: si alguna base ya fue indexada, también lo consideramos schema pydantic
                for b in base_names:
                    b_name = b.split(".")[-1]
                    if b_name in index:
                        inherits_basemodel = True
                        break

            if not inherits_basemodel:
                continue

            # Si hereda de otro schema, parte de sus campos
            required: List[str] = []
            optional: List[str] = []

            for b in base_names:
                b_name = b.split(".")[-1]
                parent = index.get(b_name)
                if parent:
                    required.extend(parent.get("required") or [])
                    optional.extend(parent.get("optional") or [])

            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_name = stmt.target.id
                    ann = stmt.annotation
                    ann_s = _safe_unparse(ann)
                    has_default = stmt.value is not None

                    # Heurística de optional por tipo: "Optional[X]" o "X | None" o "Union[..., None]"
                    type_allows_none = ("None" in ann_s) or ("Optional[" in ann_s)

                    if has_default or type_allows_none:
                        if field_name not in optional:
                            optional.append(field_name)
                        if field_name in required:
                            required.remove(field_name)
                    else:
                        if field_name not in required:
                            required.append(field_name)
                        if field_name in optional:
                            optional.remove(field_name)

            index[node.name] = {"required": required, "optional": optional}

    return index


def _extract_route_path_params(route_path: str) -> List[str]:
    # /items/{id}/sub/{name} -> ["id", "name"]
    out: List[str] = []
    if not route_path:
        return out
    for m in re.finditer(r"\\{([A-Za-z_][A-Za-z0-9_]*)\\}", route_path):
        out.append(m.group(1))
    # uniq + orden estable
    seen: set[str] = set()
    res: List[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def _is_fastapi_param_call(node: ast.AST, names: tuple[str, ...]) -> bool:
    # Query(...), fastapi.Query(...), params.Query(...)
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id in names
    if isinstance(fn, ast.Attribute):
        return fn.attr in names
    return False


def _classify_required_optional_from_call(call: ast.Call) -> tuple[bool, bool]:
    """
    Devuelve (required, optional) para Query/Header/Cookie/Form/File.
    Heurística:
    - required si:
      - existe keyword `default` y es `...` (Ellipsis), o
      - primer arg posicional es `...` (Ellipsis), o
      - NO hay args ni default keyword (Query()): lo tratamos como optional (FastAPI suele requerir default=... para required)
    - optional si:
      - hay default distinto de Ellipsis, o
      - el primer arg posicional existe y NO es Ellipsis
    Nota: si no se puede inferir bien, asumimos optional (conservador para evitar falsos 422).
    """
    # keyword default=
    for kw in call.keywords or []:
        if isinstance(kw, ast.keyword) and kw.arg == "default":
            v = kw.value
            if isinstance(v, ast.Constant) and v.value is Ellipsis:
                return True, False
            # default explícito (incluye None, 0, "x", etc.) => optional
            return False, True

    # positional default
    if call.args:
        v0 = call.args[0]
        if isinstance(v0, ast.Constant) and v0.value is Ellipsis:
            return True, False
        return False, True

    return False, True


def _infer_depends_callable_from_param(
    *,
    arg: ast.arg,
    default_node: ast.AST,
) -> Optional[str]:
    """
    Caso especial FastAPI: `param: Type = Depends()` sin callable explícito.
    En ese caso, el callable efectivo suele ser la anotación del parámetro (Type).
    Devolvemos el nombre/expr (sin resolver imports).
    """
    if not _is_depends_call(default_node):
        return None
    if not isinstance(default_node, ast.Call):
        return None
    if default_node.args:
        # ya hay callable explícito: Depends(get_db) -> lo manejará _depends_symbol_from_call
        return None
    if not arg.annotation:
        return None
    # Ej: ProductService, services.ProductService
    return _safe_unparse(arg.annotation) or None


def extract_poc_facts_from_structure(estructura_generada: Dict[str, str]) -> POCFacts:
    """
    Entrada: estructura_generada { "app/main.py": "...", ... }
    Salida: POCFacts con endpoints/env vars/stubs.

    Nota: solo analiza ficheros .py.
    """
    endpoints: List[EndpointFact] = []
    stubs: List[StubFact] = []
    env_vars: List[str] = []
    imports: List[str] = []

    schema_index = _build_pydantic_schema_index(estructura_generada)

    for path, content in estructura_generada.items():
        norm_path = path.replace("\\\\", "/")
        if not norm_path.endswith(".py"):
            continue
        if not isinstance(content, str):
            continue

        env_vars.extend(_extract_env_vars_from_source(content))

        try:
            tree = ast.parse(content)
        except Exception:
            # No romper generación por un fichero inválido
            continue

        imports.extend(_collect_imports(tree))

        # detectar endpoints: funciones decoradas con router.*
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            decs = getattr(node, "decorator_list", []) or []
            for dec in decs:
                info = _router_decorator_info(dec)
                if not info:
                    continue
                method, route_path, status_code, response_model_expr, response_model_name = info

                # detectar request model + params (best-effort)
                request_body_param: Optional[str] = None
                request_model: Optional[str] = None
                request_required_fields: List[str] = []
                request_optional_fields: List[str] = []

                # params (source)
                path_params = _extract_route_path_params(route_path)
                query_required: List[str] = []
                query_optional: List[str] = []
                header_required: List[str] = []
                header_optional: List[str] = []
                cookie_required: List[str] = []
                cookie_optional: List[str] = []
                form_required: List[str] = []
                form_optional: List[str] = []
                file_required: List[str] = []
                file_optional: List[str] = []

                injected_params: List[str] = []
                depends_syms: List[str] = []
                depends_imports: List[str] = []

                # En AST, args.args contiene solo definiciones de argumentos.
                # Los valores por defecto están en node.args.defaults (alineados con los últimos N args).
                defaults = list(node.args.defaults or [])
                args_list = list(node.args.args or [])
                defaults_by_arg: Dict[str, ast.AST] = {}
                if defaults:
                    for a, d in zip(args_list[-len(defaults) :], defaults):
                        defaults_by_arg[a.arg] = d

                for arg in args_list:
                    # detectar dependencias (DI) primero
                    default_node = defaults_by_arg.get(arg.arg)
                    if default_node and _is_depends_call(default_node):
                        injected_params.append(arg.arg)

                        # Caso normal: Depends(callable)
                        sym = _depends_symbol_from_call(default_node)  # type: ignore[arg-type]
                        if sym:
                            depends_syms.append(sym)

                            # Mejor esfuerzo FQN: si el módulo importa el símbolo, podemos resolver a import-string.
                            # Soporta:
                            # - from app.database import get_db  -> app.database.get_db
                            # - import app.database as database  -> sym puede ser database.get_db (se deja como expr)
                            fqn: Optional[str] = None
                            for imp in _collect_imports(tree):
                                # exact match para from-imports
                                if imp.endswith(f".{sym}"):
                                    fqn = imp
                                    break
                            if fqn:
                                depends_imports.append(fqn)
                        else:
                            # Caso FastAPI: Depends() sin callable explícito.
                            inferred = _infer_depends_callable_from_param(arg=arg, default_node=default_node)
                            if inferred:
                                depends_syms.append(inferred)
                                # Si inferred es un nombre simple y existe import from, resolver también.
                                if "." not in inferred:
                                    for imp in _collect_imports(tree):
                                        if imp.endswith(f".{inferred}"):
                                            depends_imports.append(imp)
                                            break
                        continue

                    # Param sources: Query/Header/Cookie/Form/File
                    if default_node and isinstance(default_node, ast.Call):
                        if _is_fastapi_param_call(default_node, ("Query",)):
                            req, opt = _classify_required_optional_from_call(default_node)
                            (query_required if req else query_optional).append(arg.arg)
                            continue
                        if _is_fastapi_param_call(default_node, ("Header",)):
                            req, opt = _classify_required_optional_from_call(default_node)
                            (header_required if req else header_optional).append(arg.arg)
                            continue
                        if _is_fastapi_param_call(default_node, ("Cookie",)):
                            req, opt = _classify_required_optional_from_call(default_node)
                            (cookie_required if req else cookie_optional).append(arg.arg)
                            continue
                        if _is_fastapi_param_call(default_node, ("Form",)):
                            req, opt = _classify_required_optional_from_call(default_node)
                            (form_required if req else form_optional).append(arg.arg)
                            continue
                        if _is_fastapi_param_call(default_node, ("File",)):
                            req, opt = _classify_required_optional_from_call(default_node)
                            (file_required if req else file_optional).append(arg.arg)
                            continue

                    # request body candidate (no DI): primer arg tipado con schema conocido
                    if arg.annotation and request_model is None:
                        ann = arg.annotation
                        if isinstance(ann, ast.Name):
                            cls = ann.id
                            if cls in schema_index:
                                request_body_param = arg.arg
                                request_model = cls
                        elif isinstance(ann, ast.Attribute):
                            cls = ann.attr
                            if cls in schema_index:
                                request_body_param = arg.arg
                                request_model = cls

                if request_model:
                    schema_info = schema_index.get(request_model) or {}
                    request_required_fields = list(schema_info.get("required") or [])
                    request_optional_fields = list(schema_info.get("optional") or [])

                # ¿usa inyectados?
                uses_injected = any(_function_uses_name(node, p) for p in injected_params)

                stub_signals: List[str] = []
                if injected_params and not uses_injected:
                    stub_signals.append("dependency_injected_but_unused")
                if _function_returns_trivial_dict(node):
                    stub_signals.append("returns_empty_dict")
                if _has_stub_comment(content):
                    stub_signals.append("stub_comment_detected")

                # Candidatos: nombres de clases/servicios importadas en el módulo (simple heuristic).
                # Si el codegen llama a ProductService.create_product(...) esta señal aparece aquí.
                module_imported_names: List[str] = []
                for imp in _collect_imports(tree):
                    # ImportFrom: "app.services.product_service.ProductService" -> queremos "ProductService"
                    if "." in imp:
                        module_imported_names.append(imp.split(".")[-1])
                    else:
                        module_imported_names.append(imp)

                observed_calls = _extract_observed_calls(
                    node,
                    injected_params,
                    candidate_receivers=module_imported_names,
                )

                required_fields: List[str] = []
                optional_fields: List[str] = []

                # Intento: si response_model apunta a un schema Pydantic, inferir fields required/optional.
                # Preferimos el nombre inferido por AST (response_model_name) para evitar fallos por expr vacía.
                cls_name = response_model_name or (response_model_expr.split(".")[-1] if response_model_expr else None)
                if cls_name:
                    schema_info = schema_index.get(cls_name) or {}
                    required_fields = list(schema_info.get("required") or [])
                    optional_fields = list(schema_info.get("optional") or [])

                ep = EndpointFact(
                    path=route_path,
                    method=method,
                    func_name=node.name,
                    module_path=norm_path,
                    status_code=status_code,
                    request_body_param=request_body_param,
                    request_model=request_model,
                    request_required_fields=request_required_fields,
                    request_optional_fields=request_optional_fields,
                    path_params=path_params,
                    query_params_required=query_required,
                    query_params_optional=query_optional,
                    header_params_required=header_required,
                    header_params_optional=header_optional,
                    cookie_params_required=cookie_required,
                    cookie_params_optional=cookie_optional,
                    form_params_required=form_required,
                    form_params_optional=form_optional,
                    file_params_required=file_required,
                    file_params_optional=file_optional,
                    response_model=response_model_expr,
                    response_model_required_fields=required_fields,
                    response_model_optional_fields=optional_fields,
                    depends=depends_syms,
                    depends_imports=depends_imports,
                    injected_params=injected_params,
                    uses_injected=uses_injected,
                    stub_signals=stub_signals,
                    observed_calls=observed_calls,
                )
                endpoints.append(ep)

                if stub_signals:
                    stubs.append(StubFact(file=norm_path, symbol=node.name, signals=stub_signals))

    return POCFacts(endpoints=endpoints, env_vars_explicit=env_vars, stubs=stubs, imports=imports)
