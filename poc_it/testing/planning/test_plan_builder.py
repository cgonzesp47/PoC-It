from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH
from poc_it.testing.domain.models import (
    TEST_PLAN_PATH,
    AssertionSpec,
    DependencyBehavior,
    EndpointCapabilities,
    EndpointTestPlan,
    RequestSpec,
    ScenarioStep,
    ScenarioTestPlan,
    TestCasePlan,
    TestPlan,
    TestPlanEndpoint,
)

_PATH_PARAM_PATTERN = re.compile(r"\{([^{}]+)\}")
_EXTERNAL_IMPORT_HINTS: List[Tuple[str, str]] = [
    ("sqlalchemy", "db"),
    ("psycopg2", "db"),
    ("asyncpg", "db"),
    ("pymongo", "db"),
    ("redis", "db"),
    ("requests", "network"),
    ("httpx", "network"),
    ("aiohttp", "network"),
    ("urllib3", "network"),
    ("googleapiclient", "credentials"),
    ("google.auth", "credentials"),
    ("boto3", "credentials"),
    ("botocore", "credentials"),
    ("azure", "credentials"),
    ("openai", "network"),
]


@dataclass(frozen=True)
class PlanningResult:
    plan: TestPlan
    structure_with_plan: Dict[str, str]


def _safe_json_loads(raw: str) -> Optional[dict]:
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _upper_mode(mode: Optional[str]) -> str:
    m = (mode or "").strip().upper()
    return m if m in {"PARCIAL", "COMPLETO"} else (m or "UNKNOWN")


def _runtime_openapi_paths(runtime_contracts: Optional[dict]) -> Dict[str, set]:
    if not isinstance(runtime_contracts, dict):
        return {}
    openapi = None
    for k in ("openapi", "openapi_json", "openapi_spec"):
        v = runtime_contracts.get(k)
        if isinstance(v, dict) and v:
            openapi = v
            break
    if not isinstance(openapi, dict):
        return {}
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        return {}
    out: Dict[str, set] = {}
    for p, methods in paths.items():
        if not isinstance(p, str) or not isinstance(methods, dict):
            continue
        ms = {str(m).lower() for m in methods.keys() if isinstance(m, str)}
        if ms:
            out[p] = ms
    return out


def _infer_external_dependency_risk(imports: List[str]) -> str:
    for imp in imports or []:
        s = str(imp or "").lower()
        for needle, kind in _EXTERNAL_IMPORT_HINTS:
            if needle in s:
                return kind
    return "none"


def _pick_expected_success_status(ep: dict) -> Optional[int]:
    sc = ep.get("status_code")
    if isinstance(sc, int) and 200 <= sc < 300:
        return sc
    return None


def _path_param_names(path: str) -> List[str]:
    return [
        match.group(1).strip()
        for match in _PATH_PARAM_PATTERN.finditer(str(path or ""))
        if match.group(1).strip()
    ]


def _default_path_param_value(
    *,
    name: str,
    ep: dict,
    openapi: Optional[dict],
    path: str,
    method: str,
) -> Any:
    explicit_values = ep.get("sample_path_params")
    if isinstance(explicit_values, dict) and name in explicit_values:
        return explicit_values[name]

    parameters = ep.get("parameters")
    if isinstance(parameters, list):
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            if (
                str(parameter.get("name") or "") == name
                and str(parameter.get("in") or "") == "path"
            ):
                schema = parameter.get("schema")
                if isinstance(schema, dict):
                    example = schema.get("example")
                    if example is not None:
                        return example

                    default = schema.get("default")
                    if default is not None:
                        return default

                    param_type = str(schema.get("type") or "")
                    param_format = str(schema.get("format") or "")

                    if param_format == "uuid":
                        return "00000000-0000-4000-8000-000000000001"
                    if param_type == "integer":
                        return 1
                    if param_type == "number":
                        return 1.0

    openapi_paths = openapi.get("paths") if isinstance(openapi, dict) else None
    operation = (
        ((openapi_paths or {}).get(path) or {}).get(method.lower())
        if isinstance(openapi_paths, dict)
        else None
    )

    if isinstance(operation, dict):
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict):
                continue
            if (
                str(parameter.get("name") or "") == name
                and str(parameter.get("in") or "") == "path"
            ):
                schema = parameter.get("schema") or {}

                if parameter.get("example") is not None:
                    return parameter["example"]
                if schema.get("example") is not None:
                    return schema["example"]
                if schema.get("default") is not None:
                    return schema["default"]

                if schema.get("format") == "uuid":
                    return "00000000-0000-4000-8000-000000000001"
                if schema.get("type") == "integer":
                    return 1
                if schema.get("type") == "number":
                    return 1.0

    lower_name = name.lower()

    if (
        lower_name == "id"
        or lower_name.endswith("_id")
        or lower_name in {
            "item_id",
            "product_id",
            "user_id",
            "document_id",
        }
    ):
        return 1

    return "example"


def _sample_path_params(
    *,
    path: str,
    ep: dict,
    openapi: Optional[dict],
    method: str,
) -> Dict[str, Any]:
    return {
        name: _default_path_param_value(
            name=name,
            ep=ep,
            openapi=openapi,
            path=path,
            method=method,
        )
        for name in _path_param_names(path)
    }


def _jsonschema_default_value(schema: Optional[dict]) -> Any:
    if not isinstance(schema, dict) or not schema:
        return "x"
    t = (schema.get("type") or "").strip().lower()
    if not t:
        fmt = str(schema.get("format") or "").lower().strip()
        if fmt in ("int32", "int64"):
            return 1
        if fmt in ("float", "double", "decimal"):
            return 10.0
        if fmt in ("date-time", "date"):
            return "1970-01-01T00:00:00Z" if fmt == "date-time" else "1970-01-01"
        return "x"
    if t == "string":
        return "x"
    if t == "integer":
        return 1
    if t == "number":
        return 10.0
    if t == "boolean":
        return True
    if t == "array":
        return []
    if t == "object":
        return {}
    return "x"


