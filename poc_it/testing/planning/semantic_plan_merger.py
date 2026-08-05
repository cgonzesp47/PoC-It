from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from poc_it.testing.domain.models import (
    AssertionSpec,
    DependencyBehavior,
    EndpointTestPlan,
    RequestSpec,
    ScenarioStep,
    ScenarioTestPlan,
    TestCasePlan,
    TestPlan,
)
from poc_it.testing.planning.test_plan_builder import (
    _legacy_endpoint_from_new_plan,
    _validate_scenario_plan,
)
from poc_it.testing.semantic_enrichment import SemanticEnrichmentResult


class SemanticPlanValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SemanticMergeResult:
    plan: TestPlan
    warnings: tuple[str, ...] = ()


def merge_semantic_enrichment(
    *,
    plan: object,
    enrichment: SemanticEnrichmentResult,
    runtime_contracts: dict,
) -> SemanticMergeResult | object:
    if not isinstance(plan, TestPlan):
        return plan

    merged_plan = deepcopy(plan)
    warnings: List[str] = []
    endpoint_plans = list(merged_plan.endpoint_plans or [])
    endpoint_index = {endpoint.operation_id: endpoint for endpoint in endpoint_plans}

    _merge_semantic_cases(endpoint_index=endpoint_index, semantic_cases=enrichment.semantic_cases)
    _merge_dependency_behaviors(
        endpoint_index=endpoint_index,
        dependency_behaviors=enrichment.dependency_behaviors,
    )
    _merge_expected_interactions(
        endpoint_index=endpoint_index,
        expected_interactions=enrichment.expected_interactions,
    )
    scenario_merge_result = _merge_stateful_scenarios(
        existing_scenarios=list(merged_plan.scenario_plans or []),
        stateful_scenarios=enrichment.stateful_scenarios,
        endpoint_index=endpoint_index,
    )
    scenario_plans = scenario_merge_result.scenario_plans
    warnings.extend(scenario_merge_result.warnings)

    merged_plan = TestPlan(
        mode=merged_plan.mode,
        strategy=merged_plan.strategy,
        schema_version=merged_plan.schema_version,
        endpoint_plans=endpoint_plans,
        scenario_plans=scenario_plans,
        endpoints=[_legacy_endpoint_from_new_plan(endpoint) for endpoint in endpoint_plans],
        totals=_rebuild_totals(endpoint_plans=endpoint_plans, scenario_plans=scenario_plans),
    )

    validate_test_plan(merged_plan, runtime_contracts=runtime_contracts)
    return SemanticMergeResult(plan=merged_plan, warnings=tuple(warnings))


def validate_test_plan(plan: object, runtime_contracts: Optional[dict] = None) -> None:
    if not isinstance(plan, TestPlan):
        raise SemanticPlanValidationError("Merged test plan must be a TestPlan instance")

    openapi_paths = _runtime_openapi_paths(runtime_contracts if isinstance(runtime_contracts, dict) else {})
    endpoint_ids = set()
    for endpoint in plan.endpoint_plans or []:
        if not isinstance(endpoint, EndpointTestPlan):
            raise SemanticPlanValidationError("endpoint_plans must contain EndpointTestPlan entries")
        if not endpoint.operation_id.strip():
            raise SemanticPlanValidationError("EndpointTestPlan requires operation_id")
        endpoint_ids.add(endpoint.operation_id)
        for case in endpoint.cases or []:
            if not isinstance(case, TestCasePlan):
                raise SemanticPlanValidationError("EndpointTestPlan.cases must contain TestCasePlan entries")
            for behavior in case.dependency_setup or []:
                if not isinstance(behavior, DependencyBehavior):
                    raise SemanticPlanValidationError("dependency_setup entries must be DependencyBehavior")
            for assertion in case.assertions or []:
                if not isinstance(assertion, AssertionSpec):
                    raise SemanticPlanValidationError("assertions entries must be AssertionSpec")
                if assertion.kind == "DEPENDENCY_CALLED" and not str(assertion.target or "").strip():
                    raise SemanticPlanValidationError("DEPENDENCY_CALLED assertions require target dependency")

        if openapi_paths:
            methods = openapi_paths.get(endpoint.path)
            if methods and endpoint.method.lower() not in methods:
                raise SemanticPlanValidationError(
                    f"Merged endpoint '{endpoint.operation_id}' is incompatible with runtime_contracts OpenAPI"
                )

    for scenario in plan.scenario_plans or []:
        if not isinstance(scenario, ScenarioTestPlan):
            raise SemanticPlanValidationError("scenario_plans must contain ScenarioTestPlan entries")
        _validate_scenario_plan(scenario)
        for step in scenario.operations or []:
            if not isinstance(step, ScenarioStep):
                raise SemanticPlanValidationError("Scenario operations must contain ScenarioStep entries")
            if step.operation_id not in endpoint_ids:
                raise SemanticPlanValidationError(
                    f"Scenario '{scenario.scenario_id}' references unknown operation '{step.operation_id}'"
                )


