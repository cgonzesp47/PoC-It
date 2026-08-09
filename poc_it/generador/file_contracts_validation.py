from __future__ import annotations

import ast
import builtins
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple

from poc_it.generador.utils_python_names import (
    module_path_from_file_path,
    safe_python_identifier,
)
from poc_it.materializacion.codegen.ast_utils import (
    contract_parameter_names,
    required_contract_parameters,
    resolve_call_arguments,
)


@dataclass(frozen=True)
class FileContractValidationError:
    code: str
    path: str
    message: str
    details: Dict[str, Any] | None = None


@dataclass(frozen=True)
class ActualFunctionSignature:
    module: str
    symbol: str
    positional_required: int
    positional_total: int
    keyword_only_required: Tuple[str, ...]
    parameter_names: Tuple[str, ...]
    has_varargs: bool = False
    has_varkwargs: bool = False


@dataclass(frozen=True)
class ConfigurationFieldAccess:
    object_name: str
    field_name: str
    line: int | None


@dataclass(frozen=True)
class ValueShape:
    kind: str
    fields: dict[str, str] | None = None


@dataclass(frozen=True)
class SensitiveLiteralAssignment:
    name: str
    value: str
    line: int | None


def validate_file_contracts(
    *,
    spec: Dict[str, Any],
    implementation_contracts: List[Dict[str, Any]],
    file_contracts: List[Dict[str, Any]],
) -> List[FileContractValidationError]:
    errors: List[FileContractValidationError] = []

    spec = spec if isinstance(spec, dict) else {}
    implementation_contracts = (
        implementation_contracts if isinstance(implementation_contracts, list) else []
    )
    file_contracts = file_contracts if isinstance(file_contracts, list) else []

    files = [
        str(path or "").replace("\\", "/")
        for path in (spec.get("files") or [])
        if str(path or "").strip()
    ]
    planned_paths = set(files)

    path_counts: Dict[str, int] = {}
    contracts_by_path: Dict[str, List[Dict[str, Any]]] = {}
    for contract in file_contracts:
        if not isinstance(contract, dict):
            continue
        path = str(contract.get("path") or "").replace("\\", "/")
        if not path:
            continue
        path_counts[path] = path_counts.get(path, 0) + 1
        contracts_by_path.setdefault(path, []).append(contract)

    for path in files:
        if path not in contracts_by_path:
            errors.append(
                FileContractValidationError(
                    code="FILE_CONTRACT_MISSING",
                    path=path,
                    message="Planned file is missing its File Contract.",
                )
            )

    for path, count in path_counts.items():
        if count > 1:
            errors.append(
                FileContractValidationError(
                    code="FILE_CONTRACT_DUPLICATED",
                    path=path,
                    message="More than one File Contract exists for the same path.",
                )
            )

    unique_contracts = [items[0] for items in contracts_by_path.values() if items]

    endpoint_contracts = [
        contract
        for contract in unique_contracts
        if str(contract.get("kind") or "").strip() == "endpoint"
    ]
    integration_contracts = [
        contract
        for contract in unique_contracts
        if str(contract.get("kind") or "").strip() == "integration"
    ]
    config_contracts = [
        contract
        for contract in unique_contracts
        if str(contract.get("kind") or "").strip() == "config"
    ]
    requirements_contracts = [
        contract
        for contract in unique_contracts
        if str(contract.get("kind") or "").strip() == "requirements"
    ]

    endpoint_owner_keys: Set[Tuple[str, str]] = set()
    endpoint_keys_by_path: Dict[str, Set[Tuple[str, str]]] = {}
    for contract in endpoint_contracts:
        keys = {
            _endpoint_key(item.get("method"), item.get("path"))
            for item in (contract.get("endpoints") or [])
            if isinstance(item, dict)
        }
        keys = {key for key in keys if key[0] and key[1]}
        endpoint_owner_keys.update(keys)
        endpoint_keys_by_path[str(contract.get("path") or "").replace("\\", "/")] = keys

    for endpoint in spec.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        key = _endpoint_key(endpoint.get("method"), endpoint.get("path"))
        if not key[0] or not key[1]:
            continue
        if key not in endpoint_owner_keys:
            errors.append(
                FileContractValidationError(
                    code="FILE_ENDPOINT_OWNER_MISSING",
                    path=str(endpoint.get("file") or ""),
                    message=f"Endpoint {key[0]} {key[1]} has no endpoint File Contract owner.",
                )
            )

    implementation_owner_count: Dict[Tuple[str, str, str], int] = {}
    for contract in endpoint_contracts:
        path = str(contract.get("path") or "").replace("\\", "/")
        endpoint_keys = endpoint_keys_by_path.get(path, set())
        for item in contract.get("implementation_contracts") or []:
            if not isinstance(item, dict):
                continue
            identity = _implementation_identity(item)
            if not identity:
                continue
            item_key = _endpoint_key(item.get("method"), item.get("path"))
            if item_key not in endpoint_keys:
                continue
            implementation_owner_count[identity] = (
                implementation_owner_count.get(identity, 0) + 1
            )

    for item in implementation_contracts:
        if not isinstance(item, dict):
            continue
        identity = _implementation_identity(item)
        if not identity:
            continue
        count = implementation_owner_count.get(identity, 0)
        if count != 1:
            errors.append(
                FileContractValidationError(
                    code="FILE_IMPLEMENTATION_OWNER_MISSING",
                    path=str(item.get("file") or ""),
                    message=(
                        f"Implementation Contract {identity[0]} {identity[1]} "
                        f"{identity[2]} must belong to exactly one endpoint File Contract."
                    ),
                )
            )

    required_action_keys = {
        _action_identity(action)
        for endpoint in (spec.get("endpoints") or [])
        if isinstance(endpoint, dict)
        for action in (endpoint.get("actions") or [])
        if isinstance(action, dict) and bool(action.get("required"))
    }
    required_action_keys.discard(None)
    file_action_keys = {
        _action_identity(action)
        for contract in unique_contracts
        for action in (contract.get("actions") or [])
        if isinstance(action, dict)
    }
    for action_key in sorted(required_action_keys):
        if action_key not in file_action_keys:
            errors.append(
                FileContractValidationError(
                    code="FILE_REQUIRED_ACTION_LOST",
                    path="",
                    message=f"Required action {action_key} is missing from File Contracts.",
                )
            )

    for endpoint in spec.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        path = str(endpoint.get("file") or "").replace("\\", "/")
        owners = contracts_by_path.get(path, [])
        owner = owners[0] if owners else None
        if not owner or str(owner.get("kind") or "").strip() != "endpoint":
            continue
        owner_errors = {
            _error_identity(item)
            for item in (owner.get("errors") or [])
            if isinstance(item, dict)
        }
        for error in endpoint.get("errors") or []:
            if not isinstance(error, dict) or not bool(error.get("required")):
                continue
            identity = _error_identity(error)
            if identity not in owner_errors:
                errors.append(
                    FileContractValidationError(
                        code="FILE_REQUIRED_ERROR_LOST",
                        path=path,
                        message=(
                            f"Required error {identity[0]}:{identity[1]} "
                            "is missing from its endpoint File Contract."
                        ),
                    )
                )

    implementation_files = spec.get("implementation_files") or []
    integration_module_by_ref: Dict[str, str] = {}
    for item in implementation_files:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "").strip() != "integration":
            continue
        ref = str(item.get("integration_ref") or "").strip()
        path = str(item.get("path") or "").replace("\\", "/")
        if ref and path:
            integration_module_by_ref[ref] = path

    inline_refs = {
        str(ref or "").strip()
        for contract in endpoint_contracts
        if bool(contract.get("inline_integration_owner"))
        for ref in (contract.get("integration_refs") or [])
        if str(ref or "").strip()
    }

    for endpoint in spec.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        owner_path = str(endpoint.get("file") or "").replace("\\", "/")
        for ref in endpoint.get("integration_refs") or []:
            ref_str = str(ref or "").strip()
            if not ref_str:
                continue
            if ref_str not in integration_module_by_ref and ref_str not in inline_refs:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTEGRATION_OWNER_MISSING",
                        path=owner_path,
                        message=(
                            f"Referenced integration {ref_str} has no dedicated module "
                            "and no explicit inline owner."
                        ),
                    )
                )

    config_keys_in_spec = {
        str(item.get("key") or "").strip()
        for item in (spec.get("configuration") or [])
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    config_keys_in_contracts = {
        str(item.get("key") or "").strip()
        for contract in config_contracts
        for item in (contract.get("configuration") or [])
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    consumer_config_keys = {
        str(item.get("key") or "").strip()
        for contract in unique_contracts
        if str(contract.get("kind") or "").strip() in {"endpoint", "integration"}
        for item in (contract.get("configuration") or [])
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    required_config_keys = {
        str(item.get("key") or "").strip()
        for item in (spec.get("configuration") or [])
        if isinstance(item, dict) and bool(item.get("required"))
    }
    for key in sorted(required_config_keys):
        if key not in config_keys_in_spec:
            continue
        if key not in config_keys_in_contracts or key not in consumer_config_keys:
            errors.append(
                FileContractValidationError(
                    code="FILE_CONFIGURATION_LOST",
                    path="app/core/config.py",
                    message=f"Required configuration {key} was not preserved in config and consumers.",
                )
            )

    requirements_dependencies: Set[str] = set()
    for contract in requirements_contracts:
        for dep in contract.get("dependencies") or []:
            dep_str = str(dep or "").strip()
            if dep_str:
                requirements_dependencies.add(dep_str)

    for integration in spec.get("integrations") or []:
        if not isinstance(integration, dict):
            continue
        for dep in _integration_dependencies(integration, spec):
            if dep not in requirements_dependencies:
                errors.append(
                    FileContractValidationError(
                        code="FILE_DEPENDENCY_LOST",
                        path="requirements.txt",
                        message=f"Dependency {dep} is required but missing from requirements File Contract.",
                    )
                )

    for contract in endpoint_contracts:
        path = str(contract.get("path") or "").replace("\\", "/")
        internal_refs = {
            str(item.get("path") or "").replace("\\", "/")
            for item in (contract.get("source", {}).get("implementation_files") or [])
            if isinstance(item, dict)
        }
        for ref in contract.get("integration_refs") or []:
            ref_str = str(ref or "").strip()
            target = integration_module_by_ref.get(ref_str)
            if target:
                internal_refs.add(target)
        for target in sorted(item for item in internal_refs if item):
            if target not in planned_paths:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERNAL_IMPORT_TARGET_MISSING",
                        path=path,
                        message=f"Internal module target {target} is missing from spec.files.",
                    )
                )

    for contract in endpoint_contracts + integration_contracts:
        path = str(contract.get("path") or "").replace("\\", "/")
        if not (contract.get("must_implement") or []):
            errors.append(
                FileContractValidationError(
                    code="FILE_MUST_IMPLEMENT_EMPTY",
                    path=path,
                    message="must_implement must not be empty for endpoint or integration contracts.",
                )
            )
        if not (contract.get("must_not") or []):
            errors.append(
                FileContractValidationError(
                    code="FILE_MUST_NOT_EMPTY",
                    path=path,
                    message="must_not must not be empty for endpoint or integration contracts.",
                )
            )

    degraded_markers = ("mock only", "fake success", "documentation only", "todo only")
    for contract in unique_contracts:
        levels = [
            str(item or "").strip()
            for item in (contract.get("implementation_levels") or [])
        ]
        if "integration_skeleton" not in levels:
            continue
        notes_blob = " || ".join(
            str(item or "")
            for item in (
                list(contract.get("notes") or [])
                + list(contract.get("implementation_plan") or [])
                + list(contract.get("must_implement") or [])
            )
        ).lower()
        if any(marker in notes_blob for marker in degraded_markers):
            errors.append(
                FileContractValidationError(
                    code="FILE_INTEGRATION_SKELETON_DEGRADED",
                    path=str(contract.get("path") or ""),
                    message="integration_skeleton contains degraded guidance.",
                )
            )

    for contract in endpoint_contracts:
        path = str(contract.get("path") or "").replace("\\", "/")
        endpoint_items = [
            item for item in (contract.get("endpoints") or []) if isinstance(item, dict)
        ]
        if len(endpoint_items) != 1:
            continue
        endpoint = endpoint_items[0]
        endpoint_path = str(endpoint.get("path") or "").strip()
        if endpoint_path != "/health":
            continue
        refs = contract.get("integration_refs") or []
        if refs:
            errors.append(
                FileContractValidationError(
                    code="FILE_HEALTH_EXTERNAL_DEPENDENCY",
                    path=path,
                    message="/health without references cannot receive external dependencies.",
                )
            )

    provided_interfaces_by_path = _provided_interfaces_by_path(unique_contracts)
    provided_interfaces_by_ref = _provided_interfaces_by_ref(unique_contracts)

    for contract in unique_contracts:
        path = str(contract.get("path") or "").replace("\\", "/")
        endpoint_contract_items = [
            item
            for item in (contract.get("endpoint_contracts") or [])
            if isinstance(item, dict)
        ]
        for endpoint_contract in endpoint_contract_items:
            endpoint_path = str(endpoint_contract.get("path") or "").strip()
            if not endpoint_path:
                errors.append(
                    FileContractValidationError(
                        code="ROUTE_PATH_MISMATCH",
                        path=path,
                        message="Endpoint contract must preserve the declared path.",
                    )
                )
            request_type = str(endpoint_contract.get("request_type") or "").strip().lower()
            if not request_type:
                errors.append(
                    FileContractValidationError(
                        code="HTTP_REQUEST_CONTRACT_MISMATCH",
                        path=path,
                        message="Endpoint contract must preserve request_type.",
                    )
                )

    for contract in unique_contracts:
        path = str(contract.get("path") or "").replace("\\", "/")
        for provided_interface in contract.get("provided_interfaces") or []:
            if not isinstance(provided_interface, dict):
                continue
            symbol = str(provided_interface.get("symbol") or "").strip()
            expected = safe_python_identifier(symbol, fallback="execute_action")
            if not symbol or symbol != expected:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERFACE_SYMBOL_INVALID",
                        path=path,
                        message=f"Provided interface symbol is not a safe deterministic Python identifier: {symbol!r}.",
                    )
                )

    for endpoint in spec.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        endpoint_path = str(endpoint.get("file") or "").replace("\\", "/").strip()
        for action in endpoint.get("actions") or []:
            if not isinstance(action, dict) or not bool(action.get("required", True)):
                continue
            action_id = str(action.get("id") or "").strip()
            owners = _candidate_owner_paths_for_action(
                action=action,
                endpoint_file=endpoint_path,
                integration_path_by_ref=integration_module_by_ref,
                planned_paths=files,
            )
            if not owners:
                errors.append(
                    FileContractValidationError(
                        code="FILE_ACTION_OWNER_MISSING",
                        path=endpoint_path,
                        message=f"Required action {action_id} has no owner path.",
                    )
                )

    for contract in unique_contracts:
        path = str(contract.get("path") or "").replace("\\", "/")
        local_symbols: Dict[str, str] = {}
        for provided_interface in contract.get("provided_interfaces") or []:
            if not isinstance(provided_interface, dict):
                continue
            symbol = str(provided_interface.get("symbol") or "").strip()
            action_ref = str(provided_interface.get("action_ref") or "").strip()
            if not symbol:
                continue
            if symbol in local_symbols and local_symbols[symbol] != action_ref:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERFACE_SYMBOL_COLLISION",
                        path=path,
                        message=f"Provided interface symbol {symbol} is declared for multiple actions.",
                    )
                )
            else:
                local_symbols[symbol] = action_ref

    for contract in unique_contracts:
        path = str(contract.get("path") or "").replace("\\", "/")
        for internal_call in contract.get("required_internal_calls") or []:
            if not isinstance(internal_call, dict):
                continue
            module = str(internal_call.get("module") or "").strip()
            symbol = str(internal_call.get("symbol") or "").strip()
            action_ref = str(internal_call.get("action_ref") or "").strip()
            interface_ref = str(internal_call.get("interface_ref") or "").strip()

            if not module:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERNAL_CALL_TARGET_MISSING",
                        path=path,
                        message=f"Internal call for symbol {symbol!r} has no target module.",
                    )
                )
                continue

            target_path = _path_from_module(module)
            if target_path not in planned_paths:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERNAL_CALL_TARGET_MISSING",
                        path=path,
                        message=f"Internal call target {module} is missing from spec.files.",
                    )
                )
                continue

            target_contracts = contracts_by_path.get(target_path, [])
            if not target_contracts:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERNAL_CALL_TARGET_MISSING",
                        path=path,
                        message=f"Internal call target {module} has no File Contract.",
                    )
                )
                continue

            target_contract = target_contracts[0]
            target_interfaces = [
                item
                for item in (target_contract.get("provided_interfaces") or [])
                if isinstance(item, dict)
            ]
            matching_symbol = next(
                (
                    item
                    for item in target_interfaces
                    if str(item.get("symbol") or "").strip() == symbol
                ),
                None,
            )
            if matching_symbol is None:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERNAL_CALL_SYMBOL_MISSING",
                        path=path,
                        message=f"Internal call {module}.{symbol} is missing from provider interfaces.",
                    )
                )
                continue

            if interface_ref and interface_ref not in provided_interfaces_by_ref:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERNAL_CALL_SYMBOL_MISSING",
                        path=path,
                        message=f"Internal call interface_ref {interface_ref!r} does not resolve to any provider interface.",
                    )
                )
            elif interface_ref and str(matching_symbol.get("interface_ref") or "").strip() != interface_ref:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERNAL_CALL_ACTION_MISMATCH",
                        path=path,
                        message=f"Internal call {module}.{symbol} points to interface {interface_ref} but provider maps it to {matching_symbol.get('interface_ref')}.",
                    )
                )

            if str(matching_symbol.get("action_ref") or "").strip() != action_ref:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERNAL_CALL_ACTION_MISMATCH",
                        path=path,
                        message=f"Internal call {module}.{symbol} points to action {action_ref} but provider maps it to {matching_symbol.get('action_ref')}.",
                    )
                )

    for contract in unique_contracts:
        path = str(contract.get("path") or "").replace("\\", "/")
        for internal_call in contract.get("required_internal_calls") or []:
            if not isinstance(internal_call, dict):
                continue
            module = str(internal_call.get("module") or "").strip()
            symbol = str(internal_call.get("symbol") or "").strip()
            if not module or not symbol:
                continue
            target_path = _path_from_module(module)
            if target_path not in provided_interfaces_by_path:
                errors.append(
                    FileContractValidationError(
                        code="FILE_INTERNAL_CALL_SYMBOL_MISSING",
                        path=path,
                        message=f"Internal call target {module}.{symbol} has no provided interfaces entry.",
                    )
                )

    for contract in unique_contracts:
        kind = str(contract.get("kind") or "").strip()
        if kind != "router":
            continue
        path = str(contract.get("path") or "").replace("\\", "/")
        included_routers = [
            item for item in (contract.get("included_routers") or []) if isinstance(item, dict)
        ]
        convention = contract.get("routing_convention") or {}
        if bool(convention.get("endpoint_owns_full_path")):
            for item in included_routers:
                prefix = str(item.get("prefix") or "").strip()
                if prefix:
                    errors.append(
                        FileContractValidationError(
                            code="ROUTE_PATH_MISMATCH",
                            path=path,
                            message="Router contract uses a non-empty prefix while endpoint contracts own the full path.",
                        )
                    )

    errors.extend(validate_internal_data_contracts(file_contracts=unique_contracts))

    for path in planned_paths:
        if path not in files:
            errors.append(
                FileContractValidationError(
                    code="FILE_INTERNAL_CALL_TARGET_MISSING",
                    path=path,
                    message=f"Planned path {path} is not present in spec.files.",
                )
            )

    return errors