def _extract_request_schema_from_openapi(*, openapi: Optional[dict], path: str, method_lower: str) -> Optional[dict]:
    if not isinstance(openapi, dict):
        return None
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        return None
    op = (paths.get(path) or {}).get(method_lower)
    if not isinstance(op, dict):
        return None
    rb = op.get("requestBody")
    if not isinstance(rb, dict):
        return None
    content = rb.get("content")
    if not isinstance(content, dict):
        return None
    for mt in ("application/json", "application/*+json"):
        if mt in content and isinstance(content.get(mt), dict):
            schema = (content.get(mt) or {}).get("schema")
            return schema if isinstance(schema, dict) else None
    for value in content.values():
        if isinstance(value, dict) and isinstance(value.get("schema"), dict):
            return value.get("schema")
    return None


def _extract_response_info_from_openapi(*, openapi: Optional[dict], path: str, method_lower: str) -> Tuple[Optional[int], List[int], Optional[str], List[str]]:
    if not isinstance(openapi, dict):
        return None, [], None, []
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        return None, [], None, []
    op = (paths.get(path) or {}).get(method_lower)
    if not isinstance(op, dict):
        return None, [], None, []
    responses = op.get("responses")
    if not isinstance(responses, dict) or not responses:
        return None, [], None, []

    codes: List[int] = []
    media_type: Optional[str] = None
    required_keys: List[str] = []

    for code_str, response in responses.items():
        if isinstance(code_str, str) and code_str.isdigit():
            codes.append(int(code_str))
        if media_type is None and isinstance(code_str, str) and code_str.isdigit():
            code = int(code_str)
            if 200 <= code < 300 and isinstance(response, dict):
                content = response.get("content")
                if isinstance(content, dict):
                    for mt in ("application/json", "application/*+json"):
                        if mt in content:
                            media_type = mt
                            schema = (content.get(mt) or {}).get("schema") if isinstance(content.get(mt), dict) else None
                            if isinstance(schema, dict):
                                props = schema.get("properties")
                                req = schema.get("required")
                                if isinstance(req, list):
                                    required_keys = [str(x).strip() for x in req if str(x).strip()]
                                elif isinstance(props, dict):
                                    required_keys = [str(k).strip() for k in list(props.keys())[:16] if str(k).strip()]
                            break

    allowed = sorted({c for c in codes if c < 500})
    twoxx = sorted([c for c in allowed if 200 <= c < 300])
    expected = twoxx[0] if len(twoxx) == 1 else None
    return expected, allowed, media_type, required_keys


def _infer_expected_status_from_openapi(*, method: str, two_xx: List[int]) -> Optional[int]:
    if not two_xx:
        return None
    method = method.upper().strip()
    statuses = sorted(set(two_xx))
    if len(statuses) == 1:
        return statuses[0]
    if method == "POST":
        return 201 if 201 in statuses else (200 if 200 in statuses else statuses[0])
    if method == "GET":
        return 200 if 200 in statuses else statuses[0]
    if method in ("PUT", "PATCH"):
        return 200 if 200 in statuses else statuses[0]
    if method == "DELETE":
        return 204 if 204 in statuses else (200 if 200 in statuses else statuses[0])
    return statuses[0]


def _sample_request_from_contract(*, ep: dict, openapi: Optional[dict], path: str, method: str) -> Optional[dict]:
    sr = ep.get("sample_request")
    if isinstance(sr, dict) and sr:
        return sr

    schema = _extract_request_schema_from_openapi(openapi=openapi, path=path, method_lower=method.lower())
    if isinstance(schema, dict):
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        req = schema.get("required") if isinstance(schema.get("required"), list) else []
        req_fields = [str(x).strip() for x in req if str(x).strip()]
        if not req_fields:
            rf = ep.get("request_required_fields") or []
            if isinstance(rf, list):
                req_fields = [str(x).strip() for x in rf if str(x).strip()]
        if req_fields:
            out: Dict[str, Any] = {}
            for key in req_fields:
                prop_schema = props.get(key) if isinstance(props, dict) else None
                out[key] = _jsonschema_default_value(prop_schema if isinstance(prop_schema, dict) else None)
            return out or None

    req_fields = ep.get("request_required_fields") or []
    if not isinstance(req_fields, list) or not req_fields:
        return None

    out: Dict[str, Any] = {}
    for key in req_fields:
        kk = str(key).strip()
        if not kk:
            continue
        lower_key = kk.lower()
        if lower_key in ("id", "product_id", "user_id") or lower_key.endswith("_id"):
            out[kk] = 1
        elif lower_key in ("precio", "price", "amount", "importe", "total", "count", "cantidad"):
            out[kk] = 10.0
        elif lower_key in ("disponible", "available", "enabled", "activo", "active"):
            out[kk] = True
        else:
            out[kk] = "x"
    return out or None


def _required_response_keys(ep: dict) -> List[str]:
    keys = ep.get("response_json_required_keys") or ep.get("response_model_required_fields") or []
    if not isinstance(keys, list):
        return []
    out: List[str] = []
    for key in keys:
        value = str(key).strip()
        if value and value not in out:
            out.append(value)
    return out


