from __future__ import annotations

import copy
import json
import keyword
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Set, Tuple

SpecErrorSeverity = Literal["fatal", "warning"]


@dataclass(frozen=True)
class SpecValidationError:
    code: str
    severity: SpecErrorSeverity
    path: str
    message: str
    repair_hint: str | None = None


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
_ALLOWED_IMPLEMENTATION_LEVELS = {
    "fully_local",
    "integration_skeleton",
    "mocked",
    "documentation_only",
}
_REQUIRED_DEPS = {"fastapi", "uvicorn"}
_REQUIRED_DEV_DEPS = {"pytest", "httpx"}
_FORBIDDEN_CONFIGURATION_VALUE_FIELDS = {"value", "default_secret", "credential", "token", "password"}


def validate_spec(spec: dict) -> List[SpecValidationError]:
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
    errors.extend(_validate_dependencies(spec))
    errors.extend(_validate_persistence(spec))
    errors.extend(_validate_contracts(spec))
    errors.extend(_validate_integrations(spec))
    errors.extend(_validate_configuration(spec))
    errors.extend(_validate_endpoints(spec))
    errors.extend(_validate_test_strategy(spec))
    return errors


def validar_spec(spec: dict) -> Tuple[bool, List[str]]:
    errs = validate_spec(spec)
    ok = not any(e.severity == "fatal" for e in errs)
    msgs = [f"{e.severity.upper()} {e.code} @ {e.path}: {e.message}" for e in errs]
    return ok, msgs


def repair_spec_deterministic(spec: dict) -> Tuple[dict, List[SpecValidationError]]:
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

            bundle_files = ep.get("bundle_files")
            if isinstance(bundle_files, list):
                for bf in bundle_files:
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

    for dep_key in ("dependencies", "dev_dependencies"):
        deps = repaired.get(dep_key)
        if isinstance(deps, list):
            cleaned = [str(x).strip() for x in deps if isinstance(x, str) and str(x).strip()]
            repaired[dep_key] = _dedupe_stable(cleaned)

    contracts = repaired.get("contracts")
    if isinstance(repaired.get("endpoints"), list):
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

    if not isinstance(repaired.get("integrations"), list):
        repaired["integrations"] = []
        changes.append(
            SpecValidationError(
                code="REPAIR_ADD_INTEGRATIONS_LIST",
                severity="warning",
                path="$.integrations",
                message="Añadida lista vacía integrations por compatibilidad.",
                repair_hint="add_empty_integrations",
            )
        )

    if not isinstance(repaired.get("configuration"), list):
        repaired["configuration"] = []
        changes.append(
            SpecValidationError(
                code="REPAIR_ADD_CONFIGURATION_LIST",
                severity="warning",
                path="$.configuration",
                message="Añadida lista vacía configuration por compatibilidad.",
                repair_hint="add_empty_configuration",
            )
        )

    if isinstance(repaired.get("endpoints"), list):
        for i, ep in enumerate(repaired.get("endpoints") or []):
            if not isinstance(ep, dict):
                continue

            method = str(ep.get("method") or "").upper().strip()
            path = str(ep.get("path") or "").strip()

            if "actions" not in ep or ep.get("actions") is None:
                ep["actions"] = []
                changes.append(
                    SpecValidationError(
                        code="REPAIR_ADD_ACTIONS_LIST",
                        severity="warning",
                        path=f"$.endpoints[{i}].actions",
                        message="Añadida lista vacía actions por compatibilidad.",
                        repair_hint="add_default_actions",
                    )
                )

            if "integration_refs" not in ep or ep.get("integration_refs") is None:
                ep["integration_refs"] = []
                changes.append(
                    SpecValidationError(
                        code="REPAIR_ADD_INTEGRATION_REFS",
                        severity="warning",
                        path=f"$.endpoints[{i}].integration_refs",
                        message="Añadida lista vacía integration_refs por compatibilidad.",
                        repair_hint="add_default_integration_refs",
                    )
                )

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

            errs = ep.get("errors")
            if errs is None:
                ep["errors"] = []
                errs = ep["errors"]
                changes.append(
                    SpecValidationError(
                        code="REPAIR_ADD_ERRORS_LIST",
                        severity="warning",
                        path=f"$.endpoints[{i}].errors",
                        message="Añadida lista vacía errors por compatibilidad.",
                        repair_hint="add_default_errors",
                    )
                )

            if "{id}" in path and method in ("GET", "PUT", "PATCH", "DELETE"):
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


