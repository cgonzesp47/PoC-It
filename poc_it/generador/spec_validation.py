from __future__ import annotations

import copy
import json
import keyword
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Set, Tuple

# ======================================================================================
# Public types
# ======================================================================================

SpecErrorSeverity = Literal["fatal", "warning"]


@dataclass(frozen=True)
class SpecValidationError:
    code: str
    severity: SpecErrorSeverity
    path: str
    message: str
    repair_hint: str | None = None


# ======================================================================================
# Public API
# ======================================================================================


def validate_spec(spec: dict) -> List[SpecValidationError]:
    """
    Validación estricta y determinista del SPEC.

    Principios:
    - No usa LLM.
    - No infiere desde texto libre.
    - No repara semántica inventando endpoints.
    """
    if not isinstance(spec, dict):
        return [
            SpecValidationError(
                code="SPEC_NOT_OBJECT",
                severity="fatal",
                path="$",
                message="SPEC no es un objeto JSON/dict",
            )
        ]

    errors: List[SpecValidationError] = []
    errors.extend(_validate_root_fields(spec))
    errors.extend(_validate_files(spec))
    errors.extend(_validate_endpoints(spec))
    errors.extend(_validate_dependencies(spec))
    errors.extend(_validate_persistence(spec))
    errors.extend(_validate_contracts(spec))
    errors.extend(_validate_test_strategy(spec))
    return errors


def validar_spec(spec: dict) -> Tuple[bool, List[str]]:
    """
    Wrapper compatible con el sistema legacy.

    ok=False si hay cualquier error fatal.
    """
    errs = validate_spec(spec)
    ok = not any(e.severity == "fatal" for e in errs)
    msgs = [f"{e.severity.upper()} {e.code} @ {e.path}: {e.message}" for e in errs]
    return ok, msgs


