from __future__ import annotations

"""
Constructor determinista de SPEC desde RequestIR.

Principios:
- No usa LLM.
- No interpreta texto libre (solo usa estructuras ya presentes en RequestIR).
- RequestIR es la única fuente de verdad previa al SPEC.
- SPEC mínimo, versionado, validable, con defaults seguros.

Nota:
- Este módulo NO decide vendors ni añade dependencias específicas de persistencia.
"""

from dataclasses import asdict
from typing import Any, Dict, List, Literal, Sequence, Tuple

from poc_it.generador.request_ir import ApiContractIR, PersistenceIR, RequestIR

SchemaVersion = Literal["pocit.spec.v1"]
SpecStatus = Literal["draft"]


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------


def build_spec_from_request_ir(ir: RequestIR) -> Dict[str, Any]:
    if not isinstance(ir, RequestIR):
        raise TypeError("ir debe ser RequestIR")

    assumptions: List[str] = []

    endpoints, ep_assumptions = _build_endpoints(ir)
    assumptions.extend(ep_assumptions)

    persistence_block, p_assumptions, test_strategy = _build_persistence_and_test_strategy(
        ir.persistence
    )
    assumptions.extend(p_assumptions)

    files = _build_files(endpoints)
    _assert_no_duplicates(files)

    source_from_user = _build_source_from_user(ir)
    source_from_assumptions = _build_source_from_assumptions(
        ir=ir,
        builder_assumptions=assumptions,
        endpoints=endpoints,
    )

    spec: Dict[str, Any] = {
        "schema_version": "pocit.spec.v1",
        "status": "draft",
        "entrypoint": "app.main:app",
        "run_command": "uvicorn app.main:app --reload",
        "imports_policy": "absolute_from_app",
        "files": files,
        "dependencies": _build_dependencies(ir),
        "dev_dependencies": _default_dev_dependencies(),
        "env": _build_env(ir),
        "endpoints": endpoints,
        "contracts": _build_contracts(endpoints),
        "assumptions": _dedupe_stable(assumptions),
        "open_questions": _dedupe_stable(ir.open_questions or []),
        "technology_signals": _build_technology_signals(ir),
        "integrations": _build_integrations(ir),
        "configuration": _build_configuration(ir),
        "source": {
            "from_user": source_from_user,
            "from_assumptions": source_from_assumptions,
        },
        "persistence": persistence_block,
        "test_strategy": test_strategy,
    }

    if ir.external_integrations:
        spec["external_integrations"] = list(ir.external_integrations)

    return spec


# ---------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------


def _default_dependencies() -> List[str]:
    # No hardcodear tecnologías específicas de persistencia.
    return ["fastapi", "uvicorn"]


def _build_dependencies(ir: RequestIR) -> List[str]:
    candidates: List[str] = _default_dependencies()

    for signal in ir.technology_signals or []:
        package = str(getattr(signal, "package", "") or "").strip()
        if package:
            candidates.append(package)

    for integration in ir.integrations or []:
        for package in getattr(integration, "packages", []) or []:
            normalized = str(package or "").strip()
            if normalized:
                candidates.append(normalized)

    return _dedupe_stable_case_insensitive(candidates)


def _default_dev_dependencies() -> List[str]:
    return ["pytest", "pytest-mock", "httpx"]


def _base_required_files() -> List[str]:
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


def _bundle_files_base() -> List[str]:
    return [
        "app/api/router.py",
        "app/core/config.py",
    ]


# ---------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------


def _build_endpoints(ir: RequestIR) -> Tuple[List[Dict[str, Any]], List[str]]:
    assumptions: List[str] = []

    selected, source_type = _select_contracts(ir)
    if not selected:
        selected = [_default_health_contract()]
        source_type = "builder_default"
        assumptions.append("No había contratos en RequestIR; se añadió GET /health como endpoint mínimo.")

    endpoints: List[Dict[str, Any]] = []
    for c in selected:
        ep, ep_assumptions = _contract_to_endpoint(c, source_type=source_type)
        endpoints.append(ep)
        assumptions.extend(ep_assumptions)

    endpoints = _dedupe_endpoints_by_method_path(endpoints)

    if source_type == "proposed":
        assumptions.append(
            "Los endpoints provienen de contratos propuestos (sin evidencia literal); deben confirmarse con el usuario."
        )

    return endpoints, _dedupe_stable(assumptions)