def _extraer_json_tolerante(respuesta: str) -> Optional[dict]:
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

    e.extend(_require_list(spec, "files", "$.files", fatal=True, non_empty=True))
    e.extend(_require_list(spec, "dependencies", "$.dependencies", fatal=True, non_empty=False))
    e.extend(_require_list(spec, "dev_dependencies", "$.dev_dependencies", fatal=True, non_empty=False))
    e.extend(_validate_env(spec))
    e.extend(_require_list(spec, "endpoints", "$.endpoints", fatal=True, non_empty=False))

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
            bundle_files = ep.get("bundle_files")
            if isinstance(bundle_files, list):
                for j, bf in enumerate(bundle_files):
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


def _validate_integrations(spec: Dict[str, Any]) -> List[SpecValidationError]:
    integrations = spec.get("integrations")
    if not isinstance(integrations, list):
        return [
            SpecValidationError(
                code="SPEC_INTEGRATIONS_NOT_LIST",
                severity="fatal",
                path="$.integrations",
                message="integrations debe ser lista",
            )
        ]

    e: List[SpecValidationError] = []
    seen_ids: Set[str] = set()
    technology_ids = _technology_signal_ids(spec)
    configuration_keys = _configuration_keys(spec)

    for i, item in enumerate(integrations):
        if not isinstance(item, dict):
            e.append(
                SpecValidationError(
                    code="SPEC_INTEGRATION_ID_REQUIRED",
                    severity="fatal",
                    path=f"$.integrations[{i}]",
                    message="Cada integración debe ser objeto con id y name.",
                )
            )
            continue

        integration_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not integration_id:
            e.append(
                SpecValidationError(
                    code="SPEC_INTEGRATION_ID_REQUIRED",
                    severity="fatal",
                    path=f"$.integrations[{i}].id",
                    message="integration.id es requerido.",
                )
            )
        if not name:
            e.append(
                SpecValidationError(
                    code="SPEC_INTEGRATION_ID_REQUIRED",
                    severity="fatal",
                    path=f"$.integrations[{i}].name",
                    message="integration.name es requerido.",
                )
            )

        key = integration_id.lower()
        if integration_id:
            if key in seen_ids:
                e.append(
                    SpecValidationError(
                        code="SPEC_INTEGRATION_DUPLICATED",
                        severity="fatal",
                        path=f"$.integrations[{i}].id",
                        message=f"Integración duplicada: {integration_id}",
                    )
                )
            else:
                seen_ids.add(key)

        level = str(item.get("implementation_level") or "").strip().lower()
        if level not in _ALLOWED_IMPLEMENTATION_LEVELS:
            e.append(
                SpecValidationError(
                    code="SPEC_INTEGRATION_INVALID_LEVEL",
                    severity="fatal",
                    path=f"$.integrations[{i}].implementation_level",
                    message=f"implementation_level inválido: {item.get('implementation_level')!r}",
                )
            )

        source = str(item.get("source") or "").strip().lower()
        evidence = str(item.get("evidence") or "").strip()
        if source == "explicit" and not evidence:
            e.append(
                SpecValidationError(
                    code="SPEC_EXPLICIT_EVIDENCE_REQUIRED",
                    severity="fatal",
                    path=f"$.integrations[{i}].evidence",
                    message="Una integración explícita requiere evidence.",
                )
            )

        auth = item.get("authentication")
        if isinstance(auth, dict):
            auth_source = str(auth.get("source") or "").strip().lower()
            auth_evidence = str(auth.get("evidence") or "").strip()
            if auth_source == "explicit" and not auth_evidence:
                e.append(
                    SpecValidationError(
                        code="SPEC_EXPLICIT_EVIDENCE_REQUIRED",
                        severity="fatal",
                        path=f"$.integrations[{i}].authentication.evidence",
                        message="authentication explícito requiere evidence.",
                    )
                )

        for j, ref in enumerate(item.get("technology_refs") or []):
            normalized_ref = str(ref or "").strip()
            if not normalized_ref:
                continue
            if normalized_ref not in technology_ids:
                e.append(
                    SpecValidationError(
                        code="SPEC_INTEGRATION_REF_UNKNOWN",
                        severity="fatal",
                        path=f"$.integrations[{i}].technology_refs[{j}]",
                        message=f"technology_ref inexistente: {normalized_ref}",
                    )
                )

        for j, ref in enumerate(item.get("configuration_refs") or []):
            if not isinstance(ref, str) or not ref.strip():
                continue
            if ref.strip().lower() not in configuration_keys:
                e.append(
                    SpecValidationError(
                        code="SPEC_INTEGRATION_REF_UNKNOWN",
                        severity="fatal",
                        path=f"$.integrations[{i}].configuration_refs[{j}]",
                        message=f"configuration_ref inexistente: {ref}",
                    )
                )

    return e