def validate_internal_data_contracts(
    *,
    file_contracts: Sequence[Mapping[str, Any]],
) -> List[FileContractValidationError]:
    errors: List[FileContractValidationError] = []
    interfaces = _index_provided_interfaces(file_contracts)

    for contract in file_contracts:
        if not isinstance(contract, Mapping):
            continue
        consumer_path = str(contract.get("path") or "").replace("\\", "/").strip()
        for flow in contract.get("data_flows") or []:
            if not isinstance(flow, Mapping):
                continue

            source = flow.get("source") or {}
            target = flow.get("target") or {}
            if not isinstance(source, Mapping) or not isinstance(target, Mapping):
                continue

            source_ref = str(source.get("interface_ref") or "").strip()
            target_ref = str(target.get("interface_ref") or "").strip()
            parameter_name = str(target.get("parameter") or "").strip()
            if not source_ref or not target_ref or not parameter_name:
                continue

            source_interface = interfaces.get(source_ref)
            target_interface = interfaces.get(target_ref)
            if not isinstance(source_interface, Mapping) or not isinstance(target_interface, Mapping):
                continue

            actual_shape = _shape_from_return_spec(source_interface.get("returns"))
            target_parameter = _find_interface_parameter(target_interface, parameter_name)
            if not isinstance(target_parameter, Mapping):
                continue

            expected_type = _parameter_expected_type(
                target_parameter,
                data_contracts={},
            )
            if not expected_type:
                continue

            compatibility = _shapes_are_compatible(actual_shape, expected_type)
            if compatibility is False:
                errors.append(
                    FileContractValidationError(
                        code="FILE_CONTRACT_PRE_CODEGEN_TYPE_MISMATCH",
                        path=consumer_path,
                        message=(
                            f"Explicit data flow {source_ref!r} produces an incompatible value "
                            f"for {target_ref!r} parameter {parameter_name!r}."
                        ),
                    )
                )

    return errors


