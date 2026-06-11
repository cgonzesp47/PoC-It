from __future__ import annotations
"""
poc_it.orquestacion.test_plan

Test planning determinista (contract-first).

Motivación
----------
El enfoque anterior generaba tests (a menudo con LLM) y luego intentaba "repararlos" vía un bucle
basado en pytest. En modo PARCIAL esto se atasca frecuentemente porque:
- el código generado depende de integraciones externas (DB, Drive, red, credenciales)
- los tests intentan invocar endpoints sin aislar dependencias correctamente
- cuando el endpoint devuelve 500, el repair loop tiende a relajar asserts, en lugar de diagnosticar
  si el fallo proviene de código, harness u "external dependency leak".

Este módulo reemplaza ese enfoque por un plan de validación por contrato:
- Clasifica cada endpoint según el nivel de test REALISTA y verificable.
- Genera requests de ejemplo mínimas (best-effort) a partir de runtime_contracts/OpenAPI.
- Decide si un endpoint puede ejecutarse herméticamente o debe degradarse a OpenAPI-only.
- Produce un artefacto estable: `.poc_it/test_plan.json` (persistido en el proyecto materializado).

Niveles
-------
A) SMOKE_ONLY
   - Solo valida que `import app.main` funciona y que /openapi.json responde.

B) OPENAPI_CONTRACT
   - Valida que el endpoint existe en /openapi.json (path+method).
   - NO invoca el endpoint.

C) HERMETIC_ENDPOINT_CONTRACT
   - Se puede invocar herméticamente (solo si podemos aislar dependencias con Depends + overrides).
   - Oráculo permitido:
     - si expected_status está definido: comprobar ese status.
     - si no: comprobar status_code < 500.
     - nunca aceptar 5xx.
     - si OpenAPI declara JSON: response.json() parseable.
     - si hay required_response_keys: comprobar presence.
   - Prohibido: asserts exactos de response.json().

D) SEMANTIC_STATEFUL
   - Solo para PoCs COMPLETAS autocontenidas o casos donde el sistema controle el estado.
   - No se aplica en PARCIAL salvo que el plan marque explícitamente candidato y overrides verificados.

Diseño (pragmático)
-------------------
Este planner es best-effort: nunca "adivina" integraciones ocultas.
Su objetivo es ser estable, conservador y que los tests converjan.

Entrada
-------
- runtime_contracts.json (obligatorio si existe)
- runtime_facts.json (opcional; hoy se usa poco)
- spec (dict; opcional)
- modo de generación (COMPLETO/PARCIAL)
- flags: hermetic (por defecto True)

Salida
------
`.poc_it/test_plan.json` con estructura:

{
  "mode": "PARCIAL",
  "strategy": "contract-first",
  "endpoints": [
    {
      "path": "/productos",
      "method": "POST",
      "level": "HERMETIC_ENDPOINT_CONTRACT",
      "reason": "...",
      "expected_status": 201,
      "sample_request": {...},
      "required_response_keys": ["id", ...],
      "allow_semantic_asserts": false
    },
    ...
  ]
}
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from poc_it.runtime_contracts import RUNTIME_CONTRACTS_PATH

TEST_PLAN_PATH = ".poc_it/test_plan.json"


# ----------------------------- Data model ---------------------------------


@dataclass(frozen=True)
class TestPlanEndpoint:
    path: str
    method: str
    level: str  # SMOKE_ONLY | OPENAPI_CONTRACT | HERMETIC_ENDPOINT_CONTRACT | SEMANTIC_STATEFUL
    reason: str

    expected_status: Optional[int] = None
    allowed_statuses: List[int] = field(default_factory=list)

    sample_request: Optional[dict] = None
    required_response_keys: List[str] = field(default_factory=list)
    response_media_type: Optional[str] = None

    allow_semantic_asserts: bool = False
    hermetic: bool = True


@dataclass(frozen=True)
class TestPlan:
    mode: str
    strategy: str = "contract-first"
    endpoints: List[TestPlanEndpoint] = field(default_factory=list)

    # stats/resumen (útil para report)
    totals: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # dict->list[str] canonical
        return d


# ----------------------------- Helpers ------------------------------------


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


_EXTERNAL_IMPORT_HINTS: List[Tuple[str, str]] = [
    # DB
    ("sqlalchemy", "db"),
    ("psycopg2", "db"),
    ("asyncpg", "db"),
    ("pymongo", "db"),
    ("redis", "db"),
    # network
    ("requests", "network"),
    ("httpx", "network"),
    ("aiohttp", "network"),
    ("urllib3", "network"),
    # cloud/credentials
    ("googleapiclient", "credentials"),
    ("google.auth", "credentials"),
    ("boto3", "credentials"),
    ("botocore", "credentials"),
    ("azure", "credentials"),
    ("openai", "network"),
]


def _infer_external_dependency_risk(imports: List[str]) -> str:
    """Heurística best-effort. No pretende ser completa."""
    low = "none"
    for imp in imports or []:
        s = str(imp or "").lower()
        for needle, kind in _EXTERNAL_IMPORT_HINTS:
            if needle in s:
                return kind
    return low


def _pick_expected_success_status(ep: dict) -> Optional[int]:
    """Selecciona un status 2xx preferido si el contrato lo trae.
    Hoy runtime_contracts.status_code suele ser el decorador FastAPI (si existe).
    """
    try:
        sc = ep.get("status_code")
        if isinstance(sc, int) and 200 <= sc < 300:
            return sc
    except Exception:
        pass
    return None


def _jsonschema_default_value(schema: Optional[dict]) -> Any:
    """Devuelve un valor dummy tipado para un schema OpenAPI/JSONSchema (best-effort)."""
    if not isinstance(schema, dict) or not schema:
        return "x"
    # OpenAPI: schema puede ser {"$ref": "..."}; aquí no resolvemos refs.
    t = (schema.get("type") or "").strip().lower()
    if not t:
        # heurística simple por format
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
        # si hay properties, podemos rellenarlas de forma mínima si no hay required explícito
        props = schema.get("properties")
        if isinstance(props, dict) and props:
            return {}
        return {}
    return "x"


def _extract_request_schema_from_openapi(
    *,
    openapi: Optional[dict],
    path: str,
    method_lower: str,
) -> Optional[dict]:
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
    # preferimos json
    for mt in ("application/json", "application/*+json"):
        if mt in content and isinstance(content.get(mt), dict):
            sch = (content.get(mt) or {}).get("schema")
            return sch if isinstance(sch, dict) else None
    # fallback: primer content-type
    for v in content.values():
        if isinstance(v, dict) and isinstance(v.get("schema"), dict):
            return v.get("schema")
    return None


def _extract_response_info_from_openapi(
    *,
    openapi: Optional[dict],
    path: str,
    method_lower: str,
) -> Tuple[Optional[int], List[int], Optional[str], List[str]]:
    """Retorna: expected_2xx, allowed_statuses, response_media_type, required_keys (si schema object)."""
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

    for code_str, resp in responses.items():
        try:
            if isinstance(code_str, str) and code_str.isdigit():
                codes.append(int(code_str))
        except Exception:
            pass

        # Intentar obtener media_type y required keys solo del primer 2xx json
        try:
            if media_type is None and isinstance(code_str, str) and code_str.isdigit():
                c = int(code_str)
                if 200 <= c < 300 and isinstance(resp, dict):
                    content = resp.get("content")
                    if isinstance(content, dict):
                        # prefer json
                        for mt in ("application/json", "application/*+json"):
                            if mt in content:
                                media_type = mt
                                sch = (content.get(mt) or {}).get("schema") if isinstance(content.get(mt), dict) else None
                                if isinstance(sch, dict):
                                    props = sch.get("properties")
                                    req = sch.get("required")
                                    if isinstance(req, list):
                                        required_keys = [str(x).strip() for x in req if str(x).strip()]
                                    elif isinstance(props, dict):
                                        # si no hay required, al menos usar keys declaradas (limitadas) como hint
                                        required_keys = [str(k).strip() for k in list(props.keys())[:16] if str(k).strip()]
                                break
                        if media_type is None:
                            # primer content-type
                            for mt, v in content.items():
                                if isinstance(mt, str):
                                    media_type = mt
                                    break
        except Exception:
            pass

    # allowed_statuses: todos los codes explícitos, pero nunca 5xx
    allowed = sorted({c for c in codes if c < 500})

    # expected_status: inferencia si hay 2xx
    twoxx = sorted([c for c in allowed if 200 <= c < 300])
    expected: Optional[int] = None
    if len(twoxx) == 1:
        expected = twoxx[0]
    elif len(twoxx) > 1:
        # se decide más arriba con reglas por método (se recalcula fuera si hace falta)
        expected = None

    return expected, allowed, media_type, required_keys


def _infer_expected_status_from_openapi(
    *,
    method: str,
    two_xx: List[int],
) -> Optional[int]:
    if not two_xx:
        return None
    method = method.upper().strip()
    s = sorted(set(two_xx))
    if len(s) == 1:
        return s[0]

    if method == "POST":
        return 201 if 201 in s else (200 if 200 in s else s[0])
    if method == "GET":
        return 200 if 200 in s else s[0]
    if method in ("PUT", "PATCH"):
        return 200 if 200 in s else s[0]
    if method == "DELETE":
        return 204 if 204 in s else (200 if 200 in s else s[0])
    return s[0]


def _sample_request_from_contract(
    *,
    ep: dict,
    openapi: Optional[dict],
    path: str,
    method: str,
) -> Optional[dict]:
    """Construye request mínima tipada para evitar 422, usando OpenAPI si es posible."""
    # 1) Si runtime_contracts ya trae sample_request, solo úsala si es compatible con OpenAPI (si lo tenemos).
    try:
        sr = ep.get("sample_request")
        if isinstance(sr, dict) and sr:
            sch0 = _extract_request_schema_from_openapi(openapi=openapi, path=path, method_lower=method.lower())
            props0 = sch0.get("properties") if isinstance(sch0, dict) and isinstance(sch0.get("properties"), dict) else None
            if not props0:
                return sr

            def _is_compatible(v: Any, ps: Optional[dict]) -> bool:
                if not isinstance(ps, dict) or not ps:
                    return True
                t = str(ps.get("type") or "").lower().strip()
                if not t:
                    return True
                if t == "string":
                    return isinstance(v, str)
                if t == "integer":
                    return isinstance(v, int) and not isinstance(v, bool)
                if t == "number":
                    return isinstance(v, (int, float)) and not isinstance(v, bool)
                if t == "boolean":
                    return isinstance(v, bool)
                if t == "array":
                    return isinstance(v, list)
                if t == "object":
                    return isinstance(v, dict)
                return True

            ok = True
            for k, v in sr.items():
                ps = props0.get(k) if isinstance(props0, dict) else None
                if not _is_compatible(v, ps if isinstance(ps, dict) else None):
                    ok = False
                    break
            if ok:
                return sr
    except Exception:
        pass

    # 2) OpenAPI schema
    sch = _extract_request_schema_from_openapi(openapi=openapi, path=path, method_lower=method.lower())
    if isinstance(sch, dict):
        props = sch.get("properties") if isinstance(sch.get("properties"), dict) else {}
        req = sch.get("required") if isinstance(sch.get("required"), list) else []
        req_fields = [str(x).strip() for x in req if str(x).strip()]
        # si no hay required, usamos request_required_fields del probe
        if not req_fields:
            rf = ep.get("request_required_fields") or []
            if isinstance(rf, list):
                req_fields = [str(x).strip() for x in rf if str(x).strip()]

        if req_fields:
            out: Dict[str, Any] = {}
            for k in req_fields:
                ps = props.get(k) if isinstance(props, dict) else None
                out[k] = _jsonschema_default_value(ps if isinstance(ps, dict) else None)
            return out or None

    # 3) fallback: request_required_fields => valores tipados genéricos por heurística de nombre
    req_fields = ep.get("request_required_fields") or []
    if not isinstance(req_fields, list) or not req_fields:
        return None
    out: Dict[str, Any] = {}
    for k in req_fields:
        kk = str(k).strip()
        if not kk:
            continue
        l = kk.lower()
        if l in ("id", "product_id", "user_id") or l.endswith("_id"):
            out[kk] = 1
        elif l in ("precio", "price", "amount", "importe", "total", "count", "cantidad"):
            out[kk] = 10.0
        elif l in ("disponible", "available", "enabled", "activo", "active"):
            out[kk] = True
        else:
            out[kk] = "x"
    return out or None


def _required_response_keys(ep: dict) -> List[str]:
    keys = ep.get("response_json_required_keys") or ep.get("response_model_required_fields") or []
    if not isinstance(keys, list):
        return []
    out = []
    for k in keys:
        s = str(k).strip()
        if s:
            out.append(s)
    # quitar duplicados manteniendo orden
    seen = set()
    uniq = []
    for k in out:
        if k in seen:
            continue
        seen.add(k)
        uniq.append(k)
    return uniq


def _endpoint_has_depends_overrideable(ep: dict, allowed: set[str]) -> bool:
    """
    Devuelve True si (y solo si) TODAS las dependencias declaradas por el probe son overrideables.

    Nota importante:
    - `runtime_contracts.allowed_dependency_overrides` NO es una lista de "deps usadas por endpoint",
      sino una allowlist global de overrides que el harness sabe generar.
    - `ep.depends_imports` lista callables usados en Depends() (p.ej. app.db.get_db).
    - Si un endpoint depende de algo fuera de allowlist, en modo PARCIAL NO lo podemos invocar
      herméticamente (porque podría tocar DB/Settings/red). Debe degradarse a OPENAPI_CONTRACT.
    """
    deps_imports = ep.get("depends_imports") or []
    if not isinstance(deps_imports, list) or not deps_imports:
        return False

    deps = [str(d).strip() for d in deps_imports if str(d).strip()]
    if not deps:
        return False

    # allowlist explícita: exige que TODAS estén incluidas
    if allowed:
        return all(d in allowed for d in deps)

    # sin allowlist: no podemos asegurar hermetismo (mejor ser conservador)
    return False


def _openapi_from_spec(spec: Optional[dict]) -> Optional[dict]:
    """Acepta spec en formato internal (con spec['openapi']) o directamente openapi.json."""
    if not isinstance(spec, dict):
        return None
    if isinstance(spec.get("openapi"), dict):
        return spec.get("openapi")
    # Heurística: si parece openapi root
    if isinstance(spec.get("paths"), dict) and isinstance(spec.get("openapi"), str):
        return spec
    if isinstance(spec.get("paths"), dict) and ("components" in spec or "info" in spec):
        return spec
    return None


def build_test_plan(
    *,
    structure: Dict[str, str],
    spec: Optional[dict],
    mode: str,
) -> TestPlan:
    rc_raw = structure.get(RUNTIME_CONTRACTS_PATH) or ""
    rc = _safe_json_loads(rc_raw) if isinstance(rc_raw, str) and rc_raw.strip() else None

    m = _upper_mode(mode)
    endpoints = (rc or {}).get("endpoints") if isinstance(rc, dict) else None
    endpoints = endpoints if isinstance(endpoints, list) else []

    # OpenAPI existence map + spec (para sample_request, expected_status, allowed_statuses)
    openapi = _openapi_from_spec(spec)
    openapi_paths = _runtime_openapi_paths({"openapi": openapi} if isinstance(openapi, dict) else rc) if isinstance(rc, dict) or isinstance(openapi, dict) else {}

    imports = []
    try:
        imports = list((rc or {}).get("imports") or [])
    except Exception:
        imports = []

    # Riesgo global inferido por imports. En PARCIAL lo usamos para degradar si parece tocar red/credenciales/DB.
    risk = _infer_external_dependency_risk([str(x) for x in imports if str(x).strip()])

    allowed = set()
    try:
        for x in (rc or {}).get("allowed_dependency_overrides") or []:
            s = str(x).strip()
            if s:
                allowed.add(s)
    except Exception:
        allowed = set()

    planned: List[TestPlanEndpoint] = []

    # Política base por modo:
    # - PARCIAL: por defecto NO invocamos endpoints salvo que sea overrideable (Depends imports).
    # - COMPLETO: permitimos invocación hermética si overrideable, y stateful solo si hay señal.
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue

        path = str(ep.get("path") or "").strip()
        method = str(ep.get("method") or "").upper().strip()
        if not path or not method:
            continue

        # Si el endpoint ni siquiera está en OpenAPI, degradar a OPENAPI_CONTRACT (lo marcará el test_openapi)
        methods_in_openapi = openapi_paths.get(path, set()) if openapi_paths else set()
        in_openapi = bool(method.lower() in methods_in_openapi) if methods_in_openapi else True  # si no hay openapi, no bloqueamos

        overrideable = _endpoint_has_depends_overrideable(ep, allowed)

        expected = _pick_expected_success_status(ep)

        # Inferencias OpenAPI (status + allowed_statuses + media_type + required keys)
        openapi_expected, openapi_allowed, openapi_mt, openapi_required_keys = _extract_response_info_from_openapi(
            openapi=openapi,
            path=path,
            method_lower=method.lower(),
        )

        # allowed_statuses solo desde OpenAPI (sin inventar)
        allowed_statuses = list(openapi_allowed)

        # expected_status: prefer runtime_contracts si ya trae 2xx; si no, inferir de OpenAPI 2xx con reglas por método
        if expected is None:
            two_xx = [c for c in allowed_statuses if 200 <= c < 300]
            expected = _infer_expected_status_from_openapi(method=method, two_xx=two_xx) or openapi_expected

        # sample_request tipado desde OpenAPI
        sample_req = _sample_request_from_contract(ep=ep, openapi=openapi, path=path, method=method)

        # required_response_keys: preferimos probe, si no usar OpenAPI required keys (best-effort)
        req_keys = _required_response_keys(ep) or openapi_required_keys

        resp_mt = ep.get("response_media_type") if isinstance(ep.get("response_media_type"), str) else None
        if not resp_mt and isinstance(openapi_mt, str):
            resp_mt = openapi_mt

        # heurística para stateful: solo si runtime_contracts recomienda y modo completo
        stateful = str(ep.get("statefulness_recommended") or "").strip().lower() == "per_client_fixture"

        if not in_openapi:
            planned.append(
                TestPlanEndpoint(
                    path=path,
                    method=method,
                    level="OPENAPI_CONTRACT",
                    reason="endpoint not present in runtime OpenAPI (cannot be invoked safely)",
                    expected_status=None,
                    sample_request=None,
                    required_response_keys=[],
                    response_media_type=resp_mt,
                    allow_semantic_asserts=False,
                    hermetic=True,
                )
            )
            continue

        if m == "PARCIAL":
            # En PARCIAL debemos ser conservadores: aunque podamos overridear un Service, si NO podemos
            # overridear el acceso a DB/Settings/red (p.ej. get_db / get_settings), el endpoint puede
            # seguir filtrando dependencias externas (como en PoC301_5: Settings() exige DATABASE_URL).
            #
            # Regla pragmática: solo invocar endpoints si:
            #   - todas las deps del endpoint están en allowlist (overrideable)
            #   - y la allowlist incluye un override de DB (get_db) o se ha declarado explícitamente que no hay DB.
            has_db_override = any(str(x).endswith(".get_db") for x in allowed)
            can_invoke = overrideable and has_db_override and (sample_req is not None or method in ("GET", "DELETE"))
            if can_invoke:
                planned.append(
                    TestPlanEndpoint(
                        path=path,
                        method=method,
                        level="HERMETIC_ENDPOINT_CONTRACT",
                        reason="PARCIAL: overrides cover endpoint deps and DB is overrideable; request can be shaped",
                        expected_status=expected,
                        allowed_statuses=allowed_statuses,
                        sample_request=sample_req,
                        required_response_keys=req_keys,
                        response_media_type=resp_mt,
                        allow_semantic_asserts=False,
                        hermetic=True,
                    )
                )
            else:
                # Si el motivo real es que faltan overrides de DB/Settings, preferimos explicitarlo.
                if overrideable and not has_db_override:
                    reason = "PARCIAL: missing DB override (get_db) in allowed_dependency_overrides"
                else:
                    reason = "PARCIAL: external integration risk" if risk != "none" else "PARCIAL: dependencies not hermetically overrideable (missing get_db/get_settings)"
                planned.append(
                    TestPlanEndpoint(
                        path=path,
                        method=method,
                        level="OPENAPI_CONTRACT",
                        reason=reason,
                        expected_status=None,
                        sample_request=None,
                        required_response_keys=[],
                        response_media_type=resp_mt,
                        allow_semantic_asserts=False,
                        hermetic=True,
                    )
                )
            continue

        # COMPLETO
        if stateful and overrideable:
            planned.append(
                TestPlanEndpoint(
                    path=path,
                    method=method,
                    level="SEMANTIC_STATEFUL",
                    reason="COMPLETO: stateful candidate and dependency overrides available",
                    expected_status=expected,
                    allowed_statuses=allowed_statuses,
                    sample_request=sample_req,
                    required_response_keys=req_keys,
                    response_media_type=resp_mt,
                    allow_semantic_asserts=True,
                    hermetic=True,
                )
            )
        elif overrideable and (sample_req is not None or method in ("GET", "DELETE")):
            planned.append(
                TestPlanEndpoint(
                    path=path,
                    method=method,
                    level="HERMETIC_ENDPOINT_CONTRACT",
                    reason="COMPLETO: dependency overrides available and request can be shaped",
                    expected_status=expected,
                    allowed_statuses=allowed_statuses,
                    sample_request=sample_req,
                    required_response_keys=req_keys,
                    response_media_type=resp_mt,
                    allow_semantic_asserts=False,
                    hermetic=True,
                )
            )
        else:
            planned.append(
                TestPlanEndpoint(
                    path=path,
                    method=method,
                    level="OPENAPI_CONTRACT",
                    reason="COMPLETO: endpoint not safely invokable hermetically (insufficient override signals)",
                    expected_status=None,
                    sample_request=None,
                    required_response_keys=[],
                    response_media_type=resp_mt,
                    allow_semantic_asserts=False,
                    hermetic=True,
                )
            )

    # Si no hay endpoints o no hay runtime_contracts: suite mínima
    if not planned:
        planned.append(
            TestPlanEndpoint(
                path="*",
                method="*",
                level="SMOKE_ONLY",
                reason="no runtime_contracts/endpoints available; fallback to smoke+openapi",
                hermetic=True,
            )
        )

    totals: Dict[str, int] = {"total_endpoints": len(planned)}
    for lv in ("SMOKE_ONLY", "OPENAPI_CONTRACT", "HERMETIC_ENDPOINT_CONTRACT", "SEMANTIC_STATEFUL"):
        totals[lv] = sum(1 for e in planned if e.level == lv)

    return TestPlan(mode=m, endpoints=planned, totals=totals)


def persist_test_plan(*, structure: Dict[str, str], plan: TestPlan) -> Dict[str, str]:
    patched = dict(structure)
    patched[TEST_PLAN_PATH] = json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n"
    return patched