def _select_contracts(ir: RequestIR) -> Tuple[List[ApiContractIR], str]:
    if ir.explicit_api_contracts:
        return list(ir.explicit_api_contracts), "explicit"
    if ir.proposed_api_contracts:
        return list(ir.proposed_api_contracts), "proposed"
    return [], "builder_default"


def _default_health_contract() -> ApiContractIR:
    return ApiContractIR(
        method="GET",
        path="/health",
        description="",
        request_type="none",
        request_schema_hint={},
        response_example={"ok": True},
        evidence="",
        assumption="builder default /health",
    )


def _contract_to_endpoint(
    c: ApiContractIR,
    *,
    source_type: str,
) -> Tuple[Dict[str, Any], List[str]]:
    assumptions: List[str] = []

    method = str(c.method or "").upper().strip() or "GET"
    path = _normalize_path(c.path)

    file_path = _endpoint_file_from_path(path)
    func_name = _func_name_from_contract(c)

    request_block, req_assumptions = _build_request_block(c)
    assumptions.extend(req_assumptions)

    response_block, resp_assumptions = _build_response_block(c)
    assumptions.extend(resp_assumptions)

    if "{id}" in path:
        request_block = dict(request_block)
        request_block["path_params"] = _infer_path_params_from_schema_hint(c.request_schema_hint)

    bundle_files = _bundle_files_for_contract(c)
    ep_source: Dict[str, Any] = {"type": source_type}
    if source_type == "explicit":
        if isinstance(c.evidence, str) and c.evidence.strip():
            ep_source["evidence"] = c.evidence.strip()
    elif source_type == "proposed":
        if isinstance(c.assumption, str) and c.assumption.strip():
            ep_source["assumption"] = c.assumption.strip()
    elif source_type == "builder_default":
        ep_source["assumption"] = "builder default endpoint"

    errors = _merge_endpoint_errors(
        explicit_or_inferred_errors=c.errors,
        default_errors=_default_errors_for_endpoint(method=method, path=path),
    )

    endpoint: Dict[str, Any] = {
        "method": method,
        "path": path,
        "file": file_path,
        "func": func_name,
        "request": request_block,
        "response": response_block,
        "actions": _build_endpoint_actions(c),
        "integration_refs": _dedupe_stable(c.integration_refs or []),
        "errors": errors,
        "bundle_files": bundle_files,
        "source": ep_source,
    }

    return endpoint, assumptions


def _build_endpoint_actions(c: ApiContractIR) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for action in c.actions or []:
        out.append(
            {
                "id": action.id,
                "kind": action.kind,
                "description": action.description,
                "required": action.required,
                "integration_ref": action.integration_ref,
                "source": action.source,
                "evidence": action.evidence,
                "assumption": action.assumption,
            }
        )
    return out


def _build_technology_signals(ir: RequestIR) -> List[Dict[str, Any]]:
    return [asdict(item) for item in (ir.technology_signals or [])]


def _build_integrations(ir: RequestIR) -> List[Dict[str, Any]]:
    return [asdict(item) for item in (ir.integrations or [])]


def _build_configuration(ir: RequestIR) -> List[Dict[str, Any]]:
    return [asdict(item) for item in (ir.configuration or [])]


def _merge_endpoint_errors(
    *,
    explicit_or_inferred_errors: Sequence[Any],
    default_errors: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[Tuple[Any, str]] = set()

    for error in explicit_or_inferred_errors or []:
        if hasattr(error, "status_code") and hasattr(error, "code"):
            payload = {
                "status_code": error.status_code,
                "code": error.code,
                "description": getattr(error, "description", ""),
                "required": getattr(error, "required", True),
                "source": getattr(error, "source", "unknown"),
                "evidence": getattr(error, "evidence", ""),
                "assumption": getattr(error, "assumption", ""),
            }
        elif isinstance(error, dict):
            payload = {
                "status_code": error.get("status_code"),
                "code": error.get("code"),
                "description": error.get("description", ""),
                "required": error.get("required", True),
                "source": error.get("source", "unknown"),
                "evidence": error.get("evidence", ""),
                "assumption": error.get("assumption", ""),
            }
        else:
            continue

        code = str(payload.get("code") or "").strip()
        key = (payload.get("status_code"), code.lower())
        if not code or key in seen:
            continue
        seen.add(key)
        out.append(payload)

    for error in default_errors or []:
        code = str(error.get("code") or "").strip()
        key = (error.get("status_code"), code.lower())
        if not code or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "status_code": error.get("status_code"),
                "code": code,
                "description": error.get("description", ""),
                "required": error.get("required", False),
                "source": error.get("source", "default"),
                "evidence": error.get("evidence", ""),
                "assumption": error.get("assumption", "Default técnico del SPEC builder"),
            }
        )

    return out