def _endpoint_key(method: Any, path: Any) -> Tuple[str, str]:
    return (str(method or "").strip().upper(), str(path or "").strip())


def _implementation_identity(item: Mapping[str, Any]) -> Tuple[str, str, str] | None:
    method = str(item.get("method") or "").strip().upper()
    path = str(item.get("path") or "").strip()
    capability = str(item.get("capability") or "").strip()
    if not method or not path or not capability:
        return None
    return (method, path, capability)


def _action_identity(item: Mapping[str, Any]) -> str | None:
    action_id = str(item.get("id") or "").strip()
    if not action_id:
        return None
    return action_id


def _error_identity(item: Mapping[str, Any]) -> Tuple[str, str]:
    return (str(item.get("status_code")), str(item.get("code") or "").strip())


def _string_values(value: object) -> List[str]:
    if value is None:
        return []

    if isinstance(value, (str, bytes)):
        text = str(value).strip()
        return [text] if text else []

    if not isinstance(value, Iterable):
        return []

    result: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _integration_dependencies(
    integration: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> List[str]:
    deps: List[str] = []

    deps.extend(_string_values(integration.get("packages")))

    refs = {
        str(ref or "").strip()
        for ref in _string_values(integration.get("technology_refs"))
        if str(ref or "").strip()
    }
    for signal in spec.get("technology_signals") or []:
        if not isinstance(signal, Mapping):
            continue

        signal_id = str(signal.get("id") or "").strip()
        if refs and signal_id not in refs:
            continue

        for package in _string_values(signal.get("packages")):
            deps.append(package)

    result: List[str] = []
    seen: Set[str] = set()
    for dep in deps:
        identity = dep.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        result.append(dep)
    return result


def _index_provided_interfaces(
    contracts: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for contract in contracts:
        if not isinstance(contract, Mapping):
            continue
        module_name = str(contract.get("module") or contract.get("module_name") or "").strip()
        if not module_name:
            path = str(contract.get("path") or "").replace("\\", "/").strip()
            if not path.endswith(".py"):
                continue
            module_name = module_path_from_file_path(path)
        if not module_name:
            continue
        for interface in contract.get("provided_interfaces") or []:
            if not isinstance(interface, Mapping):
                continue
            symbol = str(interface.get("symbol") or "").strip()
            if not symbol:
                continue
            result[f"{module_name}:{symbol}"] = dict(interface)
    return result


def _find_interface_parameter(
    interface: Mapping[str, Any],
    parameter_name: str,
) -> Dict[str, Any] | None:
    wanted = str(parameter_name or "").strip()
    if not wanted:
        return None
    for parameter in interface.get("parameters") or []:
        if not isinstance(parameter, Mapping):
            continue
        name = str(parameter.get("name") or "").strip()
        if name == wanted:
            return dict(parameter)
    return None


def _provided_interfaces_by_path(
    contracts: Sequence[Mapping[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {}
    for contract in contracts:
        path = str(contract.get("path") or "").replace("\\", "/").strip()
        if not path:
            continue
        result[path] = [
            dict(item)
            for item in (contract.get("provided_interfaces") or [])
            if isinstance(item, dict)
        ]
    return result


def _provided_interfaces_by_ref(
    contracts: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for contract in contracts:
        for item in (contract.get("provided_interfaces") or []):
            if not isinstance(item, dict):
                continue
            interface_ref = str(item.get("interface_ref") or "").strip()
            if interface_ref:
                result[interface_ref] = dict(item)
    return result


def _candidate_owner_paths_for_action(
    *,
    action: Mapping[str, Any],
    endpoint_file: str,
    integration_path_by_ref: Mapping[str, str],
    planned_paths: Sequence[str],
) -> List[str]:
    kind = str(action.get("kind") or "").strip()
    integration_ref = str(action.get("integration_ref") or "").strip()
    repository_paths = [
        str(path or "").replace("\\", "/").strip()
        for path in planned_paths
        if str(path or "").replace("\\", "/").startswith("app/repositories/")
    ]

    if kind in {"external_call", "notification"}:
        integration_path = integration_path_by_ref.get(integration_ref)
        if integration_path:
            return [integration_path]

    if kind == "persistence" and repository_paths:
        return [sorted(repository_paths)[0]]

    if endpoint_file:
        return [endpoint_file]

    return []


def _path_from_module(module: str) -> str:
    normalized = str(module or "").strip()
    if not normalized:
        return ""
    if normalized.endswith(".__init__"):
        normalized = normalized[: -len(".__init__")]
    return normalized.replace(".", "/") + ".py"


def _package_init_path_from_module(module: str) -> str:
    normalized = str(module or "").strip()
    if not normalized:
        return ""
    if normalized.endswith(".__init__"):
        normalized = normalized[: -len(".__init__")]
    return normalized.replace(".", "/") + "/__init__.py"


def _module_exists_in_generated_project(
    module_name: str,
    *,
    files_by_path: Mapping[str, str],
    contracts_by_path: Mapping[str, Mapping[str, Any]],
) -> bool:
    del contracts_by_path
    module_path = _path_from_module(module_name)
    package_init_path = _package_init_path_from_module(module_name)
    return module_path in files_by_path or package_init_path in files_by_path


def _internal_symbol_exists(
    *,
    module_name: str,
    symbol_name: str,
    files_by_path: Mapping[str, str],
    contracts_by_path: Mapping[str, Mapping[str, Any]],
    exports_by_module: Mapping[str, Set[str]],
) -> bool:
    if not module_name or not symbol_name:
        return False

    available = exports_by_module.get(module_name)
    if available is not None and symbol_name in available:
        return True

    target_path = _path_from_module(module_name)
    target_source = files_by_path.get(target_path)
    if isinstance(target_source, str):
        try:
            return symbol_name in _extract_module_exports(target_source)
        except SyntaxError:
            return False

    package_init_path = _package_init_path_from_module(module_name)
    package_source = files_by_path.get(package_init_path)
    if isinstance(package_source, str):
        try:
            if symbol_name in _extract_module_exports(package_source):
                return True
        except SyntaxError:
            return False

    child_module = f"{module_name}.{symbol_name}"
    if _module_exists_in_generated_project(
        child_module,
        files_by_path=files_by_path,
        contracts_by_path=contracts_by_path,
    ):
        return True

    return False


def _extract_function_signatures(
    *,
    module: str,
    source: str,
) -> Dict[str, ActualFunctionSignature]:
    tree = ast.parse(source)
    result: Dict[str, ActualFunctionSignature] = {}

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        args = node.args
        positional = [*args.posonlyargs, *args.args]
        defaults_count = len(args.defaults)
        required_count = max(0, len(positional) - defaults_count)
        required_kwonly = tuple(
            arg.arg
            for arg, default in zip(args.kwonlyargs, args.kw_defaults)
            if default is None
        )

        result[node.name] = ActualFunctionSignature(
            module=module,
            symbol=node.name,
            positional_required=required_count,
            positional_total=len(positional),
            keyword_only_required=required_kwonly,
            parameter_names=tuple(arg.arg for arg in positional),
            has_varargs=args.vararg is not None,
            has_varkwargs=args.kwarg is not None,
        )

    return result


def _extract_module_exports(
    source: str,
) -> Set[str]:
    tree = ast.parse(source)
    exports: Set[str] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            exports.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    exports.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                exports.add(node.target.id)

    return exports


def _extract_calls(source: str) -> List[ast.Call]:
    tree = ast.parse(source)
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _extract_import_aliases(
    source: str,
) -> Tuple[Dict[str, Tuple[str, str | None]], Dict[str, str]]:
    tree = ast.parse(source)
    symbol_imports: Dict[str, Tuple[str, str | None]] = {}
    module_imports: Dict[str, str] = {}

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = str(node.module or "").strip()
            for alias in node.names:
                local_name = alias.asname or alias.name
                imported_name: str | None = None if alias.name == "*" else alias.name
                symbol_imports[local_name] = (module, imported_name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name
                module_imports[local_name] = alias.name

    return symbol_imports, module_imports


def _resolve_called_symbol(
    call: ast.Call,
    *,
    symbol_imports: Mapping[str, Tuple[str, str | None]],
    module_imports: Mapping[str, str],
) -> Tuple[str, str] | None:
    func = call.func
    if isinstance(func, ast.Name):
        resolved = symbol_imports.get(func.id)
        if resolved is not None and resolved[1]:
            return (resolved[0], resolved[1])
        return None
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module_name = module_imports.get(func.value.id)
        if module_name:
            return (module_name, func.attr)
    return None


def _call_is_compatible(
    call: ast.Call,
    signature: ActualFunctionSignature,
) -> bool:
    positional_count = len(call.args)
    keyword_names = {
        keyword.arg
        for keyword in call.keywords
        if keyword.arg is not None
    }

    if not signature.has_varargs and positional_count > signature.positional_total:
        return False

    positional_parameter_names = signature.parameter_names
    required_parameter_names = positional_parameter_names[: signature.positional_required]

    if positional_count > len(required_parameter_names):
        satisfied_required = set(required_parameter_names)
    else:
        satisfied_required = set(required_parameter_names[:positional_count])

    for required_name in required_parameter_names[positional_count:]:
        if required_name not in keyword_names:
            return False
        satisfied_required.add(required_name)

    for required_name in signature.keyword_only_required:
        if required_name not in keyword_names:
            return False

    if signature.has_varkwargs:
        return True

    for keyword_name in keyword_names:
        if keyword_name not in positional_parameter_names and keyword_name not in signature.keyword_only_required:
            return False

    duplicated_by_keyword = keyword_names.intersection(positional_parameter_names[:positional_count])
    if duplicated_by_keyword:
        return False

    return True


def validate_generated_python_against_file_contracts(
    *,
    files_by_path: Mapping[str, str],
    file_contracts: Sequence[Mapping[str, Any]],
) -> List[FileContractValidationError]:
    errors: List[FileContractValidationError] = []
    contracts_by_path = {
        str(contract.get("path") or "").replace("\\", "/"): contract
        for contract in file_contracts
        if isinstance(contract, Mapping)
    }
    exports_by_module: Dict[str, Set[str]] = {}
    signatures_by_module: Dict[str, Dict[str, ActualFunctionSignature]] = {}
    shapes_by_module_symbol: Dict[Tuple[str, str], ValueShape] = {}

    for path, source in files_by_path.items():
        if not path.endswith(".py"):
            continue
        module = module_path_from_file_path(path)
        try:
            exports_by_module[module] = _extract_module_exports(source)
            signatures_by_module[module] = _extract_function_signatures(
                module=module,
                source=source,
            )
        except SyntaxError:
            continue

    for contract in file_contracts:
        if not isinstance(contract, Mapping):
            continue
        path = str(contract.get("path") or "").replace("\\", "/").strip()
        if not path.endswith(".py"):
            continue
        module = module_path_from_file_path(path)
        for provided_interface in contract.get("provided_interfaces") or []:
            if not isinstance(provided_interface, Mapping):
                continue
            symbol = str(provided_interface.get("symbol") or "").strip()
            if not symbol:
                continue
            shapes_by_module_symbol[(module, symbol)] = _shape_from_return_spec(
                provided_interface.get("returns")
            )

    for path, contract in contracts_by_path.items():
        source = files_by_path.get(path)
        if not isinstance(source, str) or not path.endswith(".py"):
            continue
        try:
            symbol_imports, module_imports = _extract_import_aliases(source)
        except SyntaxError:
            continue

        required_internal_refs = {
            (
                str(item.get("module") or "").strip(),
                str(item.get("symbol") or "").strip(),
            )
            for item in (contract.get("required_internal_calls") or [])
            if isinstance(item, dict)
        }

        for local_name, (module_name, original_name) in symbol_imports.items():
            if not module_name.startswith("app."):
                continue
            if original_name is None:
                continue
            if not _module_exists_in_generated_project(
                module_name,
                files_by_path=files_by_path,
                contracts_by_path=contracts_by_path,
            ):
                continue
            if (
                not _internal_symbol_exists(
                    module_name=module_name,
                    symbol_name=original_name,
                    files_by_path=files_by_path,
                    contracts_by_path=contracts_by_path,
                    exports_by_module=exports_by_module,
                )
                and (module_name, original_name) not in required_internal_refs
            ):
                errors.append(
                    FileContractValidationError(
                        code="INTERNAL_IMPORT_SYMBOL_MISSING",
                        path=path,
                        message=(
                            f"Imported symbol {original_name!r} does not exist in internal module "
                            f"{module_name!r}."
                        ),
                    )
                )

        errors.extend(
            _validate_configuration_access(
                path=path,
                source=source,
                contract=contract,
            )
        )
        errors.extend(
            _validate_internal_call_contracts(
                path=path,
                source=source,
                contract=contract,
                symbol_imports=symbol_imports,
                module_imports=module_imports,
                signatures_by_module=signatures_by_module,
                shapes_by_module_symbol=shapes_by_module_symbol,
            )
        )
        errors.extend(
            _validate_no_embedded_configuration_secrets(
                path=path,
                source=source,
                contract=contract,
            )
        )
        errors.extend(
            _validate_unresolved_python_symbols(
                path=path,
                source=source,
            )
        )

    return errors


def _validate_configuration_access(
    *,
    path: str,
    source: str,
    contract: Mapping[str, Any],
) -> List[FileContractValidationError]:
    configuration_access = contract.get("configuration_access") or {}
    provider_module = str(configuration_access.get("provider_module") or "").strip()
    provider_symbol = str(configuration_access.get("provider_symbol") or "").strip()
    allowed_fields = {
        str(field).strip()
        for field in (configuration_access.get("allowed_fields") or [])
        if str(field).strip()
    }
    if not provider_module or not provider_symbol or not allowed_fields:
        return []

    bindings = _extract_provider_bindings(source, provider_symbol=provider_symbol)
    if not bindings:
        return []

    accesses = _extract_attribute_accesses(source)
    errors: List[FileContractValidationError] = []
    for access in accesses:
        if access.object_name not in bindings:
            continue
        if access.field_name in allowed_fields:
            continue
        line_suffix = f" at line {access.line}" if access.line is not None else ""
        errors.append(
            FileContractValidationError(
                code="CONFIGURATION_FIELD_MISSING",
                path=path,
                message=(
                    "Generated code references configuration field "
                    f"{access.field_name!r} not allowed by FileContract{line_suffix}."
                ),
                details={
                    "field": access.field_name,
                    "allowed_fields": sorted(allowed_fields),
                    "line": access.line,
                },
            )
        )
    return errors


def _validate_internal_call_contracts(
    *,
    path: str,
    source: str,
    contract: Mapping[str, Any],
    symbol_imports: Mapping[str, Tuple[str, str | None]],
    module_imports: Mapping[str, str],
    signatures_by_module: Mapping[str, Dict[str, ActualFunctionSignature]],
    shapes_by_module_symbol: Mapping[Tuple[str, str], ValueShape],
) -> List[FileContractValidationError]:
    errors: List[FileContractValidationError] = []
    calls = _extract_calls(source)
    tree = ast.parse(source)

    for internal_call in contract.get("required_internal_calls") or []:
        if not isinstance(internal_call, dict):
            continue
        module_name = str(internal_call.get("module") or "").strip()
        symbol_name = str(internal_call.get("symbol") or "").strip()
        interface_ref = str(internal_call.get("interface_ref") or "").strip()
        if interface_ref and ":" in interface_ref:
            module_name, symbol_name = interface_ref.split(":", 1)

        if module_name not in signatures_by_module:
            continue

        actual_signatures = signatures_by_module[module_name]
        if symbol_name not in actual_signatures:
            errors.append(
                FileContractValidationError(
                    code="FILE_CONTRACT_SYMBOL_MISSING",
                    path=path,
                    message=f"Required symbol {symbol_name!r} not provided by {module_name!r}.",
                )
            )
            continue

        matching_calls = [
            call
            for call in calls
            if _resolve_called_symbol(
                call,
                symbol_imports=symbol_imports,
                module_imports=module_imports,
            )
            == (module_name, symbol_name)
        ]
        if not matching_calls:
            errors.append(
                FileContractValidationError(
                    code="FILE_CONTRACT_SYMBOL_MISSING",
                    path=path,
                    message=f"Required internal interface {module_name}.{symbol_name} is never called by the consumer.",
                )
            )
            continue

        contract_parameters = [
            item
            for item in (internal_call.get("parameters") or [])
            if isinstance(item, dict)
        ]
        ordered_parameter_names = contract_parameter_names(contract_parameters)
        required_parameter_names = required_contract_parameters(contract_parameters)
        arguments_ref = str(internal_call.get("arguments_ref") or "").strip()
        expected_parameters = {
            str(item.get("name") or "").strip(): _parameter_expected_type(
                item,
                data_contracts=dict(contract.get("data_contracts") or {}),
            )
            for item in contract_parameters
            if str(item.get("name") or "").strip()
        }
        provider_shape = shapes_by_module_symbol.get(
            (module_name, symbol_name),
            ValueShape(kind="unknown"),
        )

        for call in matching_calls:
            resolved_arguments = resolve_call_arguments(
                call,
                parameter_names=ordered_parameter_names,
            )
            passed_argument_names = set(resolved_arguments.provided_parameter_names)
            unresolved_by_star = (
                (resolved_arguments.has_star_args or resolved_arguments.has_star_kwargs)
                and required_parameter_names - passed_argument_names
            )
            if not _call_is_compatible(call, actual_signatures[symbol_name]):
                errors.append(
                    FileContractValidationError(
                        code="FILE_CONTRACT_CALL_SIGNATURE_MISMATCH",
                        path=path,
                        message=(
                            f"Call to {module_name}.{symbol_name} does not match the actual "
                            "callee signature."
                        ),
                        details={
                            "module": module_name,
                            "symbol": symbol_name,
                            "interface_ref": interface_ref or None,
                            "expected_signature": {
                                "module": actual_signatures[symbol_name].module,
                                "symbol": actual_signatures[symbol_name].symbol,
                                "positional_required": actual_signatures[symbol_name].positional_required,
                                "positional_total": actual_signatures[symbol_name].positional_total,
                                "keyword_only_required": list(
                                    actual_signatures[symbol_name].keyword_only_required
                                ),
                                "parameter_names": list(
                                    actual_signatures[symbol_name].parameter_names
                                ),
                                "has_varargs": actual_signatures[symbol_name].has_varargs,
                                "has_varkwargs": actual_signatures[symbol_name].has_varkwargs,
                            },
                            "actual_call": {
                                "positional_count": len(call.args),
                                "keyword_names": sorted(
                                    keyword.arg
                                    for keyword in call.keywords
                                    if keyword.arg is not None
                                ),
                                "has_star_args": any(
                                    isinstance(argument, ast.Starred) for argument in call.args
                                ),
                                "has_star_kwargs": any(
                                    keyword.arg is None for keyword in call.keywords
                                ),
                                "line": getattr(call, "lineno", None),
                            },
                            "contract_parameters": ordered_parameter_names,
                            "required_contract_parameters": sorted(required_parameter_names),
                        },
                    )
                )
            if (
                required_parameter_names
                and not required_parameter_names.issubset(passed_argument_names)
                and not unresolved_by_star
            ):
                missing = sorted(required_parameter_names - passed_argument_names)
                errors.append(
                    FileContractValidationError(
                        code="FILE_CONTRACT_DATA_FLOW_MISMATCH",
                        path=path,
                        message=(
                            f"Call to {module_name}.{symbol_name} does not provide required "
                            f"FileContract parameters {missing}."
                        ),
                    )
                )
            if arguments_ref:
                unpacked_names = _unpacked_mapping_argument_names(call)
                if unpacked_names and not required_parameter_names.issubset(unpacked_names):
                    errors.append(
                        FileContractValidationError(
                            code="FILE_CONTRACT_DATA_FLOW_MISMATCH",
                            path=path,
                            message=(
                                f"Call to {module_name}.{symbol_name} passes mapping data for "
                                f"{arguments_ref!r} without all required fields {sorted(required_parameter_names)}."
                            ),
                        )
                    )
                if not passed_argument_names and not unpacked_names and required_parameter_names:
                    errors.append(
                        FileContractValidationError(
                            code="FILE_CONTRACT_DATA_FLOW_MISMATCH",
                            path=path,
                            message=(
                                f"Call to {module_name}.{symbol_name} does not expose the "
                                f"required arguments for data contract {arguments_ref!r}."
                            ),
                        )
                    )

            local_bindings = _build_local_bindings(
                tree,
                provider_shapes=shapes_by_module_symbol,
                symbol_imports=symbol_imports,
            )
            for parameter_name, argument_node in resolved_arguments.arguments_by_parameter_name.items():
                expected_type = expected_parameters.get(parameter_name)
                if not expected_type:
                    continue
                actual_shape = _resolve_expression_shape(
                    argument_node,
                    bindings=local_bindings,
                    provider_shapes=shapes_by_module_symbol,
                    symbol_imports=symbol_imports,
                )
                if actual_shape.kind == "unknown" and provider_shape.kind != "unknown":
                    if isinstance(argument_node, ast.Name):
                        local_shape = local_bindings.get(argument_node.id)
                        if local_shape is None and provider_shape.kind != "unknown":
                            actual_shape = provider_shape
                compatibility = _shapes_are_compatible(actual_shape, expected_type)
                if compatibility is False:
                    errors.append(
                        FileContractValidationError(
                            code="FILE_CONTRACT_DATA_FLOW_TYPE_MISMATCH",
                            path=path,
                            message=(
                                f"Call to {module_name}.{symbol_name} provides {actual_shape.kind} "
                                f"for parameter {parameter_name!r}, expected {expected_type}."
                            ),
                        )
                    )

    return errors


def _extract_attribute_accesses(source: str) -> List[ConfigurationFieldAccess]:
    tree = ast.parse(source)
    result: List[ConfigurationFieldAccess] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        result.append(
            ConfigurationFieldAccess(
                object_name=node.value.id,
                field_name=node.attr,
                line=getattr(node, "lineno", None),
            )
        )
    return result


def _extract_provider_bindings(
    source: str,
    *,
    provider_symbol: str,
) -> Set[str]:
    tree = ast.parse(source)
    bindings: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == provider_symbol:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bindings.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == provider_symbol:
                bindings.add(node.target.id)

    return bindings


def _unpacked_mapping_argument_names(call: ast.Call) -> Set[str]:
    names: Set[str] = set()
    for keyword in call.keywords:
        if keyword.arg is not None:
            continue
        if isinstance(keyword.value, ast.Dict):
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    normalized = str(key.value).strip()
                    if normalized:
                        names.add(normalized)
    return names


def _literal_string_value(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _extract_literal_assignments(source: str) -> List[SensitiveLiteralAssignment]:
    tree = ast.parse(source)
    result: List[SensitiveLiteralAssignment] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = _literal_string_value(node.value)
            if value is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    result.append(
                        SensitiveLiteralAssignment(
                            name=target.id,
                            value=value,
                            line=getattr(node, "lineno", None),
                        )
                    )
        elif isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name):
                continue
            value = _literal_string_value(node.value)
            if value is None:
                continue
            result.append(
                SensitiveLiteralAssignment(
                    name=node.target.id,
                    value=value,
                    line=getattr(node, "lineno", None),
                )
            )

    return result


def _secret_configuration_keys(contract: Mapping[str, Any]) -> Set[str]:
    keys: Set[str] = set()
    for item in contract.get("configuration") or []:
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("secret")):
            continue
        key = str(item.get("key") or "").strip()
        if key:
            keys.add(key)
    return keys


def _validate_no_embedded_configuration_secrets(
    *,
    path: str,
    source: str,
    contract: Mapping[str, Any],
) -> List[FileContractValidationError]:
    secret_keys = _secret_configuration_keys(contract)
    if not secret_keys:
        return []

    errors: List[FileContractValidationError] = []
    for assignment in _extract_literal_assignments(source):
        if assignment.name not in secret_keys:
            continue
        line_suffix = f" at line {assignment.line}" if assignment.line is not None else ""
        errors.append(
            FileContractValidationError(
                code="EMBEDDED_SECRET_FORBIDDEN",
                path=path,
                message=(
                    f"Secret configuration {assignment.name!r} is assigned a literal value"
                    f"{line_suffix}."
                ),
            )
        )
    return errors


def _shape_from_return_spec(value: Any) -> ValueShape:
    if isinstance(value, str):
        normalized = str(value).strip().lower()
        return ValueShape(kind=normalized or "unknown")

    if not isinstance(value, Mapping):
        return ValueShape(kind="unknown")

    kind = str(value.get("kind") or value.get("type") or "").strip().lower() or "unknown"
    raw_fields = value.get("fields")
    fields: dict[str, str] | None = None
    if isinstance(raw_fields, Mapping):
        fields = {
            str(key).strip(): str(field_type).strip().lower()
            for key, field_type in raw_fields.items()
            if str(key).strip() and str(field_type).strip()
        }
    return ValueShape(kind=kind, fields=fields or None)


def _parameter_expected_type(
    parameter: Mapping[str, Any],
    *,
    data_contracts: Mapping[str, Any],
) -> str | None:
    param_type = str(parameter.get("type") or "").strip().lower()
    if param_type:
        return param_type

    ref = str(parameter.get("data_contract_ref") or parameter.get("arguments_ref") or "").strip()
    if not ref:
        return None

    contract = data_contracts.get(ref)
    if isinstance(contract, Mapping):
        normalized = str(contract.get("kind") or contract.get("type") or "").strip().lower()
        return normalized or None
    if isinstance(contract, str):
        normalized = str(contract).strip().lower()
        return normalized or None
    return None


def _extract_static_subscript_key(expression: ast.Subscript) -> str | None:
    slice_node = expression.slice
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        return str(slice_node.value)
    return None


def _build_local_bindings(
    tree: ast.Module,
    *,
    provider_shapes: Mapping[Tuple[str, str], ValueShape],
    symbol_imports: Mapping[str, Tuple[str, str | None]],
) -> dict[str, ValueShape]:
    bindings: dict[str, ValueShape] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            shape = _infer_shape_from_value(
                node.value,
                bindings=bindings,
                provider_shapes=provider_shapes,
                symbol_imports=symbol_imports,
            )
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = shape
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            shape = _infer_shape_from_value(
                node.value,
                bindings=bindings,
                provider_shapes=provider_shapes,
                symbol_imports=symbol_imports,
            )
            bindings[node.target.id] = shape

    return bindings


def _infer_shape_from_value(
    value: ast.AST | None,
    *,
    bindings: Mapping[str, ValueShape],
    provider_shapes: Mapping[Tuple[str, str], ValueShape],
    symbol_imports: Mapping[str, Tuple[str, str | None]],
) -> ValueShape:
    if value is None:
        return ValueShape(kind="unknown")
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Name):
            resolved = symbol_imports.get(func.id)
            if resolved is not None and resolved[1]:
                return provider_shapes.get(
                    (resolved[0], resolved[1]),
                    ValueShape(kind="unknown"),
                )
        return ValueShape(kind="unknown")
    if isinstance(value, ast.Constant):
        py_value = value.value
        if isinstance(py_value, str):
            return ValueShape(kind="string")
        if isinstance(py_value, bytes):
            return ValueShape(kind="bytes")
        if isinstance(py_value, bool):
            return ValueShape(kind="boolean")
        if isinstance(py_value, int) and not isinstance(py_value, bool):
            return ValueShape(kind="integer")
        if isinstance(py_value, float):
            return ValueShape(kind="number")
    if isinstance(value, ast.Dict):
        fields: dict[str, str] = {}
        for key_node, val_node in zip(value.keys, value.values):
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                fields[str(key_node.value)] = _infer_shape_from_value(
                    val_node,
                    bindings=bindings,
                    provider_shapes=provider_shapes,
                    symbol_imports=symbol_imports,
                ).kind
        return ValueShape(kind="object", fields=fields or None)
    if isinstance(value, ast.List):
        return ValueShape(kind="array")
    if isinstance(value, ast.Name):
        return bindings.get(value.id, ValueShape(kind="unknown"))
    return ValueShape(kind="unknown")


def _resolve_expression_shape(
    expression: ast.AST,
    *,
    bindings: Mapping[str, ValueShape],
    provider_shapes: Mapping[Tuple[str, str], ValueShape],
    symbol_imports: Mapping[str, Tuple[str, str | None]],
) -> ValueShape:
    if isinstance(expression, ast.Name):
        return bindings.get(expression.id, ValueShape(kind="unknown"))

    if isinstance(expression, ast.Subscript):
        if not isinstance(expression.value, ast.Name):
            return ValueShape(kind="unknown")
        parent = bindings.get(expression.value.id)
        if parent is None or parent.kind != "object":
            return ValueShape(kind="unknown")
        field_name = _extract_static_subscript_key(expression)
        if not field_name or not parent.fields:
            return ValueShape(kind="unknown")
        return ValueShape(kind=parent.fields.get(field_name, "unknown"))

    if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
        parent = bindings.get(expression.value.id)
        if parent is None or parent.kind != "object" or not parent.fields:
            return ValueShape(kind="unknown")
        return ValueShape(kind=parent.fields.get(expression.attr, "unknown"))

    if isinstance(expression, ast.Call):
        func = expression.func
        if isinstance(func, ast.Name):
            resolved = symbol_imports.get(func.id)
            if resolved is not None and resolved[1]:
                return provider_shapes.get(
                    (resolved[0], resolved[1]),
                    ValueShape(kind="unknown"),
                )
        return ValueShape(kind="unknown")

    if isinstance(expression, ast.Constant):
        return _infer_shape_from_value(
            expression,
            bindings=bindings,
            provider_shapes=provider_shapes,
            symbol_imports=symbol_imports,
        )

    return ValueShape(kind="unknown")


def _shapes_are_compatible(
    actual: ValueShape,
    expected_type: str,
) -> bool | None:
    normalized_expected = str(expected_type or "").strip().lower()
    if not normalized_expected:
        return None
    if actual.kind == "unknown":
        return None
    return actual.kind == normalized_expected


class _ScopeCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.builtins: Set[str] = set(dir(builtins))
        self.unresolved: Set[str] = set()
        self._scopes: list[Set[str]] = [set(self.builtins)]

    def _define(self, name: str) -> None:
        if name:
            self._scopes[-1].add(name)

    def _is_defined(self, name: str) -> bool:
        return any(name in scope for scope in reversed(self._scopes))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            self._define(local_name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            self._define(local_name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._define(node.name)
        self._visit_function_like(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._define(node.name)
        self._visit_function_like(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._define(node.name)
        self._scopes.append(set())
        for stmt in node.body:
            self.visit(stmt)
        self._scopes.pop()

    def _visit_function_like(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        function_scope: Set[str] = set()
        for arg in (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        ):
            function_scope.add(arg.arg)
        if node.args.vararg is not None:
            function_scope.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            function_scope.add(node.args.kwarg.arg)
        self._scopes.append(function_scope)
        if isinstance(node, ast.Lambda):
            self.visit(node.body)
        else:
            for decorator in getattr(node, "decorator_list", []):
                self.visit(decorator)
            for stmt in node.body:
                self.visit(stmt)
        self._scopes.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_function_like(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self._define(node.id)
        elif isinstance(node.ctx, ast.Load):
            if not self._is_defined(node.id):
                self.unresolved.add(node.id)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._bind_target(node.target)
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars)
        for stmt in node.body:
            self.visit(stmt)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._scopes.append(set())
        if node.name:
            self._define(node.name)
        for stmt in node.body:
            self.visit(stmt)
        self._scopes.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node)

    def _visit_comprehension(self, node: ast.AST) -> None:
        self._scopes.append(set())
        for generator in node.generators:
            self.visit(generator.iter)
            self._bind_target(generator.target)
            for if_node in generator.ifs:
                self.visit(if_node)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self._scopes.pop()

    def _bind_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self._define(target.id)
            return
        for child in ast.walk(target):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                self._define(child.id)


def _unresolved_loaded_names(source: str) -> Set[str]:
    tree = ast.parse(source)
    collector = _ScopeCollector()
    collector.visit(tree)
    return collector.unresolved


def _validate_unresolved_python_symbols(
    *,
    path: str,
    source: str,
) -> List[FileContractValidationError]:
    errors: List[FileContractValidationError] = []
    for name in sorted(_unresolved_loaded_names(source)):
        line: int | None = None
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == name:
                    line = getattr(node, "lineno", None)
                    break
        except SyntaxError:
            line = None

        errors.append(
            FileContractValidationError(
                code="UNRESOLVED_PYTHON_SYMBOL",
                path=path,
                message=f"Python symbol {name!r} is used but not defined or imported.",
                details={
                    "symbol": name,
                    "line": line,
                },
            )
        )
    return errors