def repair_spec_deterministic(spec: dict) -> Tuple[dict, List[SpecValidationError]]:
    """
    Reparación determinista segura (sin LLM).

    Permitido:
    - normalizar slashes
    - eliminar duplicados (files/dependencies/dev_dependencies)
    - añadir __init__.py mínimos faltantes
    - añadir archivos base obligatorios faltantes
    - añadir bundle_files faltantes a files si ya estaban referenciados
    - añadir endpoint.file faltante a files si ya estaba referenciado
    - añadir contracts ligeros faltantes desde endpoints
    - añadir 404 a endpoints con {id} si errors está vacío
    - añadir request.path_params mínimo para {id} si falta (sin cambiar request.type)

    Prohibido:
    - inventar endpoints
    - cambiar persistence.required
    - añadir vendors
    - cambiar paths o methods
    - cambiar source.type
    - cambiar request.type cuando sea incorrecto (p.e. query por id): eso debe seguir siendo fatal
    """
    if not isinstance(spec, dict):
        return spec, [
            SpecValidationError(
                code="REPAIR_SPEC_NOT_OBJECT",
                severity="fatal",
                path="$",
                message="No se puede reparar: SPEC no es dict",
            )
        ]

    repaired: Dict[str, Any] = copy.deepcopy(spec)
    changes: List[SpecValidationError] = []

    # 1) normalizar files slashes + dedupe + required base + inits
    files = repaired.get("files")
    if isinstance(files, list):
        norm_files = [_norm_path(p) for p in files if isinstance(p, str) and p.strip()]
        norm_files = _dedupe_stable(norm_files)

        for req in _required_base_files():
            if req not in norm_files:
                norm_files.append(req)
                changes.append(
                    SpecValidationError(
                        code="REPAIR_ADD_REQUIRED_FILE",
                        severity="warning",
                        path="$.files",
                        message=f"Añadido archivo base requerido: {req}",
                        repair_hint="added_required_base_files",
                    )
                )

        before = set(norm_files)
        norm_files = completar_inits_en_files(norm_files)
        added_inits = [p for p in norm_files if p not in before and p.endswith("/__init__.py")]
        for p in added_inits:
            changes.append(
                SpecValidationError(
                    code="REPAIR_ADD_INIT",
                    severity="warning",
                    path="$.files",
                    message=f"Añadido __init__.py faltante: {p}",
                    repair_hint="added_missing_inits",
                )
            )

        repaired["files"] = norm_files

    # 2) asegurar endpoint.file y bundle_files en files (sin cambiar endpoint)
    endpoints = repaired.get("endpoints")
    if isinstance(endpoints, list) and isinstance(repaired.get("files"), list):
        file_set = set(repaired.get("files") or [])
        extra: List[str] = []
        for ep in endpoints:
            if not isinstance(ep, dict):
                continue

            ep_file = ep.get("file")
            if isinstance(ep_file, str) and ep_file.strip():
                ep_file_norm = _norm_path(ep_file)
                if ep_file_norm not in file_set and ep_file_norm not in extra:
                    extra.append(ep_file_norm)
                    changes.append(
                        SpecValidationError(
                            code="REPAIR_ADD_ENDPOINT_FILE",
                            severity="warning",
                            path="$.files",
                            message=f"Añadido endpoint.file referenciado en endpoints: {ep_file_norm}",
                            repair_hint="added_referenced_endpoint_file",
                        )
                    )

            for bf in (ep.get("bundle_files") or []):
                if not isinstance(bf, str) or not bf.strip():
                    continue
                bf_norm = _norm_path(bf)
                if bf_norm not in file_set and bf_norm not in extra:
                    extra.append(bf_norm)
                    changes.append(
                        SpecValidationError(
                            code="REPAIR_ADD_BUNDLE_FILE",
                            severity="warning",
                            path="$.files",
                            message=f"Añadido bundle_file referenciado en endpoints: {bf_norm}",
                            repair_hint="added_referenced_bundle_file",
                        )
                    )

        if extra:
            repaired["files"] = _dedupe_stable(list(repaired.get("files") or []) + extra)

    # 3) dedupe dependencies/dev_dependencies
    for dep_key in ("dependencies", "dev_dependencies"):
        deps = repaired.get(dep_key)
        if isinstance(deps, list):
            cleaned = [str(x).strip() for x in deps if isinstance(x, str) and str(x).strip()]
            repaired[dep_key] = _dedupe_stable(cleaned)

    # 4) completar contracts ligeros desde endpoints
    if isinstance(repaired.get("endpoints"), list):
        contracts = repaired.get("contracts")
        if not isinstance(contracts, list):
            contracts = []

        contract_keys = {
            (c.get("method"), c.get("path")) for c in contracts if isinstance(c, dict)
        }
        for ep in repaired.get("endpoints") or []:
            if not isinstance(ep, dict):
                continue
            k = (ep.get("method"), ep.get("path"))
            if k in contract_keys:
                continue

            contracts.append(
                {
                    "method": ep.get("method"),
                    "path": ep.get("path"),
                    "request_type": (ep.get("request") or {}).get("type"),
                    "file": ep.get("file"),
                    "source_type": (ep.get("source") or {}).get("type"),
                }
            )
            contract_keys.add(k)
            changes.append(
                SpecValidationError(
                    code="REPAIR_ADD_CONTRACT",
                    severity="warning",
                    path="$.contracts",
                    message=f"Añadido contrato ligero faltante para endpoint {k[0]} {k[1]}",
                    repair_hint="added_missing_contracts",
                )
            )

        repaired["contracts"] = contracts

    # 5) repairs por endpoint (404 + path_params)
    if isinstance(repaired.get("endpoints"), list):
        for i, ep in enumerate(repaired.get("endpoints") or []):
            if not isinstance(ep, dict):
                continue

            method = str(ep.get("method") or "").upper().strip()
            path = str(ep.get("path") or "").strip()

            # 5.1) añadir path_params mínimo si {id} y request sin path_params
            if "{id}" in path:
                req = ep.get("request")
                if isinstance(req, dict) and "path_params" not in req:
                    req["path_params"] = {"id": "string"}
                    changes.append(
                        SpecValidationError(
                            code="REPAIR_ADD_PATH_PARAMS",
                            severity="warning",
                            path=f"$.endpoints[{i}].request.path_params",
                            message="Añadido request.path_params mínimo para endpoint con {id}",
                            repair_hint="add_path_params",
                        )
                    )

            # 5.2) añadir 404 por defecto si errors vacío
            if "{id}" in path and method in ("GET", "PUT", "PATCH", "DELETE"):
                errs = ep.get("errors")
                if errs is None:
                    ep["errors"] = []
                    errs = ep["errors"]

                if isinstance(errs, list) and not errs:
                    ep["errors"] = [{"status_code": 404, "code": "not_found"}]
                    changes.append(
                        SpecValidationError(
                            code="REPAIR_ADD_404",
                            severity="warning",
                            path=f"$.endpoints[{i}].errors",
                            message=f"Añadido 404 por defecto a {method} {path}",
                            repair_hint="add_404_if_id_endpoint",
                        )
                    )

    return repaired, changes


# ======================================================================================
# Legacy compatibility helpers (kept only if imported by other modules)
# ======================================================================================


def _extraer_json_tolerante(respuesta: str) -> Optional[dict]:
    """
    Intenta parsear JSON de forma tolerante.

    Casos soportados:
    - JSON limpio
    - Texto extra antes/después del JSON
    - Respuestas con bloques Markdown (```json ... ```)
    - Respuestas con múltiples bloques: extrae el primer {...} que parezca JSON
    """
    if not isinstance(respuesta, str) or "{" not in respuesta:
        return None

    s = respuesta.strip()

    if "```" in s:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL | re.IGNORECASE)
        if m:
            s = m.group(1).strip()

    try:
        return json.loads(s)
    except Exception:
        pass

    try:
        start = s.index("{")
        end = s.rindex("}")
        candidate = s[start : end + 1]
        return json.loads(candidate)
    except Exception:
        return None


def normalizar_paths(files: List[Dict[str, str]]) -> List[str]:
    return [f.get("path", "").replace("\\", "/") for f in files if f.get("path")]


def carpetas_de_codigo(py_paths: List[str]) -> Set[str]:
    carpetas: Set[str] = set()
    for p in py_paths:
        if "/" in p:
            carpetas.add(p.rsplit("/", 1)[0])
    return carpetas


def completar_inits_en_files(files: List[str]) -> List[str]:
    """
    Asegura que toda carpeta que contenga un .py tenga su __init__.py declarado.
    """
    norm_files = [str(p).replace("\\", "/") for p in files]
    extra: Set[str] = set()

    py_paths = [p for p in norm_files if p.endswith(".py") and p.startswith("app/")]
    for carpeta in carpetas_de_codigo(py_paths):
        init_path = f"{carpeta}/__init__.py"
        if init_path not in norm_files:
            extra.add(init_path)

    return norm_files + sorted(extra)