def _validate_configuration(spec: Dict[str, Any]) -> List[SpecValidationError]:
    configuration = spec.get("configuration")
    if not isinstance(configuration, list):
        return [
            SpecValidationError(
                code="SPEC_CONFIGURATION_NOT_LIST",
                severity="fatal",
                path="$.configuration",
                message="configuration debe ser lista",
            )
        ]

    e: List[SpecValidationError] = []
    seen_keys: Set[str] = set()

    for i, item in enumerate(configuration):
        if not isinstance(item, dict):
            e.append(
                SpecValidationError(
                    code="SPEC_CONFIGURATION_KEY_REQUIRED",
                    severity="fatal",
                    path=f"$.configuration[{i}]",
                    message="Cada configuration debe ser objeto con key.",
                )
            )
            continue

        key = str(item.get("key") or "").strip()
        if not key:
            e.append(
                SpecValidationError(
                    code="SPEC_CONFIGURATION_KEY_REQUIRED",
                    severity="fatal",
                    path=f"$.configuration[{i}].key",
                    message="configuration.key es requerido.",
                )
            )
        else:
            key_norm = key.lower()
            if key_norm in seen_keys:
                e.append(
                    SpecValidationError(
                        code="SPEC_CONFIGURATION_DUPLICATED",
                        severity="fatal",
                        path=f"$.configuration[{i}].key",
                        message=f"configuration duplicada: {key}",
                    )
                )
            else:
                seen_keys.add(key_norm)

        source = str(item.get("source") or "").strip().lower()
        evidence = str(item.get("evidence") or "").strip()
        if source == "explicit" and not evidence:
            e.append(
                SpecValidationError(
                    code="SPEC_EXPLICIT_EVIDENCE_REQUIRED",
                    severity="fatal",
                    path=f"$.configuration[{i}].evidence",
                    message="configuration explícita requiere evidence.",
                )
            )

        for forbidden in _FORBIDDEN_CONFIGURATION_VALUE_FIELDS:
            if forbidden in item and item.get(forbidden) not in (None, ""):
                e.append(
                    SpecValidationError(
                        code="SPEC_CONFIGURATION_EMBEDS_VALUE",
                        severity="fatal",
                        path=f"$.configuration[{i}].{forbidden}",
                        message=f"configuration no debe embeder valores reales en el campo {forbidden}.",
                    )
                )

    return e


def _validate_endpoints(spec: Dict[str, Any]) -> List[SpecValidationError]:
    e: List[SpecValidationError] = []
    endpoints = spec.get("endpoints")
    if not isinstance(endpoints, list):
        return e

    seen: Set[Tuple[str, str]] = set()
    integration_ids = _integration_ids(spec)

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

        actions = ep.get("actions")
        if not isinstance(actions, list):
            e.append(
                SpecValidationError(
                    code="ENDPOINT_ACTIONS_INVALID",
                    severity="fatal",
                    path=f"$.endpoints[{i}].actions",
                    message="actions debe ser lista",
                )
            )
        else:
            e.extend(_validate_actions_block(actions, endpoint=ep, idx=i, integration_ids=integration_ids))

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

        integration_refs = ep.get("integration_refs")
        if not isinstance(integration_refs, list):
            e.append(
                SpecValidationError(
                    code="ENDPOINT_INTEGRATION_REFS_INVALID",
                    severity="fatal",
                    path=f"$.endpoints[{i}].integration_refs",
                    message="integration_refs debe ser lista",
                )
            )
        else:
            for j, ref in enumerate(integration_refs):
                if not isinstance(ref, str) or not ref.strip():
                    continue
                if ref.strip().lower() not in integration_ids:
                    e.append(
                        SpecValidationError(
                            code="SPEC_INTEGRATION_REF_UNKNOWN",
                            severity="fatal",
                            path=f"$.endpoints[{i}].integration_refs[{j}]",
                            message=f"Integration ref desconocida: {ref}",
                        )
                    )

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


