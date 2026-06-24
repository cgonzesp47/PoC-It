from __future__ import annotations
"""
Extracción/normalización determinista: ContextoNormalizado -> IRSpecPlan.

Responsabilidad:
- Convertir la fuente de verdad del usuario (ContextoNormalizado, incluyendo contratos_api)
  a un IR tipado y estable.
- Aplicar defaults conservadores para que el SPEC resultante sea mínimo y consistente
  incluso si el LLM de normalización devolvió información pobre/incompleta.

No usa LLM.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from poc_it.generador.spec_ir import IRSpecPlan, IREndpoint, IRRequest, IRResponse, IREnvVar, dedupe_endpoints
from poc_it.modulos.models import ContextoNormalizado, ContratoAPI


def _safe_str(x: Any) -> str:
    return str(x) if x is not None else ""


def _normalize_path(p: str) -> str:
    p = _safe_str(p).strip()
    if not p:
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    # elimina dobles //
    while "//" in p:
        p = p.replace("//", "/")
    return p


def _normalize_method(m: str) -> str:
    m = _safe_str(m).upper().strip()
    if m in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return m
    # default conservador
    return "GET"


def _infer_request_type_from_contrato(c: ContratoAPI) -> str:
    try:
        t = (c.request.type or "").strip()
        if t in ("json", "multipart", "query", "none"):
            return t
    except Exception:
        pass
    return "none"


def build_ir_from_context(
    ctx: ContextoNormalizado | Dict[str, Any] | None,
) -> IRSpecPlan:
    """
    Política:
    - Si hay contratos_api, se usan como fuente de verdad para endpoints.
    - Si NO hay contratos_api, generamos un IR mínimo (solo /health) para asegurar PoC viable.
      Esto cumple el objetivo de resiliencia: el LLM nunca es la fuente de verdad global.
    """
    if ctx is None:
        return _ir_minimo()

    if isinstance(ctx, dict):
        # best-effort: no acoplar a pydantic aquí
        contratos_raw = ctx.get("contratos_api")
        if isinstance(contratos_raw, list) and contratos_raw:
            endpoints = [_endpoint_from_contrato_dict(c) for c in contratos_raw]
            endpoints = [e for e in endpoints if e is not None]
            endpoints = dedupe_endpoints(endpoints)
            if endpoints:
                return IRSpecPlan(endpoints=endpoints)
        return _ir_minimo()

    if isinstance(ctx, ContextoNormalizado):
        if ctx.contratos_api:
            endpoints = [_endpoint_from_contrato_model(c) for c in ctx.contratos_api]
            endpoints = [e for e in endpoints if e is not None]
            endpoints = dedupe_endpoints(endpoints)
            if endpoints:
                return IRSpecPlan(endpoints=endpoints)
        return _ir_minimo()

    return _ir_minimo()


def _endpoint_from_contrato_model(c: ContratoAPI) -> Optional[IREndpoint]:
    method = _normalize_method(c.method)
    path = _normalize_path(c.path)

    req_type = _infer_request_type_from_contrato(c)
    schema = {}
    try:
        if req_type == "json":
            schema = dict(c.request.schema_hint or {})
    except Exception:
        schema = {}

    resp_example: Any = {}
    try:
        resp_example = c.response.json_example
    except Exception:
        resp_example = {}

    return IREndpoint(
        method=method,  # type: ignore[arg-type]
        path=path,
        request=IRRequest(type=req_type, schema=schema),  # type: ignore[arg-type]
        response=IRResponse(json_example=resp_example),
        errors=[],
    )


def _endpoint_from_contrato_dict(c: Dict[str, Any]) -> Optional[IREndpoint]:
    if not isinstance(c, dict):
        return None
    method = _normalize_method(c.get("method"))
    path = _normalize_path(c.get("path"))

    req = c.get("request") if isinstance(c.get("request"), dict) else {}
    req_type = _safe_str(req.get("type")).strip()
    if req_type not in ("json", "multipart", "query", "none"):
        req_type = "none"

    schema = {}
    if req_type == "json":
        sch = req.get("schema_hint")
        if isinstance(sch, dict):
            schema = sch

    resp = c.get("response") if isinstance(c.get("response"), dict) else {}
    resp_example = resp.get("json_example", {})

    return IREndpoint(
        method=method,  # type: ignore[arg-type]
        path=path,
        request=IRRequest(type=req_type, schema=schema),  # type: ignore[arg-type]
        response=IRResponse(json_example=resp_example),
        errors=[],
    )


def _ir_minimo() -> IRSpecPlan:
    """
    IR mínimo determinista, independiente del LLM.

    /health es crítico para:
    - probes runtime
    - smoke tests
    - demostraciones y verificación rápida
    """
    return IRSpecPlan(
        endpoints=[
            IREndpoint(
                method="GET",  # type: ignore[arg-type]
                path="/health",
                request=IRRequest(type="none"),  # type: ignore[arg-type]
                response=IRResponse(json_example={"status": "ok"}),
                errors=[],
            )
        ],
        notes="IR mínimo: no se detectaron contratos_api; se generó /health determinista.",
    )
