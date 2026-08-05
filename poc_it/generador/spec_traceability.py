from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from poc_it.generador.request_ir import RequestIR


@dataclass(frozen=True)
class TraceabilityError:
    code: str
    path: str
    message: str


def validate_request_ir_to_spec_traceability(
    ir: RequestIR,
    spec: Dict[str, Any],
) -> List[TraceabilityError]:
    if not isinstance(spec, dict):
        return [
            TraceabilityError(
                code="TRACE_SPEC_NOT_OBJECT",
                path="$",
                message="SPEC debe ser dict para validar trazabilidad.",
            )
        ]

    errors: List[TraceabilityError] = []

    tech_signals = spec.get("technology_signals")
    tech_names = {
        str(item.get("name") or "").strip().lower()
        for item in tech_signals
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }

    for signal in ir.technology_signals or []:
        if str(getattr(signal, "confidence", "") or "").strip().lower() != "explicit":
            continue
        name = str(getattr(signal, "name", "") or "").strip()
        if name and name.lower() not in tech_names:
            errors.append(
                TraceabilityError(
                    code="TRACE_TECHNOLOGY_LOST",
                    path="$.technology_signals",
                    message=f"No se preservó technology signal explícita: {name}",
                )
            )

    integrations = spec.get("integrations")
    spec_integrations_by_id = {
        str(item.get("id") or "").strip().lower(): item
        for item in integrations
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    for integration in ir.integrations or []:
        integration_id = str(getattr(integration, "id", "") or "").strip()
        if integration_id and integration_id.lower() not in spec_integrations_by_id:
            errors.append(
                TraceabilityError(
                    code="TRACE_INTEGRATION_LOST",
                    path="$.integrations",
                    message=f"No se preservó integration: {integration_id}",
                )
            )

    configuration = spec.get("configuration")
    spec_configuration_by_key = {
        str(item.get("key") or "").strip().lower(): item
        for item in configuration
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    spec_env_names = _env_names(spec.get("env"))
    for item in ir.configuration or []:
        key = str(getattr(item, "key", "") or "").strip()
        if not key:
            continue
        if key.lower() not in spec_configuration_by_key:
            errors.append(
                TraceabilityError(
                    code="TRACE_CONFIGURATION_LOST",
                    path="$.configuration",
                    message=f"No se preservó configuration: {key}",
                )
            )
        delivery = str(getattr(item, "delivery", "env") or "env").strip().lower() or "env"
        if delivery == "env" and key.lower() not in spec_env_names:
            errors.append(
                TraceabilityError(
                    code="TRACE_ENV_LOST",
                    path="$.env",
                    message=f"No se preservó variable de entorno: {key}",
                )
            )

    dependencies = {
        str(dep or "").strip().lower()
        for dep in (spec.get("dependencies") or [])
        if isinstance(dep, str) and str(dep).strip()
    }
    for signal in ir.technology_signals or []:
        package = str(getattr(signal, "package", "") or "").strip()
        confidence = str(getattr(signal, "confidence", "") or "").strip().lower()
        if confidence == "explicit" and package and package.lower() not in dependencies:
            errors.append(
                TraceabilityError(
                    code="TRACE_DEPENDENCY_LOST",
                    path="$.dependencies",
                    message=f"No se preservó dependency explícita: {package}",
                )
            )

    endpoints = spec.get("endpoints")
    spec_endpoints_by_key = {
        (
            str(item.get("method") or "").upper().strip(),
            str(item.get("path") or "").strip(),
        ): item
        for item in endpoints
        if isinstance(item, dict)
    }

    for contract in list(ir.explicit_api_contracts or []) + list(ir.proposed_api_contracts or []):
        key = (
            str(getattr(contract, "method", "") or "").upper().strip(),
            str(getattr(contract, "path", "") or "").strip(),
        )
        endpoint = spec_endpoints_by_key.get(key)
        if endpoint is None:
            errors.append(
                TraceabilityError(
                    code="TRACE_ENDPOINT_LOST",
                    path="$.endpoints",
                    message=f"No se preservó endpoint {key[0]} {key[1]}",
                )
            )
            continue

        endpoint_action_ids = {
            str(item.get("id") or "").strip().lower()
            for item in (endpoint.get("actions") or [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        for action in contract.actions or []:
            action_id = str(getattr(action, "id", "") or "").strip()
            if action_id and action_id.lower() not in endpoint_action_ids:
                errors.append(
                    TraceabilityError(
                        code="TRACE_ACTION_LOST",
                        path=f"$.endpoints[{key[0]} {key[1]}].actions",
                        message=f"No se preservó action {action_id} en {key[0]} {key[1]}",
                    )
                )

        endpoint_errors = {
            (
                _normalize_status_code(item.get("status_code") if isinstance(item, dict) else None),
                str(item.get("code") or "").strip().lower(),
            )
            for item in (endpoint.get("errors") or [])
            if isinstance(item, dict) and str(item.get("code") or "").strip()
        }
        for error in contract.errors or []:
            err_key = (
                _normalize_status_code(getattr(error, "status_code", None)),
                str(getattr(error, "code", "") or "").strip().lower(),
            )
            if err_key[1] and err_key not in endpoint_errors:
                errors.append(
                    TraceabilityError(
                        code="TRACE_ERROR_LOST",
                        path=f"$.endpoints[{key[0]} {key[1]}].errors",
                        message=f"No se preservó error {err_key[0]} {err_key[1]} en {key[0]} {key[1]}",
                    )
                )

        endpoint_integration_refs = {
            str(value or "").strip().lower()
            for value in (endpoint.get("integration_refs") or [])
            if isinstance(value, str) and str(value).strip()
        }
        for ref in contract.integration_refs or []:
            ref_norm = str(ref or "").strip().lower()
            if ref_norm and ref_norm not in endpoint_integration_refs:
                errors.append(
                    TraceabilityError(
                        code="TRACE_INTEGRATION_REF_LOST",
                        path=f"$.endpoints[{key[0]} {key[1]}].integration_refs",
                        message=f"No se preservó integration_ref {ref} en {key[0]} {key[1]}",
                    )
                )

    return errors


def _env_names(raw: Any) -> set[str]:
    out: set[str] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.add(item.strip().lower())
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                out.add(name.lower())
    return out


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