def _merge_semantic_cases(
    *,
    endpoint_index: Dict[str, EndpointTestPlan],
    semantic_cases: List[Dict[str, Any]],
) -> None:
    for item in semantic_cases or []:
        if not isinstance(item, dict):
            continue
        operation_id = str(item.get("operation_id") or "").strip()
        endpoint = endpoint_index.get(operation_id)
        if endpoint is None:
            continue

        category = str(item.get("category") or "semantic").strip() or "semantic"
        level = str(item.get("level") or "SEMANTIC").strip() or "SEMANTIC"
        case_id = str(item.get("case_id") or f"{operation_id.replace(':', '__')}__{category}").strip()
        response_fields = _string_list(item.get("response_fields"))
        assertions = list(endpoint.cases[0].assertions[:0]) if endpoint.cases else []
        if response_fields:
            assertions.append(
                AssertionSpec(
                    kind="JSON_HAS_KEYS",
                    expected=response_fields,
                    metadata={"source": "semantic_enrichment"},
                )
            )
        expected_status = item.get("expected_status")
        if isinstance(expected_status, int):
            assertions.append(
                AssertionSpec(
                    kind="STATUS_EQUALS",
                    expected=expected_status,
                    metadata={"source": "semantic_enrichment"},
                )
            )

        evidence = _string_list(item.get("evidence"))
        request = _request_like_from_existing_cases(endpoint.cases, expected_status=expected_status)
        new_case = TestCasePlan(
            case_id=case_id,
            level=level,
            category=category,
            request=request,
            dependency_setup=[],
            assertions=assertions,
        )
        if not any(case.case_id == new_case.case_id for case in endpoint.cases):
            endpoint.cases.append(new_case)
        if evidence:
            endpoint.evidence.extend(value for value in evidence if value not in endpoint.evidence)


def _merge_dependency_behaviors(
    *,
    endpoint_index: Dict[str, EndpointTestPlan],
    dependency_behaviors: List[Dict[str, Any]],
) -> None:
    for item in dependency_behaviors or []:
        if not isinstance(item, dict):
            continue
        operation_id = str(item.get("operation_id") or "").strip()
        if not operation_id:
            operation_id = _guess_operation_id_for_dependency(
                endpoint_index=endpoint_index,
                dependency=str(item.get("dependency_fqn") or item.get("dependency") or "").strip(),
            )
        endpoint = endpoint_index.get(operation_id)
        if endpoint is None:
            continue

        behavior = DependencyBehavior(
            dependency_fqn=str(item.get("dependency_fqn") or item.get("dependency") or "").strip(),
            method_name=str(item.get("method_name") or item.get("method") or "").strip(),
            action=str(item.get("action") or item.get("behavior") or "return").strip(),
            value=item.get("value"),
            exception_type=(str(item.get("exception_type")).strip() if item.get("exception_type") else None),
            exception_message=(str(item.get("exception_message")).strip() if item.get("exception_message") else None),
        )
        if not behavior.dependency_fqn:
            continue
        if not behavior.method_name:
            continue
        if behavior.action not in {"return", "raise", "yield", "async_return"}:
            continue

        for idx, case in enumerate(list(endpoint.cases or [])):
            if not _is_http_like_case(case):
                continue
            if any(
                existing.dependency_fqn == behavior.dependency_fqn
                and existing.method_name == behavior.method_name
                and existing.action == behavior.action
                for existing in case.dependency_setup
            ):
                continue
            endpoint.cases[idx] = TestCasePlan(
                case_id=case.case_id,
                level=case.level,
                category=case.category,
                request=case.request,
                dependency_setup=[*case.dependency_setup, behavior],
                assertions=list(case.assertions),
            )