def persistir_spec_debug(
    nombre_archivo: str,
    spec: dict,
    descripcion_global: str,
    contexto_normalizado: dict | None,
) -> None:
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_path = debug_dir / nombre_archivo
        payload: Dict[str, Any] = {
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "descripcion_global": descripcion_global,
            "contexto_normalizado": contexto_normalizado,
            "spec": spec,
        }
        debug_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[DEBUG] SPEC persistido en: {debug_path.as_posix()}")
    except Exception as e:
        print(f"[DEBUG] No se pudo persistir SPEC: {e}")


# ======================================================================================
# Internal validation helpers
# ======================================================================================

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_ALLOWED_REQUEST_TYPES = {"json", "multipart", "query", "none"}
_ALLOWED_SOURCE_TYPES = {"explicit", "proposed", "builder_default"}
_ALLOWED_STATUSES = {"draft", "valid", "degraded"}
_ALLOWED_PERSISTENCE_KINDS = {
    "relational",
    "document",
    "key_value",
    "object_storage",
    "event_log",
    "unknown",
}
_REQUIRED_DEPS = {"fastapi", "uvicorn"}
_REQUIRED_DEV_DEPS = {"pytest", "httpx"}


def _required_base_files() -> List[str]:
    return [
        "app/__init__.py",
        "app/main.py",
        "app/api/__init__.py",
        "app/api/router.py",
        "app/api/endpoints/__init__.py",
        "app/core/__init__.py",
        "app/core/config.py",
        "requirements.txt",
        "README.md",
    ]


def _validate_root_fields(spec: Dict[str, Any]) -> List[SpecValidationError]:
    e: List[SpecValidationError] = []

    def req_eq(path: str, key: str, expected: Any) -> None:
        v = spec.get(key)
        if v != expected:
            e.append(
                SpecValidationError(
                    code="ROOT_FIELD_INVALID",
                    severity="fatal",
                    path=path,
                    message=f"{key} debe ser exactamente {expected!r} (actual={v!r})",
                )
            )

    # (1) root strict fields
    req_eq("$.schema_version", "schema_version", "pocit.spec.v1")

    status = spec.get("status")
    if status not in _ALLOWED_STATUSES:
        e.append(
            SpecValidationError(
                code="ROOT_STATUS_INVALID",
                severity="fatal",
                path="$.status",
                message=f"status inválido: {status!r}",
            )
        )

    req_eq("$.entrypoint", "entrypoint", "app.main:app")
    req_eq("$.run_command", "run_command", "uvicorn app.main:app --reload")

    imports_policy = spec.get("imports_policy")
    if imports_policy != "absolute_from_app":
        e.append(
            SpecValidationError(
                code="ROOT_IMPORTS_POLICY_INVALID",
                severity="fatal",
                path="$.imports_policy",
                message=f"imports_policy debe ser 'absolute_from_app' (actual={imports_policy!r})",
            )
        )

    # (1) root required collection types
    e.extend(_require_list(spec, "files", "$.files", fatal=True, non_empty=True))
    e.extend(_require_list(spec, "dependencies", "$.dependencies", fatal=True, non_empty=False))
    e.extend(_require_list(spec, "dev_dependencies", "$.dev_dependencies", fatal=True, non_empty=False))
    e.extend(_validate_env(spec))
    e.extend(_require_list(spec, "endpoints", "$.endpoints", fatal=True, non_empty=False))

    # assumptions: list[str] (warning on invalid items)
    assumptions = spec.get("assumptions")
    if not isinstance(assumptions, list):
        e.append(
            SpecValidationError(
                code="ASSUMPTIONS_NOT_LIST",
                severity="fatal",
                path="$.assumptions",
                message="assumptions debe ser lista",
            )
        )
    else:
        for i, it in enumerate(assumptions):
            if not isinstance(it, str):
                e.append(
                    SpecValidationError(
                        code="ASSUMPTION_ITEM_NOT_STRING",
                        severity="warning",
                        path=f"$.assumptions[{i}]",
                        message="assumptions debe contener solo strings",
                    )
                )

    # required objects exist
    for key in ("contracts", "persistence", "test_strategy", "source"):
        if key not in spec:
            e.append(
                SpecValidationError(
                    code="ROOT_MISSING_KEY",
                    severity="fatal",
                    path=f"$.{key}",
                    message=f"Falta campo raíz requerido: {key}",
                )
            )

    # (5) global source validation
    e.extend(_validate_global_source(spec))

    return e


def _validate_env(spec: Dict[str, Any]) -> List[SpecValidationError]:
    env = spec.get("env")
    if not isinstance(env, list):
        return [
            SpecValidationError(
                code="ENV_NOT_LIST",
                severity="fatal",
                path="$.env",
                message="env debe ser lista",
            )
        ]

    e: List[SpecValidationError] = []
    for i, it in enumerate(env):
        if isinstance(it, str):
            continue
        if isinstance(it, dict):
            name = it.get("name")
            if not isinstance(name, str) or not name.strip():
                e.append(
                    SpecValidationError(
                        code="ENV_ITEM_INVALID",
                        severity="fatal",
                        path=f"$.env[{i}]",
                        message="env dict debe contener name string",
                    )
                )
            continue
        e.append(
            SpecValidationError(
                code="ENV_ITEM_INVALID",
                severity="fatal",
                path=f"$.env[{i}]",
                message="env items deben ser string o dict",
            )
        )
    return e


