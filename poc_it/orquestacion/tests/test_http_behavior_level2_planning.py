from __future__ import annotations

import json

from poc_it.orquestacion.test_plan import build_test_plan
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH


def test_level2_happy_path_includes_http_case_with_exact_success_status():
    runtime_contracts = {
        "endpoints": [
            {
                "path": "/products",
                "method": "POST",
                "status_code": 201,
                "sample_request": {"name": "Keyboard"},
                "depends_imports": ["app.dependencies.get_product_repository"],
                "response_json_required_keys": ["id", "name"],
            }
        ],
        "allowed_dependency_overrides": [
            "app.dependencies.get_product_repository",
            "app.dependencies.get_db",
        ],
        "openapi": {
            "paths": {
                "/products": {
                    "post": {
                        "responses": {
                            "201": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "required": ["id", "name"],
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
    }

    plan = build_test_plan(
        structure={
            RUNTIME_CONTRACTS_PATH: json.dumps(
                runtime_contracts,
                ensure_ascii=False,
            )
        },
        spec={"openapi": runtime_contracts["openapi"]},
        mode="COMPLETO",
    )

    endpoint = plan.endpoint_plans[0]
    happy_case = next(case for case in endpoint.cases if case.category == "happy_path")

    assert happy_case.level == "HERMETIC_HTTP"
    assert happy_case.request is not None
    assert happy_case.request.expected_status == 201
    assert any(assertion.kind == "STATUS_EQUALS" and assertion.expected == 201 for assertion in happy_case.assertions)
    assert any(assertion.kind == "JSON_HAS_KEYS" for assertion in happy_case.assertions)
    assert happy_case.dependency_setup == []


def test_level2_nonexistent_case_is_generated_only_with_evidence():
    runtime_contracts = {
        "endpoints": [
            {
                "path": "/products/{product_id}",
                "method": "GET",
                "status_code": 200,
                "sample_request": None,
                "depends_imports": ["app.dependencies.get_product_repository"],
                "response_json_required_keys": ["id", "name"],
                "not_found_evidence": {
                    "declared_status": 404,
                    "dependency_can_return_none": True,
                },
            }
        ],
        "allowed_dependency_overrides": [
            "app.dependencies.get_product_repository",
            "app.dependencies.get_db",
        ],
        "openapi": {
            "paths": {
                "/products/{product_id}": {
                    "get": {
                        "responses": {
                            "200": {"content": {"application/json": {"schema": {"required": ["id", "name"]}}}},
                            "404": {"description": "Not found"},
                        }
                    }
                }
            }
        },
    }

    plan = build_test_plan(
        structure={
            RUNTIME_CONTRACTS_PATH: json.dumps(
                runtime_contracts,
                ensure_ascii=False,
            )
        },
        spec={"openapi": runtime_contracts["openapi"]},
        mode="COMPLETO",
    )

    endpoint = plan.endpoint_plans[0]
    assert any(case.category == "not_found" for case in endpoint.cases)


def test_level2_degrades_with_explicit_reason_when_dependencies_are_not_overrideable():
    runtime_contracts = {
        "endpoints": [
            {
                "path": "/reports",
                "method": "POST",
                "status_code": 201,
                "sample_request": {"title": "Q3"},
                "depends_imports": ["app.dependencies.get_report_service"],
            }
        ],
        "allowed_dependency_overrides": [],
        "openapi": {
            "paths": {
                "/reports": {
                    "post": {
                        "responses": {
                            "201": {"description": "Created"},
                        }
                    }
                }
            }
        },
    }

    plan = build_test_plan(
        structure={
            RUNTIME_CONTRACTS_PATH: json.dumps(
                runtime_contracts,
                ensure_ascii=False,
            )
        },
        spec={"openapi": runtime_contracts["openapi"]},
        mode="COMPLETO",
    )

    endpoint = plan.endpoint_plans[0]
    assert not any(case.category == "happy_path" for case in endpoint.cases)
    assert any("overrideable" in limitation.lower() for limitation in endpoint.limitations)
