from __future__ import annotations

import json

from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH
from poc_it.testing.planning.test_plan_builder import build_test_plan
from poc_it.testing.reporting.coverage_report import _contains_real_tests
from poc_it.testing.test_generation_service import TestGenerationService


def test_build_test_plan_uses_request_field_types_for_sample_body() -> None:
    runtime_contracts = {
        "allowed_dependency_overrides": ["app.api.deps.get_service"],
        "endpoints": [
            {
                "path": "/items",
                "method": "POST",
                "status_code": 201,
                "depends_imports": ["app.api.deps.get_service"],
                "request_required_fields": ["name", "price", "active"],
                "request_field_types": {
                    "name": "string",
                    "price": "number",
                    "active": "boolean",
                },
                "response_json_required_keys": ["id", "name"],
                "response_media_type": "application/json",
            }
        ],
    }

    plan = build_test_plan(
        structure={
            RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts),
        },
        spec=None,
        mode="PARCIAL",
    )

    endpoint_plan = plan.endpoint_plans[0]
    happy_path = next(case for case in endpoint_plan.cases if case.category == "happy_path")
    assert happy_path.request is not None
    assert happy_path.request.json_body == {
        "name": "x",
        "price": 10.0,
        "active": True,
    }


def test_build_test_plan_uses_sample_response_keys_when_explicit_required_keys_are_missing() -> None:
    runtime_contracts = {
        "allowed_dependency_overrides": ["app.api.deps.get_service"],
        "endpoints": [
            {
                "path": "/health",
                "method": "GET",
                "status_code": 200,
                "depends_imports": ["app.api.deps.get_service"],
                "sample_response": {"status": "ok", "service": "api"},
                "response_media_type": "application/json",
            }
        ],
    }

    plan = build_test_plan(
        structure={
            RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts),
        },
        spec=None,
        mode="PARCIAL",
    )

    happy_path = next(case for case in plan.endpoint_plans[0].cases if case.category == "happy_path")
    json_keys_assert = next(assertion for assertion in happy_path.assertions if assertion.kind == "JSON_HAS_KEYS")
    assert json_keys_assert.expected == ["status", "service"]


def test_build_test_plan_creates_dependency_setup_from_required_internal_calls() -> None:
    runtime_contracts = {
        "allowed_dependency_overrides": ["app.api.deps.get_service"],
        "endpoints": [
            {
                "path": "/items/{item_id}",
                "method": "GET",
                "status_code": 200,
                "depends_imports": ["app.api.deps.get_service"],
                "required_internal_calls": [
                    {
                        "dependency_fqn": "app.api.deps.get_service",
                        "method_name": "fetch_item",
                        "return_value": {"id": 1},
                    }
                ],
                "sample_response": {"id": 1},
                "response_media_type": "application/json",
            }
        ],
    }

    plan = build_test_plan(
        structure={
            RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts),
        },
        spec=None,
        mode="PARCIAL",
    )

    happy_path = next(case for case in plan.endpoint_plans[0].cases if case.category == "happy_path")
    assert [behavior.dependency_fqn for behavior in happy_path.dependency_setup] == ["app.api.deps.get_service"]
    assert [behavior.method_name for behavior in happy_path.dependency_setup] == ["fetch_item"]


def test_service_builds_deterministic_auth_case_from_overrideable_dependency() -> None:
    service = TestGenerationService(feature_flag_enabled=True, fallback_to_legacy_minimal=False)

    runtime_contracts = {
        "allowed_dependency_overrides": ["app.api.deps.get_current_user"],
        "endpoints": [
            {
                "path": "/me",
                "method": "GET",
                "status_code": 200,
                "depends_imports": ["app.api.deps.get_current_user"],
                "sample_response": {"id": 1},
                "response_json_required_keys": ["id"],
            }
        ],
    }

    context = service._build_semantic_context(
        runtime_contracts=runtime_contracts,
        runtime_facts={},
        spec={"paths": {"/me": {"get": {"responses": {"200": {"description": "ok"}}}}}},
        project_name="Demo",
    )

    cases = context.deterministic_cases["get:/me"]
    assert len(cases) == 2
    assert cases[1]["headers"] == {"authorization": "Bearer ok"}
    assert cases[1]["dependency_setup"][0]["dependency_fqn"] == "app.api.deps.get_current_user"


def test_placeholder_tests_do_not_count_as_real_tests() -> None:
    assert _contains_real_tests("def helper():\n    pass\n") is False
    assert _contains_real_tests("def test_case_placeholder():\n    assert True\n") is False
    assert _contains_real_tests("def test_health():\n    assert response.status_code == 200\n") is True