def _validate_global_source(spec: Dict[str, Any]) -> List[SpecValidationError]:
    e: List[SpecValidationError] = []
    source = spec.get("source")
    if source is None:
        # root already checks presence, but keep defensive
        return e
    if not isinstance(source, dict):
        return [
            SpecValidationError(
                code="SOURCE_NOT_OBJECT",
                severity="fatal",
                path="$.source",
                message="source debe ser dict",
            )
        ]

    # warnings si faltan (o si existen pero mal tipo)
    for k in ("from_user", "from_assumptions"):
        if k not in source:
            e.append(
                SpecValidationError(
                    code="SOURCE_LIST_MISSING",
                    severity="warning",
                    path=f"$.source.{k}",
                    message=f"source.{k} no está presente",
                )
            )
            continue
        v = source.get(k)
        if not isinstance(v, list):
            e.append(
                SpecValidationError(
                    code="SOURCE_LIST_INVALID",
                    severity="warning",
                    path=f"$.source.{k}",
                    message=f"source.{k} debe ser lista de strings",
                )
            )
            continue
        for i, it in enumerate(v):
            if not isinstance(it, str):
                e.append(
                    SpecValidationError(
                        code="SOURCE_ITEM_INVALID",
                        severity="warning",
                        path=f"$.source.{k}[{i}]",
                        message="items de source deben ser strings",
                    )
                )
    return e


def _validate_files(spec: Dict[str, Any]) -> List[SpecValidationError]:
    e: List[SpecValidationError] = []
    files = spec.get("files")
    if not isinstance(files, list):
        return e

    norm_files: List[str] = []
    for i, p in enumerate(files):
        if not isinstance(p, str) or not p.strip():
            e.append(
                SpecValidationError(
                    code="FILES_INVALID_ITEM",
                    severity="fatal",
                    path=f"$.files[{i}]",
                    message="Path inválido (no string o vacío)",
                )
            )
            continue
        np = _norm_path(p)
        if np != p:
            e.append(
                SpecValidationError(
                    code="FILES_NOT_NORMALIZED",
                    severity="warning",
                    path=f"$.files[{i}]",
                    message=f"Path no normalizado: {p!r} -> {np!r}",
                    repair_hint="normalize_slashes",
                )
            )
        norm_files.append(np)

    for req in _required_base_files():
        if req not in norm_files:
            e.append(
                SpecValidationError(
                    code="FILES_MISSING_REQUIRED",
                    severity="fatal",
                    path="$.files",
                    message=f"Falta archivo base requerido: {req}",
                    repair_hint="add_required_base_files",
                )
            )

    for p in _find_duplicates(norm_files):
        e.append(
            SpecValidationError(
                code="FILES_DUPLICATE",
                severity="fatal",
                path="$.files",
                message=f"Path duplicado en files: {p}",
                repair_hint="dedupe_files",
            )
        )

    for p in norm_files:
        if p.startswith("/") or re.match(r"^[A-Za-z]:\\\\", p):
            e.append(
                SpecValidationError(
                    code="FILES_ABSOLUTE_PATH",
                    severity="fatal",
                    path="$.files",
                    message=f"No se permiten paths absolutos: {p}",
                )
            )
        if ".." in p.split("/"):
            e.append(
                SpecValidationError(
                    code="FILES_PATH_TRAVERSAL",
                    severity="fatal",
                    path="$.files",
                    message=f"No se permite '..' en paths: {p}",
                )
            )
        if "\\" in p:
            e.append(
                SpecValidationError(
                    code="FILES_BACKSLASH",
                    severity="fatal",
                    path="$.files",
                    message=f"Todos los paths deben usar '/': {p}",
                )
            )
        if p.endswith(".py") and not p.startswith("app/"):
            e.append(
                SpecValidationError(
                    code="FILES_PY_OUTSIDE_APP",
                    severity="fatal",
                    path="$.files",
                    message=f"Archivo Python fuera de app/: {p}",
                )
            )

    endpoints = spec.get("endpoints")
    if isinstance(endpoints, list):
        file_set = set(norm_files)
        for idx, ep in enumerate(endpoints):
            if not isinstance(ep, dict):
                continue
            ep_file = ep.get("file")
            if isinstance(ep_file, str) and ep_file.strip():
                ep_file_norm = _norm_path(ep_file)
                if ep_file_norm not in file_set:
                    e.append(
                        SpecValidationError(
                            code="FILES_ENDPOINT_FILE_MISSING",
                            severity="fatal",
                            path=f"$.endpoints[{idx}].file",
                            message=f"Endpoint referencia archivo no incluido en files: {ep_file_norm}",
                            repair_hint="add_referenced_endpoint_file",
                        )
                    )
            for j, bf in enumerate(ep.get("bundle_files") or []):
                if not isinstance(bf, str) or not bf.strip():
                    continue
                bf_norm = _norm_path(bf)
                if bf_norm not in file_set:
                    e.append(
                        SpecValidationError(
                            code="FILES_BUNDLE_FILE_MISSING",
                            severity="fatal",
                            path=f"$.endpoints[{idx}].bundle_files[{j}]",
                            message=f"Endpoint referencia bundle_file no incluido en files: {bf_norm}",
                            repair_hint="add_referenced_bundle_file",
                        )
                    )
    return e