def _endpoint_has_depends_overrideable(ep: dict, allowed: set[str]) -> bool:
    deps_imports = ep.get("depends_imports") or []
    if not isinstance(deps_imports, list) or not deps_imports:
        # Sin dependencias inyectadas => no hay nada que "overridear". Esto es
        # VACUAMENTE cierto (no un "no lo sabemos"): un endpoint sin dependencias externas es
        # hermético por construcción. Devolver False aquí (como antes) capaba estructuralmente
        # a CUALQUIER endpoint sin `Depends()` (p.ej. GET /health) en OPENAPI_CONTRACT para
        # siempre, aunque fuera trivialmente determinista y no tuviera nada que mockear.
        return True
    deps = [str(dep).strip() for dep in deps_imports if str(dep).strip()]
    if not deps:
        return True
    return bool(allowed) and all(dep in allowed for dep in deps)


def _openapi_from_spec(spec: Optional[dict]) -> Optional[dict]:
    if not isinstance(spec, dict):
        return None
    if isinstance(spec.get("openapi"), dict):
        return spec.get("openapi")
    if isinstance(spec.get("paths"), dict) and ("components" in spec or "info" in spec or isinstance(spec.get("openapi"), str)):
        return spec
    return None


def _declared_errors_by_endpoint(spec: Optional[dict]) -> Dict[Tuple[str, str], List[dict]]:
    """Índice (method, path) -> errores declarados por el usuario en la plantilla
    (`ContratoAPI.errors`, materializados por spec_builder en `spec["endpoints"][i]["errors"]`).

    Esta es la evidencia que Fase B necesita para generar casos de error hermético que reflejen
    lo que el usuario describió (403, 409, ...), en vez de limitarse al 422 genérico de FastAPI.
    """
    result: Dict[Tuple[str, str], List[dict]] = {}
    if not isinstance(spec, dict):
        return result
    endpoints = spec.get("endpoints")
    if not isinstance(endpoints, list):
        return result
    for item in endpoints:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "").upper().strip()
        path = str(item.get("path") or "").strip()
        errors = item.get("errors")
        if not method or not path or not isinstance(errors, list):
            continue
        result[(method, path)] = [e for e in errors if isinstance(e, dict)]
    return result


def _build_capabilities(
    *,
    in_openapi: bool,
    sample_req: Optional[dict],
    overrideable: bool,
    allowed: set[str],
    req_keys: List[str],
    stateful: bool,
    ep: dict,
) -> EndpointCapabilities:
    has_deps = bool(ep.get("depends_imports"))
    method = str(ep.get("method") or "").upper()
    return EndpointCapabilities(
        startup_available=True,
        openapi_available=in_openapi,
        request_schema_complete=sample_req is not None or method in {"GET", "DELETE"},
        valid_request_generatable=sample_req is not None or method in {"GET", "DELETE"},
        invalid_request_generatable=sample_req is not None,
        dependencies_discovered=has_deps,
        dependencies_overrideable=overrideable,
        dependency_protocol_known=overrideable or any(str(x).endswith(".get_db") for x in allowed),
        response_contract_known=bool(req_keys) or in_openapi,
        stateful_candidate=stateful,
    )


def _dependency_setup_from_endpoint(
    ep: dict, allowed: Optional[set] = None, *, auto_mock_external_risk: bool = False
) -> List[DependencyBehavior]:
    result: List[DependencyBehavior] = []

    for item in ep.get("dependency_behaviors") or []:
        if not isinstance(item, dict):
            continue

        dependency_fqn = str(item.get("dependency_fqn") or "").strip()
        action = str(item.get("action") or "").strip()
        method_name = str(item.get("method_name") or "").strip()

        if not dependency_fqn:
            continue

        if action == "provide":
            result.append(
                DependencyBehavior(
                    dependency_fqn=dependency_fqn,
                    method_name="",
                    action="provide",
                    value=item.get("value"),
                )
            )
            continue

        if method_name and action in {"return", "raise", "async_return"}:
            result.append(
                DependencyBehavior(
                    dependency_fqn=dependency_fqn,
                    method_name=method_name,
                    action=action,
                    value=item.get("value"),
                    exception_type=item.get("exception_type"),
                    exception_message=item.get("exception_message"),
                )
            )

    # Fallback determinista: para cualquier dependencia inyectada de este endpoint que esté
    # marcada como overrideable (`allowed_dependency_overrides`), pertenezca a un proyecto con
    # riesgo de integración externa real detectado (`_infer_external_dependency_risk`: SDKs de
    # red/DB/credenciales) y no tenga ya un comportamiento explícito (arriba, típicamente vía
    # semantic enrichment LLM), generamos un doble automático genérico (`unittest.mock.MagicMock`)
    # en lugar de dejarla sin mockear.
    #
    # Por qué solo cuando hay riesgo externo: cuando la dependencia inyectada es un fake/objeto en
    # memoria SIN SDK de red/credenciales (p.ej. un repositorio in-memory en los fixtures E2E), es
    # perfectamente seguro dejar que el test la ejecute de verdad — y hacerlo produce datos reales
    # coherentes (ids, campos) en vez de un MagicMock vacío que rompe las aserciones de contenido.
    # Aplicar el auto-mock incondicionalmente ahí sustituía esa implementación real y funcional por
    # un doble que no sabe qué forma debe tener la respuesta.
    #
    # Por qué hace falta esto para dependencias de riesgo real: sin él, `dependency_setup` quedaba
    # vacío para CUALQUIER dependencia externa no cubierta por enrichment semántico (que en
    # producción nunca está configurado con un LLM real — ver `SemanticEnrichmentService`), así que
    # en los tests herméticos la dependencia real se ejecutaba de verdad (p.ej. un cliente de
    # Google Drive intentando autenticarse), provocando errores 500 en vez de un test hermético.
    already_covered = {behavior.dependency_fqn for behavior in result}
    if not auto_mock_external_risk:
        return result
    for dep_fqn in ep.get("depends_imports") or []:
        dep_fqn = str(dep_fqn or "").strip()
        if not dep_fqn or dep_fqn in already_covered:
            continue
        if not allowed or dep_fqn not in allowed:
            continue
        result.append(
            DependencyBehavior(
                dependency_fqn=dep_fqn,
                method_name="",
                action="provide_auto",
                value=None,
            )
        )
        already_covered.add(dep_fqn)

    return result


