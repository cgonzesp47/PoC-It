from __future__ import annotations

from typing import Any, Dict, List

from .models import EndpointTestContract


def build_endpoint_test_contracts(
    *,
    spec: Dict[str, Any],
    file_contracts: List[Dict[str, Any]],
) -> List[EndpointTestContract]:
    contracts_by_path = {
        str(item.get("path") or "").replace("\\", "/"): item
        for item in file_contracts
        if isinstance(item, dict)
    }
    result: List[EndpointTestContract] = []

    for endpoint in spec.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        endpoint_path = str(endpoint.get("path") or "").strip()
        methods = endpoint.get("methods") or endpoint.get("method") or []
        if isinstance(methods, str):
            methods = [methods]

        endpoint_file = _resolve_endpoint_file(
            endpoint=endpoint,
            contracts_by_path=contracts_by_path,
        )
        if not endpoint_file:
            continue

        file_contract = contracts_by_path.get(endpoint_file, {})
        internal_calls = [
            dict(item)
            for item in (file_contract.get("required_internal_calls") or [])
            if isinstance(item, dict)
        ]
        declared_errors = _normalize_declared_errors(
            endpoint.get("errors")
            or (file_contract.get("endpoint") or {}).get("errors")
            or []
        )
        request = dict(
            endpoint.get("request") or (file_contract.get("endpoint") or {}).get("request") or {}
        )
        response = dict(
            endpoint.get("response") or (file_contract.get("endpoint") or {}).get("response") or {}
        )
        success_case_required = True
        integration_refs = _endpoint_integration_refs(endpoint, file_contract)
        test_kinds = _build_test_kinds(
            integration_refs=integration_refs,
            declared_errors=declared_errors,
            request=request,
            response=response,
        )

        for method in methods:
            method_name = str(method or "").strip().upper()
            if not method_name or not endpoint_path:
                continue
            result.append(
                EndpointTestContract(
                    method=method_name,
                    path=endpoint_path,
                    endpoint_file=endpoint_file,
                    success_case_required=success_case_required,
                    internal_calls=internal_calls,
                    declared_errors=declared_errors,
                    request=request,
                    response=response,
                    external_calls_forbidden=True,
                    test_kinds=test_kinds,
                )
            )

    return result


def build_test_plan_debug_artifact(
    *,
    contracts: List[EndpointTestContract],
    tests_generated: int,
    tests_executed: int,
    skip_reason: str | None = None,
) -> Dict[str, Any]:
    tests_planned: List[Dict[str, Any]] = []
    for contract in contracts:
        endpoint_name = f"{contract.method} {contract.path}"
        for item in contract.test_kinds:
            planned = {"endpoint": endpoint_name, **dict(item)}
            tests_planned.append(planned)

    return {
        "tests_planned": tests_planned,
        "tests_generated": int(tests_generated),
        "tests_executed": int(tests_executed),
        "skip_reason": str(skip_reason or ""),
    }


def _resolve_endpoint_file(
    *,
    endpoint: Dict[str, Any],
    contracts_by_path: Dict[str, Dict[str, Any]],
) -> str:
    endpoint_path = str(endpoint.get("path") or "").strip()
    methods = endpoint.get("methods") or endpoint.get("method") or []
    if isinstance(methods, str):
        methods = [methods]
    normalized_methods = {str(item or "").strip().upper() for item in methods if str(item or "").strip()}

    for path, contract in contracts_by_path.items():
        if str(contract.get("kind") or "").strip() != "endpoint":
            continue
        endpoint_meta = contract.get("endpoint") or {}
        contract_path = str(endpoint_meta.get("path") or "").strip()
        contract_methods = endpoint_meta.get("methods") or endpoint_meta.get("method") or []
        if isinstance(contract_methods, str):
            contract_methods = [contract_methods]
        normalized_contract_methods = {
            str(item or "").strip().upper()
            for item in contract_methods
            if str(item or "").strip()
        }
        if contract_path == endpoint_path and normalized_contract_methods == normalized_methods:
            return path

    return ""


def _normalize_declared_errors(errors: List[Any]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in errors or []:
        if isinstance(item, dict):
            normalized.append(dict(item))
            continue
        if isinstance(item, int):
            normalized.append({"status": item})
    return normalized


def _endpoint_integration_refs(
    endpoint: Dict[str, Any],
    file_contract: Dict[str, Any],
) -> List[str]:
    refs = endpoint.get("integration_refs")
    if refs is None:
        refs = file_contract.get("integration_refs") or []
    return [str(item).strip() for item in refs or [] if str(item).strip()]


def _build_test_kinds(
    *,
    integration_refs: List[str],
    declared_errors: List[Dict[str, Any]],
    request: Dict[str, Any],
    response: Dict[str, Any],
) -> List[Dict[str, Any]]:
    planned: List[Dict[str, Any]] = []

    if integration_refs:
        planned.append({"kind": "mocked_success"})
        for error in declared_errors:
            status = error.get("status")
            if isinstance(status, int):
                planned.append({"kind": "mocked_error", "status": status})
        if request:
            planned.append({"kind": "request_body_validation"})
    else:
        planned.append({"kind": "direct_functional_test"})
        if response:
            planned.append({"kind": "deterministic_response_assertion"})

    return planned