def _bundle_files_for_contract(_c: ApiContractIR) -> List[str]:
    return list(_bundle_files_base())


def _endpoint_file_from_path(path: str) -> str:
    seg = _first_path_segment(path)
    seg = seg or "root"
    seg = seg.replace("{", "").replace("}", "")
    seg = "".join(ch for ch in seg if ch.isalnum() or ch in ("_", "-")).replace("-", "_") or "root"
    return f"app/api/endpoints/{seg}.py"


def _first_path_segment(path: str) -> str:
    p = _normalize_path(path)
    if p == "/":
        return "root"
    s = p.lstrip("/").split("?", 1)[0]
    return s.split("/", 1)[0].strip()


def _normalize_path(path: str) -> str:
    p = str(path or "").strip()
    if not p:
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    return p


def _func_name_from_contract(c: ApiContractIR) -> str:
    base_seg = _first_path_segment(c.path) or "root"
    base_seg = base_seg.replace("{", "").replace("}", "")
    base_seg = "".join(ch for ch in base_seg if ch.isalnum() or ch in ("_", "-")).replace("-", "_") or "root"

    path = _normalize_path(c.path)
    has_id = "{id}" in path
    singular = _singularize_es(base_seg)
    plural = base_seg

    m = (c.method or "GET").upper().strip()

    if m == "GET" and not has_id:
        return f"list_{plural}"
    if m == "GET" and has_id:
        return f"get_{singular}"
    if m == "POST":
        return f"create_{singular}" if singular else f"create_{plural}"
    if m == "PUT":
        return f"replace_{singular}" if has_id else f"replace_{plural}"
    if m == "PATCH":
        return f"update_{singular}" if has_id else f"update_{plural}"
    if m == "DELETE":
        return f"delete_{singular}" if has_id else f"delete_{plural}"
    return plural


def _build_request_block(c: ApiContractIR) -> Tuple[Dict[str, Any], List[str]]:
    assumptions: List[str] = []

    path = _normalize_path(c.path)
    has_id = "{id}" in path

    rt = (c.request_type or "none").strip()
    if rt not in ("json", "multipart", "query", "none"):
        rt = "none"

    method = (c.method or "GET").upper().strip()
    if has_id and method in ("GET", "DELETE") and rt == "query":
        rt = "none"

    if rt == "json":
        schema = dict(c.request_schema_hint or {})
        if not schema:
            assumptions.append(f"Request schema vacío para {c.method} {c.path}; se mantiene {{}} por defecto.")
        return {"type": "json", "schema": schema}, assumptions

    if rt == "multipart":
        return {"type": "multipart"}, assumptions
    if rt == "query":
        return {"type": "query"}, assumptions
    return {"type": "none"}, assumptions


def _build_response_block(c: ApiContractIR) -> Tuple[Dict[str, Any], List[str]]:
    assumptions: List[str] = []

    ex = c.response_example

    if isinstance(ex, dict):
        if ex:
            return {"json_example": ex}, assumptions
        assumptions.append(
            f"Response example vacío (dict) para {c.method} {c.path}; se usó {{\"ok\": true}} por defecto."
        )
        return {"json_example": {"ok": True}}, assumptions

    if isinstance(ex, list):
        if ex:
            return {"json_example": ex}, assumptions
        assumptions.append(
            f"Response example vacío (list) para {c.method} {c.path}; se preservó [] (debe confirmarse el ejemplo real)."
        )
        return {"json_example": []}, assumptions

    assumptions.append(f"Response example ausente para {c.method} {c.path}; se usó {{\"ok\": true}} por defecto.")
    return {"json_example": {"ok": True}}, assumptions