def _declared_error_cases(
    *,
    base_case_id: str,
    path: str,
    method: str,
    path_params: Dict[str, Any],
    sample_req: Optional[dict],
    ep: dict,
    already_used_statuses: set,
    auto_mock_external_risk: bool,
) -> Tuple[List[TestCasePlan], List[str]]:
    """Genera un caso HERMETIC_HTTP por cada error declarado por el usuario en la plantilla
    (`ContratoAPI.errors`, propagado hasta aquí como `ep["declared_errors"]`) que no esté ya
    cubierto por otro caso (happy path, 404 por not-found, controlled_failure, 422 validación).

    Solo se genera cuando `auto_mock_external_risk` es True: es el único escenario en el que la
    dependencia se liga a un auto-double MagicMock permisivo (`provide_auto`), así que podemos
    hacer que CUALQUIER llamada sobre él lance la excepción declarada (`provide_auto_raise`) sin
    arriesgarnos a un `StrictDoubleError` por adivinar mal el nombre del método real invocado —
    riesgo real cuando la plantilla solo declara status_code/code/description, no qué método de
    qué dependencia dispara ese error.
    """
    cases: List[TestCasePlan] = []
    evidence: List[str] = []
    if not auto_mock_external_risk:
        return cases, evidence

    dep_imports = [str(x).strip() for x in (ep.get("depends_imports") or []) if str(x).strip()]
    if not dep_imports:
        return cases, evidence
    dep_fqn = dep_imports[0]

    declared = ep.get("declared_errors") or []
    seen_status: set = set()
    for item in declared:
        if not isinstance(item, dict):
            continue
        status_code = item.get("status_code")
        if not isinstance(status_code, int) or not (400 <= status_code < 600):
            continue
        if status_code in already_used_statuses or status_code in seen_status:
            continue
        seen_status.add(status_code)

        code = str(item.get("code") or "").strip() or f"error_{status_code}"
        description = str(item.get("description") or code).strip()

        # Herméticos: cualquier otra dependencia del endpoint (además de la que forzamos a
        # fallar) también se auto-mockea, para no dejar que un dependency real (con riesgo
        # externo) se ejecute de verdad en un caso que se supone hermético.
        case_dep_setup = [
            DependencyBehavior(
                dependency_fqn=dep_fqn,
                method_name="",
                action="provide_auto_raise",
                value=None,
                exception_type="HTTPException",
                exception_message=description,
                exception_status_code=status_code,
            )
        ]
        for other_dep_fqn in dep_imports[1:]:
            case_dep_setup.append(
                DependencyBehavior(
                    dependency_fqn=other_dep_fqn,
                    method_name="",
                    action="provide_auto",
                    value=None,
                )
            )

        cases.append(
            TestCasePlan(
                case_id=f"{base_case_id}__declared_error_{status_code}",
                level="HERMETIC_HTTP",
                category="declared_error",
                request=RequestSpec(
                    method=method,
                    path_template=path,
                    path_params=dict(path_params),
                    json_body=sample_req,
                    expected_status=status_code,
                    allowed_statuses=[status_code],
                    response_media_type="application/json",
                ),
                dependency_setup=case_dep_setup,
                assertions=[
                    AssertionSpec(
                        kind="STATUS_EQUALS",
                        expected=status_code,
                        metadata={"evidence": f"declared_error:{code}"},
                    )
                ],
            )
        )
        evidence.append(f"declared_error:{code}")

    return cases, evidence