def _validate_endpoints(spec: Dict[str, Any]) -> List[SpecValidationError]:
    e: List[SpecValidationError] = []
    endpoints = spec.get("endpoints")
    if not isinstance(endpoints, list):
        return e

    seen: Set[Tuple[str, str]] = set()
    for i, ep in enumerate(endpoints):
        if not isinstance(ep, dict):
            e.append(
                SpecValidationError(
                    code="ENDPOINT_NOT_OBJECT",
                    severity="fatal",
                    path=f"$.endpoints[{i}]",
                    message="Endpoint debe ser objeto/dict",
                )
            )
            continue

        method = str(ep.get("method") or "").upper().strip()
        path = str(ep.get("path") or "").strip()
        file_ = ep.get("file")
        func = ep.get("func")

        if method not in _ALLOWED_METHODS:
            e.append(
                SpecValidationError(
                    code="ENDPOINT_METHOD_INVALID",
                    severity="fatal",
                    path=f"$.endpoints[{i}].method",
                    message=f"method inválido: {method!r}",
                )
            )
        if not path.startswith("/"):
            e.append(
                SpecValidationError(
                    code="ENDPOINT_PATH_INVALID",
                    severity="fatal",
                    path=f"$.endpoints[{i}].path",
                    message=f"path debe empezar por '/': {path!r}",
                )
            )

        k = (method, path)
        if k in seen:
            e.append(
                SpecValidationError(
                    code="ENDPOINT_DUPLICATE_METHOD_PATH",
                    severity="fatal",
                    path=f"$.endpoints[{i}]",
                    message=f"Duplicado por method+path: {method} {path}",
                )
            )
        else:
            seen.add(k)

        if not isinstance(file_, str) or not file_.strip():
            e.append(
                SpecValidationError(
                    code="ENDPOINT_FILE_MISSING",
                    severity="fatal",
                    path=f"$.endpoints[{i}].file",
                    message="file requerido",
                )
            )
        else:
            f = _norm_path(file_)
            if not f.startswith("app/api/endpoints/") or not f.endswith(".py"):
                e.append(
                    SpecValidationError(
                        code="ENDPOINT_FILE_INVALID",
                        severity="fatal",
                        path=f"$.endpoints[{i}].file",
                        message=f"file debe estar bajo app/api/endpoints/*.py (actual={f!r})",
                    )
                )

        if not isinstance(func, str) or not func.strip() or not _is_valid_python_identifier(func):
            e.append(
                SpecValidationError(
                    code="ENDPOINT_FUNC_INVALID",
                    severity="fatal",
                    path=f"$.endpoints[{i}].func",
                    message=f"func debe ser identificador Python válido (actual={func!r})",
                )
            )

        req = ep.get("request")
        resp = ep.get("response")

        if not isinstance(req, dict):
            e.append(
                SpecValidationError(
                    code="ENDPOINT_REQUEST_MISSING",
                    severity="fatal",
                    path=f"$.endpoints[{i}].request",
                    message="request requerido",
                )
            )
        else:
            e.extend(_validate_request_block(req, endpoint=ep, idx=i))

        if not isinstance(resp, dict):
            e.append(
                SpecValidationError(
                    code="ENDPOINT_RESPONSE_MISSING",
                    severity="fatal",
                    path=f"$.endpoints[{i}].response",
                    message="response requerido",
                )
            )
        else:
            e.extend(_validate_response_block(resp, endpoint=ep, idx=i))

        source = ep.get("source")
        if not isinstance(source, dict):
            e.append(
                SpecValidationError(
                    code="ENDPOINT_SOURCE_MISSING",
                    severity="fatal",
                    path=f"$.endpoints[{i}].source",
                    message="source requerido",
                )
            )
        else:
            st = source.get("type")
            if st not in _ALLOWED_SOURCE_TYPES:
                e.append(
                    SpecValidationError(
                        code="ENDPOINT_SOURCE_TYPE_INVALID",
                        severity="fatal",
                        path=f"$.endpoints[{i}].source.type",
                        message=f"source.type inválido: {st!r}",
                    )
                )
            if st == "explicit" and not (
                isinstance(source.get("evidence"), str) and source.get("evidence").strip()
            ):
                e.append(
                    SpecValidationError(
                        code="ENDPOINT_EXPLICIT_MISSING_EVIDENCE",
                        severity="fatal",
                        path=f"$.endpoints[{i}].source.evidence",
                        message="explicit endpoint requiere source.evidence",
                    )
                )
            if st == "proposed" and not (
                isinstance(source.get("assumption"), str) and source.get("assumption").strip()
            ):
                e.append(
                    SpecValidationError(
                        code="ENDPOINT_PROPOSED_MISSING_ASSUMPTION",
                        severity="fatal",
                        path=f"$.endpoints[{i}].source.assumption",
                        message="proposed endpoint requiere source.assumption",
                    )
                )

        errs = ep.get("errors")
        if errs is None:
            e.append(
                SpecValidationError(
                    code="ENDPOINT_ERRORS_MISSING",
                    severity="warning",
                    path=f"$.endpoints[{i}].errors",
                    message="errors no presente; se asumirá []",
                    repair_hint="add_default_errors",
                )
            )
            errs = []
        if not isinstance(errs, list):
            e.append(
                SpecValidationError(
                    code="ENDPOINT_ERRORS_INVALID",
                    severity="fatal",
                    path=f"$.endpoints[{i}].errors",
                    message="errors debe ser lista",
                )
            )
        else:
            e.extend(_validate_errors_block(errs, endpoint=ep, idx=i))

    return e


