from __future__ import annotations

import json

from poc_it.orquestacion.contract_test_renderer import render_tests_from_test_plan
from poc_it.orquestacion.test_plan import TEST_PLAN_PATH, build_test_plan
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH


def test_build_test_plan_emits_stateful_scenario_with_shared_dependencies_and_capture():
    runtime_contracts = {
        "allowed_dependency_overrides": [
            "app.dependencies.get_product_repository",
            "app.dependencies.get_db",
        ],
        "endpoints": [
            {
                "path": "/products",
                "method": "POST",
                "status_code": 201,
                "sample_request": {"name": "Keyboard"},
                "depends_imports": ["app.dependencies.get_product_repository"],
                "response_json_required_keys": ["id", "name"],
                "statefulness_recommended": "per_client_fixture",
            },
            {
                "path": "/products/{product_id}",
                "method": "GET",
                "status_code": 200,
                "depends_imports": ["app.dependencies.get_product_repository"],
                "response_json_required_keys": ["id", "name"],
                "not_found_evidence": {
                    "declared_status": 404,
                    "dependency_can_return_none": True,
                },
                "statefulness_recommended": "per_client_fixture",
            },
            {
                "path": "/products/{product_id}",
                "method": "DELETE",
                "status_code": 204,
                "depends_imports": ["app.dependencies.get_product_repository"],
                "statefulness_recommended": "per_client_fixture",
            },
        ],
    }

    openapi = {
        "openapi": "3.1.0",
        "paths": {
            "/products": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id", "name"],
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                    }
                                }
                            }
                        }
                    },
                }
            },
            "/products/{product_id}": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id", "name"],
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                    }
                                }
                            }
                        },
                        "404": {"description": "Not found"},
                    }
                },
                "delete": {
                    "responses": {
                        "204": {"description": "Deleted"},
                    }
                },
            },
        },
    }

    plan = build_test_plan(
        structure={RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts)},
        spec={"openapi": openapi},
        mode="COMPLETO",
    )

    assert len(plan.scenario_plans) == 1
    scenario = plan.scenario_plans[0]
    assert scenario.level == "SEMANTIC_STATEFUL"
    assert len(scenario.operations) == 4
    assert scenario.operations[0].operation_id == "post:/products"
    assert scenario.operations[0].capture == {"response.id": "product_id"}
    assert scenario.operations[1].request.path_params == {"product_id": "{product_id}"}
    assert scenario.operations[2].operation_id == "delete:/products/{product_id}"
    assert scenario.operations[3].request.expected_status == 404
    assert scenario.shared_dependencies == []
    assert any(assertion.kind == "JSON_FIELD_EQUALS" for assertion in scenario.assertions)
    assert scenario.confidence >= 0.85
    assert plan.totals["SEMANTIC_STATEFUL"] == 1


def test_render_tests_from_test_plan_emits_independent_stateful_scenario_file():
    plan = {
        "mode": "COMPLETO",
        "strategy": "contract-first",
        "endpoint_plans": [],
        "endpoints": [],
        "scenario_plans": [
            {
                "scenario_id": "scenario__post_products",
                "name": "Product workflow",
                "level": "SEMANTIC_STATEFUL",
                "shared_dependencies": ["app.dependencies.get_product_repository"],
                "evidence": ["shared_state", "capture:response.id"],
                "confidence": 0.95,
                "operations": [
                    {
                        "operation_id": "post:/products",
                        "request": {
                            "method": "POST",
                            "path_template": "/products",
                            "json_body": {"name": "Keyboard"},
                            "expected_status": 201,
                            "allowed_statuses": [201],
                            "response_media_type": "application/json",
                        },
                        "capture": {"response.id": "product_id"},
                        "assertions": [{"kind": "STATUS_EQUALS", "expected": 201}],
                    },
                    {
                        "operation_id": "get:/products/{product_id}",
                        "request": {
                            "method": "GET",
                            "path_template": "/products/{product_id}",
                            "path_params": {"product_id": "{product_id}"},
                            "expected_status": 200,
                            "allowed_statuses": [200],
                            "response_media_type": "application/json",
                        },
                        "capture": {},
                        "assertions": [{"kind": "STATUS_EQUALS", "expected": 200}],
                    },
                ],
                "assertions": [
                    {
                        "kind": "JSON_FIELD_EQUALS",
                        "target": "name",
                        "expected": "Keyboard",
                        "metadata": {"evidence": "stateful_read_after_create"},
                    }
                ],
            }
        ],
    }

    rendered = render_tests_from_test_plan(
        structure={TEST_PLAN_PATH: json.dumps(plan)},
        runtime_contracts={},
        runtime_facts={},
    )

    assert "tests/test_semantic_scenarios.py" in rendered
    assert "tests/test_stateful_scenarios.py" not in rendered
    assert "tests/test_openapi_contract.py" in rendered
    assert "tests/test_request_validation.py" in rendered
    assert "tests/test_startup.py" in rendered
    assert "test_semantic_scenarios.py" in rendered["pytest.ini"]
    assert "test_stateful_scenarios.py" not in rendered["pytest.ini"]