def _build_endpoint_cases(
    *,
    path: str,
    method: str,
    path_params: Dict[str, Any],
    capabilities: EndpointCapabilities,
    expected: Optional[int],
    allowed_statuses: List[int],
    sample_req: Optional[dict],
    req_keys: List[str],
    resp_mt: Optional[str],
    ep: dict,
    allowed: Optional[set] = None,
    auto_mock_external_risk: bool = False,
    request_required_keys: Optional[List[str]] = None,
) -> Tuple[List[TestCasePlan], List[str], List[str]]:
    cases: List[TestCasePlan] = []
    limitations: List[str] = []
    evidence: List[str] = []
    base_case_id = f"{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '') or 'root'}"

    cases.append(
        TestCasePlan(
            case_id=f"{base_case_id}__startup",
            level="STARTUP",
            category="startup",
            request=None,
            assertions=[AssertionSpec(kind="STATE_CONTAINS", target="startup_available", expected=True)],
        )
    )
    evidence.append("startup_available")

    if capabilities.openapi_available:
        cases.append(
            TestCasePlan(
                case_id=f"{base_case_id}__openapi",
                level="OPENAPI_CONTRACT",
                category="openapi",
                request=None,
                assertions=[
                    AssertionSpec(kind="STATE_CONTAINS", target="openapi_path", expected=path),
                    AssertionSpec(kind="STATE_CONTAINS", target="openapi_method", expected=method, metadata={"path": path}),
                ],
            )
        )
        evidence.append("openapi_available")
    else:
        limitations.append("OpenAPI no contiene el endpoint o no pudo confirmarse su presencia.")

    can_http = (
        capabilities.startup_available
        and capabilities.valid_request_generatable
        and capabilities.dependencies_overrideable
        and expected is not None
        and bool(resp_mt or req_keys or allowed_statuses)
    )
    if can_http:
        used_statuses: set[int] = {422}
        if isinstance(expected, int):
            used_statuses.add(expected)

        dep_setup = _dependency_setup_from_endpoint(
            ep, allowed, auto_mock_external_risk=auto_mock_external_risk
        )
        assertions = [AssertionSpec(kind="STATUS_EQUALS", expected=expected, metadata={"evidence": "response.status"})]
        if req_keys:
            assertions.append(
                AssertionSpec(kind="JSON_HAS_KEYS", expected=req_keys, metadata={"evidence": "response.required_fields"})
            )
        cases.append(
            TestCasePlan(
                case_id=f"{base_case_id}__happy_path",
                level="HERMETIC_HTTP",
                category="happy_path",
                request=RequestSpec(
                    method=method,
                    path_template=path,
                    path_params=dict(path_params),
                    json_body=sample_req,
                    expected_status=expected,
                    allowed_statuses=allowed_statuses,
                    response_media_type=resp_mt,
                ),
                dependency_setup=dep_setup,
                assertions=assertions,
            )
        )
        evidence.extend(["dependencies_overrideable", "expected_response_evidence"])

        not_found = ep.get("not_found_evidence")
        if isinstance(not_found, dict) and int(not_found.get("declared_status") or 0) == 404 and bool(
            not_found.get("dependency_can_return_none")
        ):
            cases.append(
                TestCasePlan(
                    case_id=f"{base_case_id}__not_found",
                    level="HERMETIC_HTTP",
                    category="not_found",
                    request=RequestSpec(
                        method=method,
                        path_template=path,
                        path_params=dict(path_params),
                        json_body=sample_req,
                        expected_status=404,
                        allowed_statuses=[404],
                        response_media_type="application/json",
                    ),
                    dependency_setup=dep_setup
                    + [
                        DependencyBehavior(
                            dependency_fqn=str((ep.get("depends_imports") or ["primary_dependency"])[0]),
                            method_name=str((not_found.get("method_name") or not_found.get("dependency_method") or "get_by_id")),
                            action="return",
                            value=None,
                        )
                    ],
                    assertions=[AssertionSpec(kind="STATUS_EQUALS", expected=404, metadata={"evidence": "declared_404"})],
                )
            )
            evidence.append("not_found_evidence")
            used_statuses.add(404)

        controlled_failure = ep.get("controlled_failure_evidence")
        if isinstance(controlled_failure, dict):
            failure_status = controlled_failure.get("declared_status")
            exception_name = controlled_failure.get("exception")
            if isinstance(failure_status, int) and 400 <= failure_status < 500 and exception_name:
                cases.append(
                    TestCasePlan(
                        case_id=f"{base_case_id}__controlled_failure",
                        level="HERMETIC_HTTP",
                        category="controlled_failure",
                        request=RequestSpec(
                            method=method,
                            path_template=path,
                            path_params=dict(path_params),
                            json_body=sample_req,
                            expected_status=failure_status,
                            allowed_statuses=[failure_status],
                            response_media_type="application/json",
                        ),
                        dependency_setup=dep_setup
                        + [
                            DependencyBehavior(
                                dependency_fqn=str((ep.get("depends_imports") or ["primary_dependency"])[0]),
                                method_name=str(
                                    controlled_failure.get("method_name")
                                    or controlled_failure.get("dependency_method")
                                    or "execute"
                                ),
                                action="raise",
                                value=None,
                                exception_type=str(exception_name),
                                exception_message=str(controlled_failure.get("message") or exception_name),
                            )
                        ],
                        assertions=[
                            AssertionSpec(
                                kind="STATUS_EQUALS",
                                expected=failure_status,
                                metadata={"evidence": f"controlled_failure:{exception_name}"},
                            )
                        ],
                    )
                )
                evidence.append("controlled_failure_evidence")
                used_statuses.add(failure_status)

        declared_error_cases, declared_error_evidence = _declared_error_cases(
            base_case_id=base_case_id,
            path=path,
            method=method,
            path_params=path_params,
            sample_req=sample_req,
            ep=ep,
            already_used_statuses=used_statuses,
            auto_mock_external_risk=auto_mock_external_risk,
        )
        cases.extend(declared_error_cases)
        evidence.extend(declared_error_evidence)

        # Solo afirmamos 422 si tenemos evidencia positiva de al menos un campo requerido en el
        # request (OpenAPI requestBody.schema.required): un handler `payload: dict` (sin modelo
        # Pydantic) acepta `{}` como body válido y FastAPI nunca lo rechaza, así que sin esta
        # evidencia el caso generado afirmaría un 422 que nunca ocurre.
        if sample_req is not None and request_required_keys:
            cases.append(
                TestCasePlan(
                    case_id=f"{base_case_id}__validation_error",
                    level="HERMETIC_HTTP",
                    category="validation",
                    request=RequestSpec(
                        method=method,
                        path_template=path,
                        path_params=dict(path_params),
                        json_body={},
                        expected_status=422,
                        allowed_statuses=[422],
                        response_media_type="application/json",
                    ),
                    dependency_setup=dep_setup,
                    assertions=[AssertionSpec(kind="STATUS_EQUALS", expected=422, metadata={"evidence": "fastapi_validation"})],
                )
            )
            evidence.append("invalid_request_generatable")
    else:
        if not capabilities.dependencies_overrideable and capabilities.dependencies_discovered:
            limitations.append("Las dependencias fueron descubiertas pero no todas son overrideables.")
        if not capabilities.valid_request_generatable and method not in {"GET", "DELETE"}:
            limitations.append("No se pudo generar una request válida para el endpoint.")
        if expected is None:
            limitations.append("No hay evidencia suficiente para fijar un status exacto del caso feliz.")
        if not bool(resp_mt or req_keys or allowed_statuses):
            limitations.append("No hay evidencia suficiente sobre la respuesta esperada del endpoint.")

    if not limitations:
        limitations.append("Sin limitaciones relevantes detectadas.")
    return cases, limitations, evidence