def _validate_actions_block(
    actions: List[Any],
    *,
    endpoint: Dict[str, Any],
    idx: int,
    integration_ids: Set[str],
) -> List[SpecValidationError]:
    e: List[SpecValidationError] = []
    for j, item in enumerate(actions):
        if not isinstance(item, dict):
            e.append(
                SpecValidationError(
                    code="SPEC_ACTION_ID_REQUIRED",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].actions[{j}]",
                    message="Cada action debe ser objeto con id y description.",
                )
            )
            continue

        action_id = str(item.get("id") or "").strip()
        description = str(item.get("description") or "").strip()
        if not action_id:
            e.append(
                SpecValidationError(
                    code="SPEC_ACTION_ID_REQUIRED",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].actions[{j}].id",
                    message="action.id es requerido.",
                )
            )
        if not description:
            e.append(
                SpecValidationError(
                    code="SPEC_ACTION_ID_REQUIRED",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].actions[{j}].description",
                    message="action.description es requerida.",
                )
            )

        source = str(item.get("source") or "").strip().lower()
        evidence = str(item.get("evidence") or "").strip()
        if source == "explicit" and not evidence:
            e.append(
                SpecValidationError(
                    code="SPEC_EXPLICIT_EVIDENCE_REQUIRED",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].actions[{j}].evidence",
                    message="Una action explícita requiere evidence.",
                )
            )

        kind = str(item.get("kind") or "").strip().lower()
        integration_ref = str(item.get("integration_ref") or "").strip()
        if kind == "external_call" and not integration_ref:
            e.append(
                SpecValidationError(
                    code="SPEC_EXTERNAL_ACTION_WITHOUT_INTEGRATION",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].actions[{j}].integration_ref",
                    message="Toda action external_call requiere integration_ref.",
                )
            )
        if integration_ref and integration_ref.lower() not in integration_ids:
            e.append(
                SpecValidationError(
                    code="SPEC_ACTION_INTEGRATION_UNKNOWN",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].actions[{j}].integration_ref",
                    message=f"Integration desconocida para action: {integration_ref}",
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
    seen_error_keys: Set[Tuple[int | None, str]] = set()

    for j, it in enumerate(errs):
        if isinstance(it, int):
            if it == 404:
                has_404 = True
            continue

        if not isinstance(it, dict):
            e.append(
                SpecValidationError(
                    code="ERRORS_ITEM_INVALID",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].errors[{j}]",
                    message="Cada error debe ser int o dict",
                )
            )
            continue

        status_code = _normalize_status_code(it.get("status_code"))
        if status_code == 404:
            has_404 = True

        code = str(it.get("code") or "").strip()
        if not code:
            e.append(
                SpecValidationError(
                    code="SPEC_ERROR_CODE_REQUIRED",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].errors[{j}].code",
                    message="error.code es requerido.",
                )
            )
        else:
            error_key = (status_code, code.lower())
            if error_key in seen_error_keys:
                e.append(
                    SpecValidationError(
                        code="SPEC_ERROR_DUPLICATED",
                        severity="fatal",
                        path=f"$.endpoints[{idx}].errors[{j}]",
                        message=f"Error duplicado por (status_code, code): {status_code} {code}",
                    )
                )
            else:
                seen_error_keys.add(error_key)

        raw_status = it.get("status_code")
        if raw_status is not None:
            if status_code is None or not (100 <= status_code <= 599):
                e.append(
                    SpecValidationError(
                        code="SPEC_ERROR_STATUS_INVALID",
                        severity="fatal",
                        path=f"$.endpoints[{idx}].errors[{j}].status_code",
                        message=f"status_code inválido: {raw_status!r}",
                    )
                )

        source = str(it.get("source") or "").strip().lower()
        evidence = str(it.get("evidence") or "").strip()
        if source == "explicit" and not evidence:
            e.append(
                SpecValidationError(
                    code="SPEC_EXPLICIT_EVIDENCE_REQUIRED",
                    severity="fatal",
                    path=f"$.endpoints[{idx}].errors[{j}].evidence",
                    message="Un error explícito requiere evidence.",
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


def _normalize_status_code(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _integration_ids(spec: Dict[str, Any]) -> Set[str]:
    integrations = spec.get("integrations")
    if not isinstance(integrations, list):
        return set()
    out: Set[str] = set()
    for item in integrations:
        if not isinstance(item, dict):
            continue
        integration_id = str(item.get("id") or "").strip()
        if integration_id:
            out.add(integration_id.lower())
    return out


def _technology_signal_ids(spec: Dict[str, Any]) -> Set[str]:
    items = spec.get("technology_signals")
    if not isinstance(items, list):
        return set()
    out: Set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        technology_id = str(item.get("id") or "").strip()
        if technology_id:
            out.add(technology_id)
    return out


def _configuration_keys(spec: Dict[str, Any]) -> Set[str]:
    items = spec.get("configuration")
    if not isinstance(items, list):
        return set()
    out: Set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key:
            out.add(key.lower())
    return out