def _validate_request_block(
    req: Dict[str, Any], *, endpoint: Dict[str, Any], idx: int
) -> List[SpecValidationError]:
    e: List[SpecValidationError] = []
    rt = req.get("type")
    if rt not in _ALLOWED_REQUEST_TYPES:
        e.append(
            SpecValidationError(
                code="REQUEST_TYPE_INVALID",
                severity="fatal",
                path=f"$.endpoints[{idx}].request.type",
                message=f"request.type inválido: {rt!r}",
            )
        )

    if rt == "json" and "schema" not in req:
        e.append(
            SpecValidationError(
                code="REQUEST_JSON_SCHEMA_MISSING",
                severity="fatal",
                path=f"$.endpoints[{idx}].request.schema",
                message="request.type='json' requiere request.schema (puede ser {})",
            )
        )

    path = str(endpoint.get("path") or "")
    if "{id}" in path:
        pp = req.get("path_params")
        if not isinstance(pp, dict):
            e.append(
                SpecValidationError(
                    code="REQUEST_PATH_PARAMS_MISSING",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].request.path_params",
                    message="Endpoint con {id} requiere request.path_params (dict) con id",
                    repair_hint="add_path_params",
                )
            )
        else:
            if "id" not in pp:
                e.append(
                    SpecValidationError(
                        code="REQUEST_PATH_PARAM_ID_MISSING",
                        severity="fatal",
                        path=f"$.endpoints[{idx}].request.path_params.id",
                        message="Falta path param 'id'",
                    )
                )

        if rt == "query":
            e.append(
                SpecValidationError(
                    code="REQUEST_ID_AS_QUERY",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].request.type",
                    message="Endpoint con {id} no puede usar request.type='query' solo por id; usar path_params + type='none'",
                )
            )

    if "path_params" in req and not isinstance(req.get("path_params"), dict):
        e.append(
            SpecValidationError(
                code="REQUEST_PATH_PARAMS_INVALID",
                severity="fatal",
                path=f"$.endpoints[{idx}].request.path_params",
                message="request.path_params debe ser dict",
            )
        )

    return e


def _validate_response_block(
    resp: Dict[str, Any], *, endpoint: Dict[str, Any], idx: int
) -> List[SpecValidationError]:
    e: List[SpecValidationError] = []
    if "json_example" not in resp or resp.get("json_example") is None:
        e.append(
            SpecValidationError(
                code="RESPONSE_JSON_EXAMPLE_MISSING",
                severity="fatal",
                path=f"$.endpoints[{idx}].response.json_example",
                message="response.json_example requerido y no puede ser null",
            )
        )
        return e

    ex = resp.get("json_example")
    if not isinstance(ex, (dict, list)):
        e.append(
            SpecValidationError(
                code="RESPONSE_JSON_EXAMPLE_INVALID",
                severity="fatal",
                path=f"$.endpoints[{idx}].response.json_example",
                message="response.json_example debe ser dict o list",
            )
        )
        return e

    method = str(endpoint.get("method") or "").upper().strip()
    path = str(endpoint.get("path") or "").strip()
    source_type = (endpoint.get("source") or {}).get("type")

    if path != "/health" and isinstance(ex, dict) and ex == {}:
        e.append(
            SpecValidationError(
                code="RESPONSE_EXAMPLE_EMPTY",
                severity="warning",
                path=f"$.endpoints[{idx}].response.json_example",
                message="json_example es {} (vacío) para endpoint no-health",
            )
        )

    if method == "GET" and "{id}" not in path and source_type != "builder_default":
        if isinstance(ex, dict) and ex.get("ok") is True and len(ex.keys()) == 1:
            e.append(
                SpecValidationError(
                    code="RESPONSE_EXAMPLE_WEAK",
                    severity="warning",
                    path=f"$.endpoints[{idx}].response.json_example",
                    message="GET list debería devolver list; json_example parece genérico {'ok': true}",
                )
            )

    return e


def _validate_errors_block(
    errs: List[Any], *, endpoint: Dict[str, Any], idx: int
) -> List[SpecValidationError]:
    e: List[SpecValidationError] = []
    method = str(endpoint.get("method") or "").upper().strip()
    path = str(endpoint.get("path") or "").strip()

    has_404 = False
    for j, it in enumerate(errs):
        if isinstance(it, int):
            if it == 404:
                has_404 = True
            continue
        if isinstance(it, dict):
            sc = it.get("status_code")
            if sc == 404:
                has_404 = True
            elif isinstance(sc, str) and sc.isdigit() and int(sc) == 404:
                has_404 = True
            continue
        e.append(
            SpecValidationError(
                code="ERRORS_ITEM_INVALID",
                severity="fatal",
                path=f"$.endpoints[{idx}].errors[{j}]",
                message="Cada error debe ser int o dict",
            )
        )

    if "{id}" in path and method in ("GET", "PUT", "PATCH", "DELETE") and not has_404:
        e.append(
            SpecValidationError(
                code="ERRORS_404_MISSING",
                severity="warning",
                path=f"$.endpoints[{idx}].errors",
                message="Endpoint con {id} debería incluir 404",
                repair_hint="add_404_if_id_endpoint",
            )
        )

    return e