def _validate_scenario_plan(scenario: ScenarioTestPlan) -> None:
    if not scenario.scenario_id.strip():
        raise ValueError("ScenarioTestPlan requires scenario_id")
    if not scenario.operations:
        raise ValueError("ScenarioTestPlan requires at least one operation")
    if not (0.0 <= float(scenario.confidence) <= 1.0):
        raise ValueError("ScenarioTestPlan confidence must be between 0 and 1")

    captures: set[str] = set()
    for step in scenario.operations:
        if not step.operation_id.strip():
            raise ValueError("ScenarioStep requires operation_id")
        for field_path, alias in (step.capture or {}).items():
            if not str(field_path).strip() or not str(alias).strip():
                raise ValueError("ScenarioStep captures must define source and alias")
            captures.add(str(alias).strip())
        rendered_path = step.request.path_template
        for placeholder in captures:
            token = "{" + placeholder + "}"
            if token in rendered_path:
                continue

    for assertion in scenario.assertions:
        if assertion.kind == "STATUS_EQUALS" and assertion.expected is None:
            raise ValueError("Scenario assertions with STATUS_EQUALS require expected value")


def _endpoint_case_map(endpoint_plans: List[EndpointTestPlan]) -> Dict[str, Dict[str, TestCasePlan]]:
    out: Dict[str, Dict[str, TestCasePlan]] = {}
    for endpoint_plan in endpoint_plans:
        by_category: Dict[str, TestCasePlan] = {}
        for case in endpoint_plan.cases:
            by_category[case.category] = case
        out[endpoint_plan.operation_id] = by_category
    return out


def _build_scenario_plans(*, mode: str, endpoint_plans: List[EndpointTestPlan]) -> List[ScenarioTestPlan]:
    if mode != "COMPLETO":
        return []

    case_map = _endpoint_case_map(endpoint_plans)
    by_path_method = {(endpoint.method, endpoint.path): endpoint for endpoint in endpoint_plans}
    scenarios: List[ScenarioTestPlan] = []

    for endpoint in endpoint_plans:
        if not endpoint.capabilities.stateful_candidate:
            continue
        create_case = next((case for case in endpoint.cases if case.category == "happy_path"), None)
        if create_case is None or create_case.request is None:
            continue

        method = endpoint.method.upper()
        path = endpoint.path
        if method != "POST":
            continue

        # Buscamos el endpoint de recurso único correspondiente a esta colección
        # (p.ej. GET/DELETE /products/{product_id} para POST /products) sin asumir
        # ningún nombre concreto de path param: basta con que sea "el path de la
        # colección + exactamente un segmento {...}".
        stripped = path.rstrip("/")
        single_resource_re = re.compile(rf"^{re.escape(stripped)}/\{{[^{{}}]+\}}$")

        read_endpoint = None
        delete_endpoint = None
        for (candidate_method, candidate_path), candidate_endpoint in by_path_method.items():
            if not single_resource_re.match(candidate_path):
                continue
            if candidate_method == "GET":
                read_endpoint = candidate_endpoint
            elif candidate_method == "DELETE":
                delete_endpoint = candidate_endpoint

        if read_endpoint is None:
            continue
        read_case = next((case for case in read_endpoint.cases if case.category == "happy_path"), None)
        if read_case is None or read_case.request is None:
            continue

        not_found_case = next((case for case in read_endpoint.cases if case.category == "not_found"), None)

        shared_dependencies = sorted(
            {
                behavior.dependency
                for selected_case in [create_case, read_case]
                for behavior in selected_case.dependency_setup
                if behavior.dependency
            }
        )

        steps: List[ScenarioStep] = [
            ScenarioStep(
                operation_id=endpoint.operation_id,
                request=create_case.request,
                capture={"response.id": "product_id"},
                assertions=create_case.assertions,
            ),
            ScenarioStep(
                operation_id=read_endpoint.operation_id,
                request=RequestSpec(
                    method=read_case.request.method,
                    path_template=read_case.request.path_template,
                    path_params={"product_id": "{product_id}"},
                    expected_status=read_case.request.expected_status,
                    allowed_statuses=list(read_case.request.allowed_statuses),
                    response_media_type=read_case.request.response_media_type,
                ),
                capture={},
                assertions=read_case.assertions,
            ),
        ]

        scenario_assertions = [
            AssertionSpec(
                kind="JSON_FIELD_EQUALS",
                target="name",
                expected=(create_case.request.json_body or {}).get("name"),
                metadata={"evidence": "stateful_read_after_create"},
            )
        ]

        evidence = ["shared_state", "capture:response.id", "read_after_create"]
        confidence = 0.85

        if delete_endpoint is not None:
            delete_case = next((case for case in delete_endpoint.cases if case.category == "happy_path"), None)
            if delete_case is not None and delete_case.request is not None:
                steps.append(
                    ScenarioStep(
                        operation_id=delete_endpoint.operation_id,
                        request=RequestSpec(
                            method=delete_case.request.method,
                            path_template=delete_case.request.path_template,
                            path_params={"product_id": "{product_id}"},
                            expected_status=delete_case.request.expected_status,
                            allowed_statuses=list(delete_case.request.allowed_statuses),
                            response_media_type=delete_case.request.response_media_type,
                        ),
                        capture={},
                        assertions=delete_case.assertions,
                    )
                )
                evidence.append("delete_after_create")
                if not_found_case is not None and not_found_case.request is not None:
                    steps.append(
                        ScenarioStep(
                            operation_id=read_endpoint.operation_id,
                            request=RequestSpec(
                                method=not_found_case.request.method,
                                path_template=not_found_case.request.path_template,
                                path_params={"product_id": "{product_id}"},
                                expected_status=404,
                                allowed_statuses=[404],
                                response_media_type=not_found_case.request.response_media_type,
                            ),
                            capture={},
                            assertions=not_found_case.assertions,
                        )
                    )
                    scenario_assertions.append(
                        AssertionSpec(kind="STATUS_EQUALS", expected=404, metadata={"evidence": "read_after_delete"})
                    )
                    evidence.append("read_after_delete")
                    confidence = 0.95

        scenario = ScenarioTestPlan(
            scenario_id=f"scenario__{endpoint.operation_id.replace(':', '__').replace('/', '_').replace('{', '').replace('}', '')}",
            name=f"Workflow for {endpoint.method} {endpoint.path}",
            level="SEMANTIC_STATEFUL",
            operations=steps,
            shared_dependencies=shared_dependencies,
            assertions=scenario_assertions,
            evidence=evidence,
            confidence=confidence,
        )
        _validate_scenario_plan(scenario)
        scenarios.append(scenario)

    return scenarios