def _dedupe_endpoints_by_method_path(endpoints: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Dict[Tuple[str, str], int] = {}
    for ep in endpoints:
        k = (
            str(ep.get("method") or "").upper().strip(),
            str(ep.get("path") or "").strip(),
        )
        if k in seen:
            out[seen[k]] = ep
        else:
            seen[k] = len(out)
            out.append(ep)
    return out


# ---------------------------------------------------------------------
# Persistence / test strategy
# ---------------------------------------------------------------------


def _build_persistence_and_test_strategy(
    p: PersistenceIR,
) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    assumptions: List[str] = []

    persistence_block: Dict[str, Any] = {
        "required": bool(p.required),
        "kind": p.kind,
        "durable_state": bool(p.durable_state),
        "business_entities": list(p.business_entities or []),
        "evidence": list(p.evidence or []),
        "uncertainty": str(p.uncertainty or ""),
    }

    test_strategy: Dict[str, Any] = {
        "level": "openapi_contract",
        "requires_dependency_overrides": False,
        "override_targets": [],
        "notes": [],
    }

    if not p.required:
        persistence_block["kind"] = None
        persistence_block["durable_state"] = False
        persistence_block["business_entities"] = []
        persistence_block["evidence"] = []
        persistence_block["uncertainty"] = ""
        return persistence_block, assumptions, test_strategy

    test_strategy["requires_dependency_overrides"] = True
    test_strategy["notes"] = [
        "Se requiere persistencia; los puertos (DB/colas/etc.) se resolverán en fases posteriores mediante overrides/fakes."
    ]

    if persistence_block.get("kind") == "unknown":
        assumptions.append(
            "Persistencia requerida pero kind='unknown'; debe especificarse el tipo de almacenamiento en fases posteriores."
        )

    return persistence_block, assumptions, test_strategy


def _build_env(ir: RequestIR) -> List[Dict[str, Any]]:
    env: List[Dict[str, Any]] = []

    for item in ir.configuration or []:
        delivery = str(getattr(item, "delivery", "env") or "env").strip().lower() or "env"
        if delivery != "env":
            continue
        env.append(
            {
                "name": item.key,
                "purpose": item.purpose,
                "required": item.required,
                "secret": item.secret,
                "source": item.source,
                "evidence": item.evidence,
                "assumption": item.assumption,
            }
        )

    return _dedupe_env_by_name(env)


# ---------------------------------------------------------------------
# Contracts (ligeros)
# ---------------------------------------------------------------------


def _build_contracts(endpoints: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ep in endpoints:
        out.append(
            {
                "method": ep.get("method"),
                "path": ep.get("path"),
                "request_type": (ep.get("request") or {}).get("type"),
                "file": ep.get("file"),
                "source_type": (ep.get("source") or {}).get("type"),
            }
        )
    return out


# ---------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------


def _build_files(endpoints: Sequence[Dict[str, Any]]) -> List[str]:
    required = _base_required_files()

    endpoint_files: List[str] = []
    bundle_files: List[str] = []

    for ep in endpoints:
        f = str(ep.get("file") or "").replace("\\", "/")
        if f:
            endpoint_files.append(f)
        for bf in ep.get("bundle_files", []) or []:
            bundle_files.append(str(bf).replace("\\", "/"))

    files = []
    files.extend(required)
    files.extend(sorted(set(endpoint_files)))
    files.extend(sorted(set(bundle_files)))
    files = list(dict.fromkeys([p.replace("\\", "/") for p in files]).keys())
    return files


def _assert_no_duplicates(items: Sequence[str]) -> None:
    seen = set()
    for x in items:
        if x in seen:
            raise AssertionError(f"duplicated file in SPEC.files: {x}")
        seen.add(x)


# ---------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------


def _build_source_from_user(ir: RequestIR) -> List[str]:
    out: List[str] = []

    for c in ir.explicit_api_contracts or []:
        if isinstance(c.evidence, str) and c.evidence.strip():
            out.append(c.evidence.strip())
        for action in c.actions or []:
            if getattr(action, "source", "") == "explicit" and isinstance(getattr(action, "evidence", ""), str) and action.evidence.strip():
                out.append(action.evidence.strip())
        for error in c.errors or []:
            if getattr(error, "source", "") == "explicit" and isinstance(getattr(error, "evidence", ""), str) and error.evidence.strip():
                out.append(error.evidence.strip())

    for tech in ir.technology_signals or []:
        if isinstance(getattr(tech, "evidence", ""), str) and tech.evidence.strip():
            out.append(tech.evidence.strip())

    for integration in ir.integrations or []:
        if getattr(integration, "source", "") == "explicit" and isinstance(getattr(integration, "evidence", ""), str) and integration.evidence.strip():
            out.append(integration.evidence.strip())
        auth = getattr(integration, "authentication", None)
        if auth is not None and getattr(auth, "source", "") == "explicit" and isinstance(getattr(auth, "evidence", ""), str) and auth.evidence.strip():
            out.append(auth.evidence.strip())

    for config in ir.configuration or []:
        if getattr(config, "source", "") == "explicit" and isinstance(getattr(config, "evidence", ""), str) and config.evidence.strip():
            out.append(config.evidence.strip())

    for ev in (ir.persistence.evidence or []):
        if isinstance(ev, str) and ev.strip():
            out.append(ev.strip())

    for f in (ir.user_facts or []):
        if isinstance(f, str) and f.strip():
            out.append(f.strip())

    return _dedupe_stable(out)


def _build_source_from_assumptions(
    *,
    ir: RequestIR,
    builder_assumptions: Sequence[str],
    endpoints: Sequence[Dict[str, Any]],
) -> List[str]:
    out: List[str] = []

    out.extend([a for a in (ir.assumptions or []) if isinstance(a, str) and a.strip()])
    out.extend([a for a in (builder_assumptions or []) if isinstance(a, str) and a.strip()])
    out.extend([q for q in (ir.open_questions or []) if isinstance(q, str) and q.strip()])

    for c in list(ir.explicit_api_contracts or []) + list(ir.proposed_api_contracts or []):
        if isinstance(c.assumption, str) and c.assumption.strip():
            out.append(c.assumption.strip())
        for action in c.actions or []:
            if isinstance(getattr(action, "assumption", ""), str) and action.assumption.strip():
                out.append(action.assumption.strip())
        for error in c.errors or []:
            if isinstance(getattr(error, "assumption", ""), str) and error.assumption.strip():
                out.append(error.assumption.strip())

    for integration in ir.integrations or []:
        if isinstance(getattr(integration, "assumption", ""), str) and integration.assumption.strip():
            out.append(integration.assumption.strip())
        auth = getattr(integration, "authentication", None)
        if auth is not None and isinstance(getattr(auth, "assumption", ""), str) and auth.assumption.strip():
            out.append(auth.assumption.strip())

    for config in ir.configuration or []:
        if isinstance(getattr(config, "assumption", ""), str) and config.assumption.strip():
            out.append(config.assumption.strip())

    if any((ep.get("source") or {}).get("type") == "builder_default" for ep in endpoints or []):
        out.append("Se añadió endpoint por defecto (builder_default) para garantizar arrancabilidad.")

    return _dedupe_stable(out)


def _infer_path_params_from_schema_hint(schema_hint: Any) -> Dict[str, str]:
    if isinstance(schema_hint, dict):
        for key in ("path_params", "pathParams", "params"):
            v = schema_hint.get(key)
            if isinstance(v, dict) and "id" in v and isinstance(v.get("id"), str) and v.get("id").strip():
                return {"id": v.get("id").strip()}
        if "id" in schema_hint and isinstance(schema_hint.get("id"), str) and schema_hint.get("id").strip():
            return {"id": schema_hint.get("id").strip()}
    return {"id": "string"}


def _default_errors_for_endpoint(*, method: str, path: str) -> List[Dict[str, Any]]:
    if "{id}" in path and method in ("GET", "PUT", "PATCH", "DELETE"):
        return [
            {
                "status_code": 404,
                "code": "not_found",
                "description": "",
                "required": False,
                "source": "default",
                "evidence": "",
                "assumption": "Default técnico del SPEC builder",
            }
        ]
    return []


def _singularize_es(plural: str) -> str:
    s = (plural or "").strip()
    if s.endswith("es") and len(s) > 2:
        return s[:-2]
    if s.endswith("s") and len(s) > 1:
        return s[:-1]
    return s


def _dedupe_stable(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _dedupe_stable_case_insensitive(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items:
        s = str(x).strip()
        key = s.lower()
        if not s or key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _dedupe_env_by_name(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            out[seen[key]] = item
        else:
            seen[key] = len(out)
            out.append(item)
    return out
