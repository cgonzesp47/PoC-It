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

from poc_it.generador.path_utils import extract_path_param_names, has_path_param
from poc_it.generador.request_ir import (
    ApiContractIR,
    PersistenceIR,
    RequestIR,
    TechnologySignalIR,
)

SchemaVersion = Literal["pocit.spec.v1"]
SpecStatus = Literal["draft"]


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------


def build_spec_from_request_ir(ir: RequestIR) -> Dict[str, Any]:
    if not isinstance(ir, RequestIR):
        raise TypeError("ir debe ser RequestIR")

    # `technology_signals` = señales detectadas del usuario + señales baseline que PoC-it
    # introduce por su propia arquitectura de generación (p.ej. app/core/config.py siempre usa
    # pydantic-settings). Ambas se tratan de forma UNIFORME a partir de aquí: cualquier import
    # que dependa de un TechnologySignal debe poder trazarse hasta su(s) `packages`, sin importar
    # si la señal vino del usuario o de PoC-it mismo.
    technology_signals = _merge_technology_signals(
        ir.technology_signals or [], _baseline_technology_signals()
    )

    package_errors = validate_technology_packages(technology_signals)
    if package_errors:
        raise ValueError("; ".join(package_errors))

    assumptions: List[str] = []
    assumptions.append(
        "app/core/config.py usa pydantic-settings (BaseSettings) por patrón de configuración "
        "base de PoC-it; se añadió como TechnologySignal baseline (id=pydantic-settings) para "
        "que su dependencia quede declarada en requirements.txt."
    )

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
        "dependencies": _build_dependencies(technology_signals, ir.integrations),
        "dev_dependencies": _default_dev_dependencies(),
        "env": _build_env(ir),
        "endpoints": endpoints,
        "contracts": _build_contracts(endpoints),
        "assumptions": _dedupe_stable(assumptions),
        "open_questions": _dedupe_stable(ir.open_questions or []),
        "technology_signals": _build_technology_signals(technology_signals),
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
    # No hardcodear tecnologías específicas de persistencia. fastapi/uvicorn siguen siendo la
    # base fija de todo proyecto generado (entrypoint=app.main:app, run_command=uvicorn ...),
    # pero su nombre instalable se deriva de `_baseline_technology_signals().packages` para que
    # también queden trazadas como cualquier otro TechnologySignal (ver
    # `_validate_dependency_imports_declared` en file_contracts_validation.py).
    dependencies: List[str] = []
    _extend_packages(
        dependencies,
        [pkg for signal in _baseline_technology_signals() for pkg in signal.packages],
    )
    return dependencies


def _baseline_technology_signals() -> List[TechnologySignalIR]:
    """Tecnologías que PoC-it introduce por su propio patrón de generación, no por detección
    del usuario.

    Estas señales existen para que archivos/import que PoC-it decide generar por diseño propio
    (el framework fastapi/uvicorn en sí, o `app/core/config.py` usando
    `pydantic_settings.BaseSettings`, ver `file_contracts.py` kind="config") queden trazados
    igual que cualquier otra tecnología:

        import requerido/permitido -> TechnologySignal que lo provee -> packages -> requirements.txt

    en vez de que el import quede "flotando" en `allowed_imports` sin ningún TechnologySignal ni
    dependencia declarada que lo respalde (la causa raíz de bugs como
    `ModuleNotFoundError: No module named 'pydantic_settings'`).

    IMPORTANTE: esto NO es una tabla de mapeo módulo->paquete para reparar errores en runtime.
    Es la declaración, en un único sitio, de una decisión arquitectónica que PoC-it ya toma
    determinísticamente (todo proyecto generado usa este patrón de configuración). El paquete
    instalable sigue siendo siempre `packages`, nunca inferido de `id`/`name`/`import_root`.
    """
    return [
        # El framework/runtime que PoC-it siempre genera (ver `_default_dependencies`,
        # `entrypoint`/`run_command` fijos a app.main:app / uvicorn). Sin esta señal, cualquier
        # `allowed_imports` que incluya "fastapi" o "uvicorn" quedaría sin TechnologySignal que
        # lo cubra, disparando DEPENDENCY_IMPORT_NOT_DECLARED en todo proyecto generado.
        TechnologySignalIR(
            id="fastapi",
            name="fastapi",
            category="framework",
            packages=("fastapi",),
            import_roots=("fastapi",),
            role="web framework",
            evidence="",
            confidence="explicit",
        ),
        TechnologySignalIR(
            id="uvicorn",
            name="uvicorn",
            category="runtime",
            packages=("uvicorn",),
            import_roots=("uvicorn",),
            role="asgi server",
            evidence="",
            confidence="explicit",
        ),
        TechnologySignalIR(
            id="pydantic-settings",
            name="pydantic-settings",
            category="library",
            packages=("pydantic-settings",),
            import_roots=("pydantic_settings",),
            role="config",
            evidence="",
            confidence="explicit",
        ),
        # Endpoints/schemas generados por PoC-it usan `pydantic.BaseModel` (allowed_imports para
        # kind="endpoint" incluye "pydantic"). Es una dependencia transitiva de fastapi, pero se
        # declara explícitamente aquí para que quede trazada igual que cualquier otro import
        # externo, en vez de depender implícitamente de lo que fastapi arrastre.
        TechnologySignalIR(
            id="pydantic",
            name="pydantic",
            category="library",
            packages=("pydantic",),
            import_roots=("pydantic",),
            role="data validation",
            evidence="",
            confidence="explicit",
        ),
    ]