def _legacy_level_from_cases(cases: List[TestCasePlan]) -> str:
    levels = {case.level for case in cases}
    if "HERMETIC_HTTP" in levels:
        return "HERMETIC_ENDPOINT_CONTRACT"
    if "OPENAPI_CONTRACT" in levels:
        return "OPENAPI_CONTRACT"
    return "SMOKE_ONLY"


def _legacy_endpoint_from_new_plan(endpoint_plan: EndpointTestPlan) -> TestPlanEndpoint:
    http_case = next((case for case in endpoint_plan.cases if case.level == "HERMETIC_HTTP"), None)
    request = http_case.request if http_case else None
    response_keys: List[str] = []
    if http_case:
        for assertion in http_case.assertions:
            if assertion.kind == "JSON_HAS_KEYS" and isinstance(assertion.expected, list):
                response_keys = [str(x) for x in assertion.expected]
                break
    return TestPlanEndpoint(
        path=endpoint_plan.path,
        method=endpoint_plan.method,
        level=_legacy_level_from_cases(endpoint_plan.cases),
        reason="; ".join(endpoint_plan.limitations),
        expected_status=request.expected_status if request else None,
        allowed_statuses=list(request.allowed_statuses) if request else [],
        sample_request=dict(request.json_body) if request and isinstance(request.json_body, dict) else None,
        required_response_keys=response_keys,
        response_media_type=request.response_media_type if request else None,
        allow_semantic_asserts=False,
        hermetic=True,
    )


