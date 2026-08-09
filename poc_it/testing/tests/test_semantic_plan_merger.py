from __future__ import annotations

from poc_it.testing.planning.test_plan_api import (
    EndpointCapabilities,
    EndpointTestPlan,
    RequestSpec,
    ScenarioTestPlan,
    TestCasePlan,
    TestPlan,
    build_test_plan,
)
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH
from poc_it.testing.planning.semantic_plan_merger import merge_semantic_enrichment
from poc_it.testing.semantic_enrichment import SemanticEnrichmentResult


def _base_endpoint_plan(
    *,
    operation_id: str,
    method: str,
    path: str,
    sample_request: dict | None = None,
    expected_status: int | None = 200,
) -> EndpointTestPlan:
    return EndpointTestPlan(
        operation_id=operation_id,
        method=method,
        path=path,
        capabilities=EndpointCapabilities(
            startup_available=True,
            openapi_available=True,
            request_schema_complete=True,
            valid_request_generatable=True,
            invalid_request_generatable=sample_request is not None,
            dependencies_discovered=True,
            dependencies_overrideable=True,
            dependency_protocol_known=True,
            response_contract_known=True,
            stateful_candidate=True,
        ),
        cases=[
            TestCasePlan(
                case_id=f"{operation_id.replace(':', '__')}__happy_path",
                level="HERMETIC_HTTP",
                category="happy_path",
                request=RequestSpec(
                    method=method,
                    path_template=path,
                    path_params={},
                    query_params={},
                    headers={},
                    cookies={},
                    json_body=sample_request,
                    form_data={},
                    files={},
                    expected_status=expected_status,
                    allowed_statuses=[expected_status] if expected_status is not None else [],
                    response_media_type="application/json",
                ),
                dependency_setup=[],
                assertions=[],
            )
        ],
        limitations=[],
        evidence=[],
    )


def test_merge_semantic_enrichment_builds_crud_scenario_with_declared_shared_dependency() -> None:
    plan = TestPlan(
        mode="PARCIAL",
        endpoint_plans=[
            _base_endpoint_plan(
                operation_id="post:/items",
                method="POST",
                path="/items",
                sample_request={"name": "demo"},
                expected_status=201,
            ),
            _base_endpoint_plan(
                operation_id="get:/items/{item_id}",
                method="GET",
                path="/items/{item_id}",
                sample_request=None,
                expected_status=200,
            ),
            _base_endpoint_plan(
                operation_id="delete:/items/{item_id}",
                method="DELETE",
                path="/items/{item_id}",
                sample_request=None,
                expected_status=200,
            ),
        ],
        scenario_plans=[],
        endpoints=[],
        totals={},
    )

    enrichment = SemanticEnrichmentResult(
        semantic_cases=[],
        stateful_scenarios=[
            {
                "scenario_id": "repository_crud_lifecycle",
                "operations": [
                    "post:/items",
                    "get:/items/{item_id}",
                    "delete:/items/{item_id}",
                ],
                "shared_dependencies": [
                    "app.main.get_repository",
                ],
            }
        ],
        dependency_behaviors=[],
        expected_interactions=[],
        uncertainties=[],
        warnings=[],
    )

    merged = merge_semantic_enrichment(
        plan=plan,
        enrichment=enrichment,
        runtime_contracts={},
    )

    assert isinstance(merged.plan, TestPlan)
    assert len(merged.plan.scenario_plans) == 1
    assert merged.warnings == ()

    scenario = merged.plan.scenario_plans[0]

    assert isinstance(scenario, ScenarioTestPlan)
    assert len(scenario.operations) == 3
    assert scenario.shared_dependencies == [
        "app.main.get_repository",
    ]
    assert scenario.operations[0].capture == {
        "response.id": "entity_id",
    }
    assert scenario.operations[1].request.path_params == {
        "item_id": "{entity_id}",
    }


def test_invalid_stateful_scenario_is_ignored_with_warning() -> None:
    plan = TestPlan(
        mode="PARCIAL",
        endpoint_plans=[
            _base_endpoint_plan(
                operation_id="post:/items",
                method="POST",
                path="/items",
                sample_request={"name": "demo"},
                expected_status=201,
            ),
        ],
        scenario_plans=[],
        endpoints=[],
        totals={},
    )

    enrichment = SemanticEnrichmentResult(
        semantic_cases=[],
        stateful_scenarios=[
            {
                "scenario_id": "repository_crud_lifecycle",
                "operations": [
                    "post:/items",
                    "get:/items/{item_id}",
                ],
                "shared_dependencies": [
                    "app.main.get_repository",
                ],
            }
        ],
        dependency_behaviors=[],
        expected_interactions=[],
        uncertainties=[],
        warnings=[],
    )

    result = merge_semantic_enrichment(
        plan=plan,
        enrichment=enrichment,
        runtime_contracts={},
    )

    assert result.plan.scenario_plans == []
    assert any(
        "semantic enrichment ignored" in warning and "get:/items/{item_id}" in warning
        for warning in result.warnings
    )


def test_build_test_plan_partial_keeps_non_db_dependency_overrideable() -> None:
    import json

    runtime_contracts = {
        "allowed_dependency_overrides": [
            "app.main.get_repository",
        ],
        "endpoints": [
            {
                "path": "/items",
                "method": "POST",
                "operation_id": "post:/items",
                "depends_imports": [
                    "app.main.get_repository",
                ],
                "sample_request": {"name": "demo"},
                "status_code": 201,
                "response_json_required_keys": ["id", "name"],
            }
        ],
    }

    structure = {
        RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts),
    }

    plan = build_test_plan(
        structure=structure,
        spec={"mode": "PARCIAL", "openapi": {"paths": {"/items": {"post": {}}}}},
        mode="PARCIAL",
    )

    assert plan.endpoint_plans, "Expected endpoint plans from runtime contracts"

    endpoint = next(ep for ep in plan.endpoint_plans if ep.operation_id == "post:/items")

    assert endpoint.capabilities.dependencies_overrideable is True
    assert any(
        case.category == "happy_path" and case.request is not None
        for case in endpoint.cases
    )


def test_build_test_plan_generates_path_params() -> None:
    import json

    runtime_contracts = {
        "allowed_dependency_overrides": [
            "app.main.get_repository",
        ],
        "endpoints": [
            {
                "path": "/items/{item_id}",
                "method": "GET",
                "operation_id": "get:/items/{item_id}",
                "depends_imports": [
                    "app.main.get_repository",
                ],
                "status_code": 200,
                "response_json_required_keys": [
                    "id",
                    "name",
                ],
            }
        ],
    }

    structure = {
        RUNTIME_CONTRACTS_PATH: json.dumps(
            runtime_contracts
        ),
    }

    plan = build_test_plan(
        structure=structure,
        spec={
            "mode": "PARCIAL",
            "openapi": {
                "paths": {
                    "/items/{item_id}": {
                        "get": {
                            "parameters": [
                                {
                                    "name": "item_id",
                                    "in": "path",
                                    "required": True,
                                    "schema": {
                                        "type": "integer",
                                    },
                                }
                            ],
                            "responses": {
                                "200": {
                                    "description": "OK",
                                }
                            },
                        }
                    }
                }
            },
        },
        mode="PARCIAL",
    )

    endpoint = next(
        ep
        for ep in plan.endpoint_plans
        if ep.operation_id
        == "get:/items/{item_id}"
    )

    happy = next(
        case
        for case in endpoint.cases
        if case.category == "happy_path"
    )

    assert happy.request is not None
    assert happy.request.path_params == {
        "item_id": 1,
    }
