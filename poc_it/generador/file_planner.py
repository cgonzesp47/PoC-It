from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Dict, List, Set


@dataclass(frozen=True)
class FilePlanResult:
    spec: Dict[str, Any]
    generated_paths: List[str]


def _safe_module_name(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")

    if not raw:
        raise ValueError("integration id cannot produce module name")

    if raw[0].isdigit():
        raw = f"integration_{raw}"

    return raw


def enrich_spec_files_for_implementation(
    spec: Dict[str, Any],
    *,
    implementation_contracts: List[Dict[str, Any]],
) -> FilePlanResult:
    planned_spec = deepcopy(spec)
    generated_paths: List[str] = []

    files = planned_spec.get("files")
    if not isinstance(files, list):
        files = []
    planned_spec["files"] = _dedupe_paths(files)

    integrations = planned_spec.get("integrations")
    if not isinstance(integrations, list):
        integrations = []

    endpoints = planned_spec.get("endpoints")
    if not isinstance(endpoints, list):
        endpoints = []

    referenced_integrations = _referenced_integrations(
        endpoints=endpoints,
        implementation_contracts=implementation_contracts,
    )

    implementation_files = planned_spec.get("implementation_files")
    if not isinstance(implementation_files, list):
        implementation_files = []
    normalized_implementation_files = [
        dict(item) for item in implementation_files if isinstance(item, dict)
    ]

    has_integration_init = "app/integrations/__init__.py" in planned_spec["files"]

    for integration in integrations:
        if not isinstance(integration, dict):
            continue

        integration_id = str(integration.get("id") or "").strip()
        if not integration_id:
            continue

        if integration.get("required", True) is not True:
            continue

        if integration_id not in referenced_integrations:
            continue

        module_path = str(integration.get("module_path") or "").strip()
        if module_path:
            normalized_module_path = module_path.replace("\\", "/")
            if not normalized_module_path.startswith("app/"):
                raise ValueError(
                    f"integration module_path must be under app/: {normalized_module_path}"
                )
            planned_path = normalized_module_path
        else:
            planned_path = (
                f"app/integrations/{_safe_module_name(integration_id)}.py"
            )

        if not has_integration_init:
            planned_spec["files"].append("app/integrations/__init__.py")
            generated_paths.append("app/integrations/__init__.py")
            has_integration_init = True

        if planned_path not in planned_spec["files"]:
            planned_spec["files"].append(planned_path)
            generated_paths.append(planned_path)

        if not any(
            str(item.get("path") or "").replace("\\", "/") == planned_path
            for item in normalized_implementation_files
        ):
            normalized_implementation_files.append(
                {
                    "path": planned_path,
                    "kind": "integration",
                    "integration_ref": integration_id,
                    "generated_by": "file_planner",
                }
            )

    planned_spec["files"] = _dedupe_paths(planned_spec["files"])
    planned_spec["implementation_files"] = normalized_implementation_files

    return FilePlanResult(
        spec=planned_spec,
        generated_paths=_dedupe_paths(generated_paths),
    )


def _referenced_integrations(
    *,
    endpoints: List[Any],
    implementation_contracts: List[Dict[str, Any]],
) -> Set[str]:
    result: Set[str] = set()

    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        for item in endpoint.get("integration_refs") or []:
            if isinstance(item, str) and item.strip():
                result.add(item.strip())

    for contract in implementation_contracts or []:
        if not isinstance(contract, dict):
            continue
        for item in contract.get("integration_refs") or []:
            if isinstance(item, str) and item.strip():
                result.add(item.strip())
        for dependency in contract.get("external_dependencies") or []:
            if not isinstance(dependency, dict):
                continue
            dep_id = str(dependency.get("id") or "").strip()
            if dep_id:
                result.add(dep_id)

    return result


def _dedupe_paths(paths: List[Any]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []

    for path in paths:
        normalized = str(path or "").replace("\\", "/").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)

    return result