def build_test_plan(*, structure: Dict[str, str], spec: Optional[dict], mode: str) -> TestPlan:
    rc_raw = structure.get(RUNTIME_CONTRACTS_PATH) or ""
    rc = _safe_json_loads(rc_raw) if isinstance(rc_raw, str) and rc_raw.strip() else None

    m = _upper_mode(mode)
    endpoints = (rc or {}).get("endpoints") if isinstance(rc, dict) else None
    endpoints = endpoints if isinstance(endpoints, list) else []

    # Preferimos el OpenAPI REAL capturado en vivo de la app en ejecución (persistido en
    # runtime_contracts.json por reparacion_runtime.py durante wiring verify), porque el SPEC
    # determinista no puede conocer el status code real de cada endpoint (los decoradores de
    # FastAPI rara vez lo declaran explícito). Sin este fallback, ningún endpoint puede
    # promocionar más allá de OPENAPI_CONTRACT por falta de "evidencia" de su status feliz.
    openapi = _openapi_from_spec(spec)
    if not isinstance(openapi, dict) and isinstance(rc, dict) and isinstance(rc.get("openapi"), dict):
        openapi = rc.get("openapi")
    openapi_paths = _runtime_openapi_paths({"openapi": openapi} if isinstance(openapi, dict) else rc) if isinstance(rc, dict) or isinstance(openapi, dict) else {}

    imports = list((rc or {}).get("imports") or []) if isinstance(rc, dict) else []
    risk = _infer_external_dependency_risk([str(x) for x in imports if str(x).strip()])

    allowed: set[str] = set()
    if isinstance(rc, dict):
        for value in (rc.get("allowed_dependency_overrides") or []):
            item = str(value).strip()
            if item:
                allowed.add(item)

    declared_errors_by_endpoint = _declared_errors_by_endpoint(spec)

    endpoint_plans: List[EndpointTestPlan] = []

    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        path = str(ep.get("path") or "").strip()
        method = str(ep.get("method") or "").upper().strip()
        if not path or not method:
            continue

        declared_errors = declared_errors_by_endpoint.get((method, path))
        if declared_errors:
            ep = {**ep, "declared_errors": declared_errors}

        methods_in_openapi = openapi_paths.get(path, set()) if openapi_paths else set()
        in_openapi = bool(method.lower() in methods_in_openapi) if methods_in_openapi else True
        overrideable = _endpoint_has_depends_overrideable(ep, allowed)
        expected = _pick_expected_success_status(ep)

        openapi_expected, openapi_allowed, openapi_mt, openapi_required_keys = _extract_response_info_from_openapi(
            openapi=openapi,
            path=path,
            method_lower=method.lower(),
        )
        allowed_statuses = list(openapi_allowed)
        if expected is None:
            two_xx = [code for code in allowed_statuses if 200 <= code < 300]
            expected = _infer_expected_status_from_openapi(method=method, two_xx=two_xx) or openapi_expected

        sample_req = _sample_request_from_contract(ep=ep, openapi=openapi, path=path, method=method)
        req_keys = _required_response_keys(ep) or openapi_required_keys

        request_schema = _extract_request_schema_from_openapi(openapi=openapi, path=path, method_lower=method.lower())
        request_required_keys = list(request_schema.get("required") or []) if isinstance(request_schema, dict) else []
        resp_mt = ep.get("response_media_type") if isinstance(ep.get("response_media_type"), str) else None
        if not resp_mt and isinstance(openapi_mt, str):
            resp_mt = openapi_mt

        stateful = str(ep.get("statefulness_recommended") or "").strip().lower() == "per_client_fixture"

        path_params = _sample_path_params(
            path=path,
            ep=ep,
            openapi=openapi,
            method=method,
        )

        capabilities = _build_capabilities(
            in_openapi=in_openapi,
            sample_req=sample_req,
            overrideable=overrideable,
            allowed=allowed,
            req_keys=req_keys,
            stateful=stateful,
            ep=ep,
        )
        cases, limitations, evidence = _build_endpoint_cases(
            path=path,
            method=method,
            path_params=path_params,
            capabilities=capabilities,
            expected=expected,
            allowed_statuses=allowed_statuses,
            sample_req=sample_req,
            req_keys=req_keys,
            resp_mt=resp_mt,
            ep=ep,
            allowed=allowed,
            auto_mock_external_risk=(risk != "none"),
            request_required_keys=request_required_keys,
        )

        # `risk` se infiere de los imports de TODO el proyecto (ver más arriba), no solo de este
        # endpoint — un endpoint sin ninguna dependencia/llamada propia (p.ej. GET /health) no
        # debe heredar el riesgo de un módulo de integración completamente distinto que use el
        # mismo proyecto. Solo aplicamos el aviso si HAY evidencia de que este endpoint concreto
        # toca alguna dependencia (inyectada u observada mediante llamadas).
        endpoint_touches_dependency = bool(ep.get("depends_imports") or ep.get("observed_calls"))
        if (
            m == "PARCIAL"
            and risk != "none"
            and not capabilities.dependencies_overrideable
            and endpoint_touches_dependency
        ):
            limitations.append(f"Riesgo de integración externa detectado: {risk}.")

        endpoint_plans.append(
            EndpointTestPlan(
                operation_id=f"{method.lower()}:{path}",
                method=method,
                path=path,
                capabilities=capabilities,
                cases=cases,
                limitations=limitations,
                evidence=evidence,
            )
        )

    if not endpoint_plans:
        endpoint_plans.append(
            EndpointTestPlan(
                operation_id="*:*",
                method="*",
                path="*",
                capabilities=EndpointCapabilities(
                    startup_available=True,
                    openapi_available=False,
                    request_schema_complete=False,
                    valid_request_generatable=False,
                    invalid_request_generatable=False,
                    dependencies_discovered=False,
                    dependencies_overrideable=False,
                    dependency_protocol_known=False,
                    response_contract_known=False,
                    stateful_candidate=False,
                ),
                cases=[
                    TestCasePlan(
                        case_id="global__startup",
                        level="STARTUP",
                        category="startup",
                        request=None,
                        assertions=[AssertionSpec(kind="STATE_CONTAINS", target="startup_available", expected=True)],
                    )
                ],
                limitations=["No hay runtime_contracts/endpoints disponibles."],
                evidence=["startup_available"],
            )
        )

    scenario_plans = _build_scenario_plans(mode=m, endpoint_plans=endpoint_plans)
    legacy_endpoints = [_legacy_endpoint_from_new_plan(ep) for ep in endpoint_plans]

    totals: Dict[str, int] = {"total_endpoints": len(endpoint_plans), "total_scenarios": len(scenario_plans)}
    for level in ("STARTUP", "OPENAPI_CONTRACT", "HERMETIC_HTTP"):
        totals[level] = sum(1 for ep in endpoint_plans for case in ep.cases if case.level == level)
    totals["SEMANTIC_STATEFUL"] = len(scenario_plans)
    for level in ("SMOKE_ONLY", "OPENAPI_CONTRACT", "HERMETIC_ENDPOINT_CONTRACT"):
        totals[level] = sum(1 for endpoint in legacy_endpoints if endpoint.level == level)

    return TestPlan(
        mode=m,
        endpoint_plans=endpoint_plans,
        scenario_plans=scenario_plans,
        endpoints=legacy_endpoints,
        totals=totals,
    )


def persist_test_plan(*, structure: Dict[str, str], plan: TestPlan) -> Dict[str, str]:
    patched = dict(structure)
    patched[TEST_PLAN_PATH] = plan.to_json()
    return patched


def build_test_plan_for_generation(
    *,
    project_structure: Dict[str, str],
    runtime_contracts: Optional[dict],
    runtime_facts: Optional[dict],
    spec: Optional[dict],
    mode: str,
    project_name: str,
) -> PlanningResult:
    structure = dict(project_structure or {})
    if isinstance(runtime_contracts, dict):
        structure[RUNTIME_CONTRACTS_PATH] = json.dumps(runtime_contracts, ensure_ascii=False, indent=2) + "\n"
    if isinstance(runtime_facts, dict):
        structure[".poc_it/runtime_facts.json"] = json.dumps(runtime_facts, ensure_ascii=False, indent=2) + "\n"

    plan = build_test_plan(
        structure=structure,
        spec=spec if isinstance(spec, dict) else None,
        mode=mode,
    )
    structure = persist_test_plan(structure=structure, plan=plan)
    return PlanningResult(plan=plan, structure_with_plan=structure)
