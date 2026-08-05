from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple


@dataclass(frozen=True)
class FileContractValidationError:
    code: str
    path: str
    message: str


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
        levels = [str(item or "").strip() for item in (contract.get("implementation_levels") or [])]
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


def _integration_dependencies(
    integration: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> List[str]:
    deps: List[str] = []
    refs = {
        str(ref or "").strip()
        for ref in (integration.get("technology_refs") or [])
        if str(ref or "").strip()
    }
    for signal in spec.get("technology_signals") or []:
        if not isinstance(signal, Mapping):
            continue
        names = {
            str(signal.get("name") or "").strip(),
            str(signal.get("id") or "").strip(),
        }
        if refs and refs.isdisjoint({name for name in names if name}):
            continue
        for key in ("package", "packages"):
            value = signal.get(key)
            if isinstance(value, str) and value.strip():
                deps.append(value.strip())
            elif isinstance(value, list):
                for item in value:
                    item_str = str(item or "").strip()
                    if item_str:
                        deps.append(item_str)
    result: List[str] = []
    seen: Set[str] = set()
    for dep in deps:
        if dep in seen:
            continue
        seen.add(dep)
        result.append(dep)
    return result