def _merge_technology_signals(
    user_signals: Sequence[TechnologySignalIR],
    baseline_signals: Sequence[TechnologySignalIR],
) -> List[TechnologySignalIR]:
    """Combina señales del usuario con señales baseline de PoC-it, sin duplicar por id.

    Si el usuario/contexto ya declaró una señal con el mismo id (p.ej. el propio usuario mencionó
    pydantic-settings explícitamente), esa señal del usuario tiene prioridad y la baseline no se
    añade de nuevo.
    """
    merged: List[TechnologySignalIR] = list(user_signals or [])
    existing_ids = {str(signal.id).strip() for signal in merged if str(signal.id or "").strip()}

    for signal in baseline_signals or []:
        signal_id = str(signal.id).strip()
        if signal_id and signal_id in existing_ids:
            continue
        merged.append(signal)
        if signal_id:
            existing_ids.add(signal_id)

    return merged


def _normalize_identity(value: str) -> str:
    return str(value or "").strip().casefold()


def _dedupe_stable_casefold(values: List[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []

    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue

        identity = value.casefold()
        if identity in seen:
            continue

        seen.add(identity)
        result.append(value)

    return result


def validate_technology_packages(signals: List[Any]) -> List[str]:
    errors: List[str] = []

    for signal in signals:
        for package in getattr(signal, "packages", []) or []:
            if not isinstance(package, str) or not package.strip():
                errors.append(
                    "TechnologySignal "
                    f"{getattr(signal, 'name', '')!r} "
                    "contiene package vacío."
                )

        for import_root in getattr(signal, "import_roots", []) or []:
            if not isinstance(import_root, str) or not import_root.strip():
                errors.append(
                    "TechnologySignal "
                    f"{getattr(signal, 'name', '')!r} "
                    "contiene import_root vacío."
                )

    return errors


def _extend_packages(target: List[str], packages: Any) -> None:
    if not packages:
        return

    if isinstance(packages, (str, bytes)):
        values = [packages]
    else:
        values = packages

    for raw in values:
        package = str(raw or "").strip()
        if package:
            target.append(package)


def _build_dependencies(
    technology_signals: Sequence[TechnologySignalIR],
    integrations: Sequence[Any],
) -> List[str]:
    dependencies = list(_default_dependencies())

    signals_by_id = {
        str(signal.id).strip(): signal
        for signal in technology_signals
        if str(signal.id or "").strip()
    }

    for signal in technology_signals:
        confidence = str(signal.confidence or "").strip().casefold()
        if confidence != "explicit":
            continue
        _extend_packages(dependencies, signal.packages)

    for integration in integrations or []:
        for raw_ref in (integration.technology_refs or ()):
            technology_id = str(raw_ref or "").strip()
            if not technology_id:
                continue

            signal = signals_by_id.get(technology_id)
            if signal is None:
                continue

            _extend_packages(dependencies, signal.packages)

        _extend_packages(dependencies, integration.packages)

    return _dedupe_stable_casefold(dependencies)


def _default_dev_dependencies() -> List[str]:
    # httpx + httpx2: fastapi.testclient.TestClient requiere un cliente HTTP compatible con
    # starlette.testclient; según la versión de starlette resuelta, puede exigir httpx2 (más
    # reciente) o httpx (fallback deprecado pero funcional). Declarar ambos evita acoplar el
    # SPEC a una versión concreta del framework.
    return ["pytest", "pytest-mock", "httpx", "httpx2"]


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

    selected = _select_contracts(ir)
    if not selected:
        if ir.product_capabilities:
            raise ValueError(
                "Existen capacidades funcionales, pero no hay contratos API explícitos ni propuestos."
            )
        selected = [(_default_health_contract(), "builder_default")]
        assumptions.append("No había contratos en RequestIR; se añadió GET /health como endpoint mínimo.")

    endpoints: List[Dict[str, Any]] = []
    has_proposed = False
    for c, source_type in selected:
        ep, ep_assumptions = _contract_to_endpoint(c, source_type=source_type)
        endpoints.append(ep)
        assumptions.extend(ep_assumptions)
        if source_type == "proposed":
            has_proposed = True

    endpoints = _dedupe_endpoints_by_method_path(endpoints)

    if has_proposed:
        assumptions.append(
            "Parte de los endpoints provienen de contratos propuestos sin método/ruta literales y requieren confirmación del usuario."
        )

    return endpoints, _dedupe_stable(assumptions)


def _select_contracts(ir: RequestIR) -> List[Tuple[ApiContractIR, str]]:
    selected: List[Tuple[ApiContractIR, str]] = []
    selected.extend((contract, "explicit") for contract in (ir.explicit_api_contracts or []))
    selected.extend((contract, "proposed") for contract in (ir.proposed_api_contracts or []))
    return _dedupe_selected_contracts(selected)


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

    if has_path_param(path):
        request_block = dict(request_block)
        request_block["path_params"] = _infer_path_params_from_schema_hint(path, c.request_schema_hint)

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


def _build_technology_signals(
    technology_signals: Sequence[TechnologySignalIR],
) -> List[Dict[str, Any]]:
    return [asdict(item) for item in (technology_signals or [])]


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
    has_id = has_path_param(path)
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
    has_id = has_path_param(path)

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
            out[seen[k]] = _merge_endpoint_dicts(out[seen[k]], ep)
        else:
            seen[k] = len(out)
            out.append(ep)
    return out


def _dedupe_selected_contracts(
    selected: Sequence[Tuple[ApiContractIR, str]],
) -> List[Tuple[ApiContractIR, str]]:
    precedence = {"builder_default": 0, "proposed": 1, "explicit": 2}
    out: List[Tuple[ApiContractIR, str]] = []
    seen: Dict[Tuple[str, str], int] = {}

    for contract, source_type in selected:
        key = (str(contract.method or "").upper().strip(), str(contract.path or "").strip())
        if key not in seen:
            seen[key] = len(out)
            out.append((contract, source_type))
            continue

        idx = seen[key]
        existing_contract, existing_source = out[idx]
        if precedence.get(source_type, -1) > precedence.get(existing_source, -1):
            merged = _merge_contract_ir(contract, existing_contract)
            out[idx] = (merged, source_type)
        else:
            merged = _merge_contract_ir(existing_contract, contract)
            out[idx] = (merged, existing_source)

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


def _infer_path_params_from_schema_hint(path: str, schema_hint: Any) -> Dict[str, str]:
    """Tipos de los parámetros de `path` (p.ej. {"product_id": "string"}).

    Los nombres se derivan siempre del propio `path` (nunca se asume "id" literal); el
    `schema_hint`, si lo declara, solo aporta el tipo de cada parámetro por nombre.
    """
    param_names = extract_path_param_names(path) or ["id"]

    hint_dict: Dict[str, Any] = {}
    if isinstance(schema_hint, dict):
        for key in ("path_params", "pathParams", "params"):
            v = schema_hint.get(key)
            if isinstance(v, dict):
                hint_dict = v
                break
        else:
            hint_dict = schema_hint

    result: Dict[str, str] = {}
    for name in param_names:
        value = hint_dict.get(name)
        result[name] = value.strip() if isinstance(value, str) and value.strip() else "string"
    return result


def _merge_contract_ir(preferred: ApiContractIR, fallback: ApiContractIR) -> ApiContractIR:
    preferred_request_type = preferred.request_type
    fallback_request_type = fallback.request_type
    merged_request_type = preferred_request_type
    if preferred_request_type in ("none", "", None) and fallback_request_type not in ("none", "", None):
        merged_request_type = fallback_request_type

    request_schema_hint = dict(preferred.request_schema_hint or {})
    if not request_schema_hint and isinstance(fallback.request_schema_hint, dict):
        request_schema_hint = dict(fallback.request_schema_hint or {})
    else:
        for key, value in dict(fallback.request_schema_hint or {}).items():
            request_schema_hint.setdefault(key, value)

    actions = list(preferred.actions or [])
    seen_action_ids = {getattr(action, "id", "").strip().lower() for action in actions}
    for action in fallback.actions or []:
        action_id = getattr(action, "id", "").strip().lower()
        if action_id and action_id not in seen_action_ids:
            actions.append(action)
            seen_action_ids.add(action_id)

    errors = list(preferred.errors or [])
    seen_errors = {
        (getattr(error, "status_code", None), getattr(error, "code", "").strip().lower())
        for error in errors
    }
    for error in fallback.errors or []:
        key = (getattr(error, "status_code", None), getattr(error, "code", "").strip().lower())
        if key not in seen_errors:
            errors.append(error)
            seen_errors.add(key)

    integration_refs = _dedupe_stable(
        list(preferred.integration_refs or []) + list(fallback.integration_refs or [])
    )

    description = str(preferred.description or "").strip() or str(fallback.description or "").strip()
    evidence = str(preferred.evidence or "").strip() or str(fallback.evidence or "").strip()
    assumption = str(preferred.assumption or "").strip() or str(fallback.assumption or "").strip()

    response_example = preferred.response_example
    if response_example in ({}, [], None, ""):
        response_example = fallback.response_example

    return ApiContractIR(
        method=str(preferred.method or fallback.method).upper().strip(),
        path=str(preferred.path or fallback.path).strip(),
        description=description,
        request_type=merged_request_type,
        request_schema_hint=request_schema_hint,
        response_example=response_example,
        evidence=evidence,
        assumption=assumption,
        actions=actions,
        errors=errors,
        integration_refs=integration_refs,
    )


def _merge_endpoint_dicts(preferred: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    precedence = {"builder_default": 0, "proposed": 1, "explicit": 2}
    preferred_type = str((preferred.get("source") or {}).get("type") or "")
    fallback_type = str((fallback.get("source") or {}).get("type") or "")

    if precedence.get(fallback_type, -1) > precedence.get(preferred_type, -1):
        preferred, fallback = fallback, preferred
        preferred_type, fallback_type = fallback_type, preferred_type

    merged = dict(preferred)

    pref_request = dict(preferred.get("request") or {})
    fall_request = dict(fallback.get("request") or {})
    if pref_request.get("type") == "none" and fall_request.get("type") not in (None, "none"):
        pref_request["type"] = fall_request.get("type")
    if not isinstance(pref_request.get("schema"), dict) and isinstance(fall_request.get("schema"), dict):
        pref_request["schema"] = dict(fall_request.get("schema") or {})
    elif isinstance(pref_request.get("schema"), dict) and isinstance(fall_request.get("schema"), dict):
        schema = dict(pref_request.get("schema") or {})
        for key, value in dict(fall_request.get("schema") or {}).items():
            schema.setdefault(key, value)
        pref_request["schema"] = schema
    if "path_params" not in pref_request and "path_params" in fall_request:
        pref_request["path_params"] = fall_request.get("path_params")
    merged["request"] = pref_request

    pref_response = dict(preferred.get("response") or {})
    fall_response = dict(fallback.get("response") or {})
    if not pref_response.get("json_example") and fall_response.get("json_example") is not None:
        pref_response["json_example"] = fall_response.get("json_example")
    merged["response"] = pref_response

    pref_actions = list(preferred.get("actions") or [])
    seen_actions = {str(item.get("id") or "").strip().lower() for item in pref_actions if isinstance(item, dict)}
    for item in fallback.get("actions") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip().lower()
        if key and key not in seen_actions:
            pref_actions.append(item)
            seen_actions.add(key)
    merged["actions"] = pref_actions

    pref_errors = list(preferred.get("errors") or [])
    seen_errors = {
        (item.get("status_code"), str(item.get("code") or "").strip().lower())
        for item in pref_errors
        if isinstance(item, dict)
    }
    for item in fallback.get("errors") or []:
        if not isinstance(item, dict):
            continue
        key = (item.get("status_code"), str(item.get("code") or "").strip().lower())
        if key not in seen_errors:
            pref_errors.append(item)
            seen_errors.add(key)
    merged["errors"] = pref_errors

    merged["integration_refs"] = _dedupe_stable(
        list(preferred.get("integration_refs") or []) + list(fallback.get("integration_refs") or [])
    )
    merged["bundle_files"] = list(dict.fromkeys(
        list(preferred.get("bundle_files") or []) + list(fallback.get("bundle_files") or [])
    ).keys())

    pref_source = dict(preferred.get("source") or {})
    fall_source = dict(fallback.get("source") or {})
    if not pref_source.get("evidence") and fall_source.get("evidence"):
        pref_source["evidence"] = fall_source.get("evidence")
    if not pref_source.get("assumption") and fall_source.get("assumption"):
        pref_source["assumption"] = fall_source.get("assumption")
    pref_source["type"] = preferred_type
    merged["source"] = pref_source

    return merged


def _default_errors_for_endpoint(*, method: str, path: str) -> List[Dict[str, Any]]:
    if has_path_param(path) and method in ("GET", "PUT", "PATCH", "DELETE"):
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