def _validate_dependencies(spec: Dict[str, Any]) -> List[SpecValidationError]:
    e: List[SpecValidationError] = []

    deps = spec.get("dependencies")
    dev_deps = spec.get("dev_dependencies")

    if not isinstance(deps, list):
        return [
            SpecValidationError(
                code="DEPS_NOT_LIST",
                severity="fatal",
                path="$.dependencies",
                message="dependencies debe ser lista",
            )
        ]

    deps_clean = [str(x).strip() for x in deps if isinstance(x, str) and str(x).strip()]
    for d in _find_duplicates(deps_clean):
        e.append(
            SpecValidationError(
                code="DEPS_DUPLICATE",
                severity="fatal",
                path="$.dependencies",
                message=f"Dependencia duplicada: {d}",
                repair_hint="dedupe_dependencies",
            )
        )
    for m in sorted([x for x in _REQUIRED_DEPS if x not in set(deps_clean)]):
        e.append(
            SpecValidationError(
                code="DEPS_REQUIRED_MISSING",
                severity="fatal",
                path="$.dependencies",
                message=f"Falta dependencia requerida: {m}",
            )
        )

    if not isinstance(dev_deps, list):
        e.append(
            SpecValidationError(
                code="DEV_DEPS_NOT_LIST",
                severity="fatal",
                path="$.dev_dependencies",
                message="dev_dependencies debe ser lista",
            )
        )
        return e

    dev_clean = [str(x).strip() for x in dev_deps if isinstance(x, str) and str(x).strip()]
    for d in _find_duplicates(dev_clean):
        e.append(
            SpecValidationError(
                code="DEV_DEPS_DUPLICATE",
                severity="fatal",
                path="$.dev_dependencies",
                message=f"Dev dependencia duplicada: {d}",
                repair_hint="dedupe_dependencies",
            )
        )
    for m in sorted([x for x in _REQUIRED_DEV_DEPS if x not in set(dev_clean)]):
        e.append(
            SpecValidationError(
                code="DEV_DEPS_REQUIRED_MISSING",
                severity="fatal",
                path="$.dev_dependencies",
                message=f"Falta dev dependencia requerida: {m}",
            )
        )

    return e


def _validate_persistence(spec: Dict[str, Any]) -> List[SpecValidationError]:
    p = spec.get("persistence")
    if not isinstance(p, dict):
        return [
            SpecValidationError(
                code="PERSISTENCE_NOT_OBJECT",
                severity="fatal",
                path="$.persistence",
                message="persistence debe ser dict",
            )
        ]

    required = p.get("required")
    if not isinstance(required, bool):
        return [
            SpecValidationError(
                code="PERSISTENCE_REQUIRED_INVALID",
                severity="fatal",
                path="$.persistence.required",
                message="persistence.required debe ser bool",
            )
        ]

    e: List[SpecValidationError] = []

    if required is False:
        if p.get("kind") is not None:
            e.append(
                SpecValidationError(
                    code="PERSISTENCE_KIND_FORBIDDEN",
                    severity="fatal",
                    path="$.persistence.kind",
                    message="Si persistence.required=false, kind debe ser None",
                )
            )
        if bool(p.get("durable_state")) is True:
            e.append(
                SpecValidationError(
                    code="PERSISTENCE_DURABLE_FORBIDDEN",
                    severity="fatal",
                    path="$.persistence.durable_state",
                    message="Si persistence.required=false, durable_state debe ser False",
                )
            )
        if (p.get("business_entities") or []) != []:
            e.append(
                SpecValidationError(
                    code="PERSISTENCE_ENTITIES_FORBIDDEN",
                    severity="fatal",
                    path="$.persistence.business_entities",
                    message="Si persistence.required=false, business_entities debe ser []",
                )
            )
        if (p.get("evidence") or []) != []:
            e.append(
                SpecValidationError(
                    code="PERSISTENCE_EVIDENCE_FORBIDDEN",
                    severity="fatal",
                    path="$.persistence.evidence",
                    message="Si persistence.required=false, evidence debe ser []",
                )
            )
        return e

    kind = p.get("kind")
    if kind not in _ALLOWED_PERSISTENCE_KINDS:
        e.append(
            SpecValidationError(
                code="PERSISTENCE_KIND_INVALID",
                severity="fatal",
                path="$.persistence.kind",
                message=f"kind inválido: {kind!r}",
            )
        )
    if bool(p.get("durable_state")) is not True:
        e.append(
            SpecValidationError(
                code="PERSISTENCE_DURABLE_REQUIRED",
                severity="fatal",
                path="$.persistence.durable_state",
                message="Si persistence.required=true, durable_state debe ser True",
            )
        )
    ev = p.get("evidence")
    if not isinstance(ev, list) or not any(isinstance(x, str) and x.strip() for x in ev):
        e.append(
            SpecValidationError(
                code="PERSISTENCE_EVIDENCE_MISSING",
                severity="fatal",
                path="$.persistence.evidence",
                message="Si persistence.required=true, evidence debe ser lista no vacía",
            )
        )

    ts = spec.get("test_strategy")
    if isinstance(ts, dict) and ts.get("requires_dependency_overrides") is not True:
        e.append(
            SpecValidationError(
                code="TEST_STRATEGY_OVERRIDES_REQUIRED",
                severity="fatal",
                path="$.test_strategy.requires_dependency_overrides",
                message="Si persistence.required=true, test_strategy.requires_dependency_overrides debe ser True",
            )
        )

    return e