def _merge_expected_interactions(
    *,
    endpoint_index: Dict[str, EndpointTestPlan],
    expected_interactions: List[Dict[str, Any]],
) -> None:
    for item in expected_interactions or []:
        if not isinstance(item, dict):
            continue
        operation_id = str(item.get("operation_id") or "").strip()
        endpoint = endpoint_index.get(operation_id)
        if endpoint is None:
            continue

        dependency = str(item.get("dependency") or "").strip()
        method_name = str(item.get("method") or item.get("method_name") or "").strip()
        if not dependency:
            continue

        assertion = AssertionSpec(
            kind="DEPENDENCY_CALLED",
            target=dependency,
            expected={
                "times": item.get("times"),
                "args_subset": item.get("args_subset"),
            },
            metadata={
                "method_name": method_name,
                "source": "semantic_enrichment",
            },
        )
        for idx, case in enumerate(list(endpoint.cases or [])):
            if not _is_http_like_case(case):
                continue
            if any(
                existing.kind == assertion.kind
                and existing.target == assertion.target
                and existing.metadata.get("method_name") == method_name
                for existing in case.assertions
            ):
                continue
            endpoint.cases[idx] = TestCasePlan(
                case_id=case.case_id,
                level=case.level,
                category=case.category,
                request=case.request,
                dependency_setup=list(case.dependency_setup),
                assertions=[*case.assertions, assertion],
            )


@dataclass(frozen=True)
class StatefulScenarioMergeResult:
    scenario_plans: List[ScenarioTestPlan]
    warnings: tuple[str, ...] = ()


def _merge_stateful_scenarios(
    *,
    existing_scenarios: List[ScenarioTestPlan],
    stateful_scenarios: List[Dict[str, Any]],
    endpoint_index: Dict[str, EndpointTestPlan],
) -> StatefulScenarioMergeResult:
    merged = list(existing_scenarios or [])
    warnings: List[str] = []
    known_ids = {scenario.scenario_id for scenario in merged}
    for item in stateful_scenarios or []:
        if not isinstance(item, dict):
            continue

        operation_ids = _string_list(item.get("operations") or item.get("operation_ids"))
        if not operation_ids:
            continue

        missing_operation_ids = [
            operation_id for operation_id in operation_ids if operation_id not in endpoint_index
        ]
        if missing_operation_ids:
            scenario_id = str(item.get("scenario_id") or item.get("name") or "unknown").strip() or "unknown"
            warnings.append(
                "semantic enrichment ignored: "
                f"scenario {scenario_id!r} references unknown "
                f"operations {missing_operation_ids}"
            )
            continue

        operations = [
            ScenarioStep(
                operation_id=operation_id,
                request=_request_for_scenario_step(endpoint_index[operation_id]),
                capture={},
                assertions=[],
            )
            for operation_id in operation_ids
        ]

        operations = _apply_crud_capture_flow(operations)

        scenario_id = str(item.get("scenario_id") or item.get("name") or f"semantic__{len(merged)+1}").strip()
        if scenario_id in known_ids:
            continue

        declared_shared_dependencies = _string_list(item.get("shared_dependencies"))
        inferred_shared_dependencies = _shared_dependencies_for_operations(operations, endpoint_index)
        shared_dependencies = list(
            dict.fromkeys([*declared_shared_dependencies, *inferred_shared_dependencies])
        )

        scenario = ScenarioTestPlan(
            scenario_id=scenario_id,
            name=str(item.get("name") or scenario_id).strip(),
            level=str(item.get("level") or "SEMANTIC_STATEFUL").strip() or "SEMANTIC_STATEFUL",
            operations=operations,
            shared_dependencies=shared_dependencies,
            assertions=[],
            evidence=_string_list(item.get("evidence")),
            confidence=_safe_confidence(item.get("confidence")),
        )
        merged.append(scenario)
        known_ids.add(scenario_id)
    return StatefulScenarioMergeResult(
        scenario_plans=merged,
        warnings=tuple(warnings),
    )


def _shared_dependencies_for_operations(
    operations: List[ScenarioStep],
    endpoint_index: Dict[str, EndpointTestPlan],
) -> List[str]:
    values: List[str] = []
    for step in operations:
        endpoint = endpoint_index.get(step.operation_id)
        if endpoint is None:
            continue
        for case in endpoint.cases or []:
            for behavior in case.dependency_setup or []:
                if behavior.dependency_fqn and behavior.dependency_fqn not in values:
                    values.append(behavior.dependency_fqn)
    return values


def _request_like_from_existing_cases(
    cases: List[TestCasePlan],
    *,
    expected_status: Optional[int],
):
    for case in cases or []:
        if case.request is not None:
            request = deepcopy(case.request)
            if expected_status is not None:
                request = type(request)(
                    method=request.method,
                    path_template=request.path_template,
                    path_params=dict(request.path_params),
                    query_params=dict(request.query_params),
                    headers=dict(request.headers),
                    cookies=dict(request.cookies),
                    json_body=deepcopy(request.json_body),
                    form_data=dict(request.form_data),
                    files=dict(request.files),
                    expected_status=expected_status,
                    allowed_statuses=list(request.allowed_statuses) if request.allowed_statuses else ([expected_status] if expected_status is not None else []),
                    response_media_type=request.response_media_type,
                )
            return request
    return None