def _validate_contracts(spec: Dict[str, Any]) -> List[SpecValidationError]:
    contracts = spec.get("contracts")
    if not isinstance(contracts, list):
        return [
            SpecValidationError(
                code="CONTRACTS_NOT_LIST",
                severity="fatal",
                path="$.contracts",
                message="contracts debe ser lista",
            )
        ]

    endpoints = spec.get("endpoints")
    if not isinstance(endpoints, list):
        return []

    e: List[SpecValidationError] = []
    ep_keys = {(ep.get("method"), ep.get("path")) for ep in endpoints if isinstance(ep, dict)}
    c_keys = {(c.get("method"), c.get("path")) for c in contracts if isinstance(c, dict)}

    for k in sorted(ep_keys - c_keys):
        e.append(
            SpecValidationError(
                code="CONTRACT_MISSING",
                severity="warning",
                path="$.contracts",
                message=f"Falta contrato para endpoint {k[0]} {k[1]}",
                repair_hint="add_missing_contracts",
            )
        )

    for k in sorted(c_keys - ep_keys):
        e.append(
            SpecValidationError(
                code="CONTRACT_ORPHAN",
                severity="warning",
                path="$.contracts",
                message=f"Contrato huérfano sin endpoint correspondiente {k[0]} {k[1]}",
            )
        )

    return e


def _validate_test_strategy(spec: Dict[str, Any]) -> List[SpecValidationError]:
    ts = spec.get("test_strategy")
    if not isinstance(ts, dict):
        return [
            SpecValidationError(
                code="TEST_STRATEGY_NOT_OBJECT",
                severity="fatal",
                path="$.test_strategy",
                message="test_strategy debe ser dict",
            )
        ]

    e: List[SpecValidationError] = []
    if "level" not in ts:
        e.append(
            SpecValidationError(
                code="TEST_STRATEGY_LEVEL_MISSING",
                severity="fatal",
                path="$.test_strategy.level",
                message="test_strategy.level requerido",
            )
        )

    rdo = ts.get("requires_dependency_overrides")
    if not isinstance(rdo, bool):
        e.append(
            SpecValidationError(
                code="TEST_STRATEGY_RDO_INVALID",
                severity="fatal",
                path="$.test_strategy.requires_dependency_overrides",
                message="requires_dependency_overrides debe ser bool",
            )
        )

    ot = ts.get("override_targets")
    if not isinstance(ot, list):
        e.append(
            SpecValidationError(
                code="TEST_STRATEGY_OVERRIDE_TARGETS_INVALID",
                severity="fatal",
                path="$.test_strategy.override_targets",
                message="override_targets debe ser lista",
            )
        )
    else:
        p = spec.get("persistence")
        if isinstance(p, dict) and p.get("required") is True and not ot:
            e.append(
                SpecValidationError(
                    code="OVERRIDE_TARGETS_MISSING",
                    severity="warning",
                    path="$.test_strategy.override_targets",
                    message="persistence.required=true pero override_targets vacío",
                )
            )

    notes = ts.get("notes")
    if not isinstance(notes, list):
        e.append(
            SpecValidationError(
                code="TEST_STRATEGY_NOTES_INVALID",
                severity="fatal",
                path="$.test_strategy.notes",
                message="notes debe ser lista",
            )
        )

    return e


# ======================================================================================
# Utilities
# ======================================================================================


def _require_list(
    spec: Dict[str, Any], key: str, path: str, *, fatal: bool, non_empty: bool
) -> List[SpecValidationError]:
    v = spec.get(key)
    if not isinstance(v, list):
        return [
            SpecValidationError(
                code="TYPE_ERROR",
                severity="fatal" if fatal else "warning",
                path=path,
                message=f"{key} debe ser lista",
            )
        ]
    if non_empty and not v:
        return [
            SpecValidationError(
                code="EMPTY_LIST",
                severity="fatal" if fatal else "warning",
                path=path,
                message=f"{key} no puede ser lista vacía",
            )
        ]
    return []


def _norm_path(p: str) -> str:
    return str(p).replace("\\", "/").strip()


def _dedupe_stable(items: Sequence[str]) -> List[str]:
    return list(dict.fromkeys([str(x) for x in items]).keys())


def _find_duplicates(items: Sequence[str]) -> List[str]:
    seen: Set[str] = set()
    dups: List[str] = []
    for x in items:
        if x in seen and x not in dups:
            dups.append(x)
        seen.add(x)
    return dups


def _is_valid_python_identifier(name: str) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if not name.isidentifier():
        return False
    return not keyword.iskeyword(name)