def _guess_operation_id_for_dependency(
    *,
    endpoint_index: Dict[str, EndpointTestPlan],
    dependency: str,
) -> str:
    for operation_id, endpoint in endpoint_index.items():
        for case in endpoint.cases or []:
            if any(behavior.dependency_fqn == dependency for behavior in case.dependency_setup):
                return operation_id
    return ""


def _request_for_scenario_step(endpoint: EndpointTestPlan):
    existing = _request_like_from_existing_cases(endpoint.cases, expected_status=None)
    if existing is not None:
        return existing

    return type(endpoint.cases[0].request)(
        method=endpoint.method,
        path_template=endpoint.path,
        path_params={},
        query_params={},
        headers={},
        cookies={},
        json_body=None,
        form_data={},
        files={},
        expected_status=None,
        allowed_statuses=[],
        response_media_type="application/json",
    ) if endpoint.cases and endpoint.cases[0].request is not None else RequestSpec(
        method=endpoint.method,
        path_template=endpoint.path,
        path_params={},
        query_params={},
        headers={},
        cookies={},
        json_body=None,
        form_data={},
        files={},
        expected_status=None,
        allowed_statuses=[],
        response_media_type="application/json",
    )


def _bind_captured_id(request, alias: str):
    placeholders = re.findall(r"\{([^{}]+)\}", request.path_template)

    path_params = dict(request.path_params)

    if placeholders:
        path_params[placeholders[0]] = "{" + alias + "}"

    return type(request)(
        method=request.method,
        path_template=request.path_template,
        path_params=path_params,
        query_params=dict(request.query_params),
        headers=dict(request.headers),
        cookies=dict(request.cookies),
        json_body=deepcopy(request.json_body),
        form_data=dict(request.form_data),
        files=dict(request.files),
        expected_status=request.expected_status,
        allowed_statuses=list(request.allowed_statuses),
        response_media_type=request.response_media_type,
    )


def _apply_crud_capture_flow(operations: List[ScenarioStep]) -> List[ScenarioStep]:
    if not operations:
        return operations

    first = operations[0]
    if first.request.method.upper() != "POST":
        return operations

    updated = list(operations)
    updated[0] = ScenarioStep(
        operation_id=first.operation_id,
        request=first.request,
        capture={"response.id": "entity_id"},
        assertions=list(first.assertions),
    )

    for index in range(1, len(updated)):
        step = updated[index]
        updated[index] = ScenarioStep(
            operation_id=step.operation_id,
            request=_bind_captured_id(step.request, "entity_id"),
            capture=dict(step.capture),
            assertions=list(step.assertions),
        )
    return updated


def _is_http_like_case(case: TestCasePlan) -> bool:
    return case.request is not None


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _safe_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except Exception:
        return 0.0
    return confidence if 0.0 <= confidence <= 1.0 else 0.0


def _rebuild_totals(
    *,
    endpoint_plans: List[EndpointTestPlan],
    scenario_plans: List[ScenarioTestPlan],
) -> Dict[str, int]:
    totals: Dict[str, int] = {
        "total_endpoints": len(endpoint_plans),
        "total_scenarios": len(scenario_plans),
        "SEMANTIC_STATEFUL": len(scenario_plans),
    }
    for level in ("STARTUP", "OPENAPI_CONTRACT", "HERMETIC_HTTP", "SEMANTIC"):
        totals[level] = sum(1 for ep in endpoint_plans for case in ep.cases if case.level == level)
    legacy_endpoints = [_legacy_endpoint_from_new_plan(ep) for ep in endpoint_plans]
    for level in ("SMOKE_ONLY", "OPENAPI_CONTRACT", "HERMETIC_ENDPOINT_CONTRACT"):
        totals[level] = sum(1 for endpoint in legacy_endpoints if endpoint.level == level)
    return totals


def _runtime_openapi_paths(runtime_contracts: Optional[dict]) -> Dict[str, set]:
    if not isinstance(runtime_contracts, dict):
        return {}
    openapi = None
    for key in ("openapi", "openapi_json", "openapi_spec"):
        candidate = runtime_contracts.get(key)
        if isinstance(candidate, dict) and candidate:
            openapi = candidate
            break
    if not isinstance(openapi, dict):
        return {}
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        return {}
    out: Dict[str, set] = {}
    for path, methods in paths.items():
        if not isinstance(path, str) or not isinstance(methods, dict):
            continue
        out[path] = {str(method).lower() for method in methods.keys() if str(method).strip()}
    return out
