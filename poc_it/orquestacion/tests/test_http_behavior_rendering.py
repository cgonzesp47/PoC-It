from __future__ import annotations

import json

from poc_it.orquestacion.contract_test_renderer import render_tests_from_test_plan
from poc_it.orquestacion.test_plan import TEST_PLAN_PATH


def test_render_tests_from_test_plan_emits_unified_http_behavior_file_and_preserves_openapi_level():
    plan = {
        "mode": "COMPLETO",
        "strategy": "contract-first",
        "endpoints": [],
        "endpoint_plans": [
            {
                "method": "POST",
                "path": "/products",
                "limitations": ["Sin limitaciones relevantes detectadas."],
                "cases": [
                    {
                        "level": "STARTUP",
                        "category": "startup",
                        "request": None,
                        "assertions": [{"kind": "STATE_CONTAINS", "target": "startup_available", "expected": True}],
                    },
                    {
                        "level": "OPENAPI_CONTRACT",
                        "category": "openapi",
                        "request": None,
                        "assertions": [{"kind": "STATE_CONTAINS", "target": "openapi_path", "expected": "/products"}],
                    },
                    {
                        "level": "HERMETIC_HTTP",
                        "category": "happy_path",
                        "request": {
                            "method": "POST",
                            "path_template": "/products",
                            "json_body": {"name": "Keyboard"},
                            "expected_status": 201,
                            "allowed_statuses": [201],
                            "response_media_type": "application/json",
                        },
                        "assertions": [
                            {"kind": "STATUS_EQUALS", "expected": 201, "metadata": {"evidence": "response.status"}},
                            {
                                "kind": "JSON_HAS_KEYS",
                                "expected": ["id", "name"],
                                "metadata": {"evidence": "response.required_fields"},
                            },
                        ],
                    },
                ],
            }
        ],
    }

    rendered = render_tests_from_test_plan(
        structure={TEST_PLAN_PATH: json.dumps(plan)},
        runtime_contracts={},
        runtime_facts={},
    )

    assert "tests/test_http_behavior.py" in rendered
    assert "tests/test_endpoint_contracts.py" not in rendered
    assert "tests/test_openapi_contract.py" in rendered
    assert "test_contract_post_products" in rendered["tests/test_http_behavior.py"]
    assert "assert resp.status_code == 201" in rendered["tests/test_http_behavior.py"]
    assert "# evidence: response.status" in rendered["tests/test_http_behavior.py"]
    assert "def test_openapi_contract_suite_has_no_endpoints" in rendered["tests/test_openapi_contract.py"] or "def test_openapi_contract_" in rendered["tests/test_openapi_contract.py"]
    assert "test_http_behavior.py" in rendered["pytest.ini"]


def test_render_tests_from_test_plan_renders_dependency_value_provider() -> None:
    plan = {
        "mode": "PARCIAL",
        "strategy": "contract-first",
        "endpoints": [],
        "endpoint_plans": [
            {
                "method": "GET",
                "path": "/me",
                "limitations": ["Sin limitaciones relevantes detectadas."],
                "cases": [
                    {
                        "level": "HERMETIC_HTTP",
                        "category": "happy_path",
                        "request": {
                            "method": "GET",
                            "path_template": "/me",
                            "path_params": {},
                            "expected_status": 200,
                            "allowed_statuses": [200],
                            "response_media_type": "application/json",
                        },
                        "dependency_setup": [
                            {
                                "dependency_fqn": "app.main.get_current_user",
                                "method_name": "",
                                "action": "provide",
                                "value": {"sub": "demo"},
                            }
                        ],
                        "assertions": [
                            {"kind": "STATUS_EQUALS", "expected": 200, "metadata": {"evidence": "response.status"}},
                        ],
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

    http_test = rendered["tests/test_http_behavior.py"]
    assert "app.main.get_current_user" in http_test
    assert "demo" in http_test


def test_render_tests_from_test_plan_renders_dependency_auto_double() -> None:
    plan = {
        "mode": "PARCIAL",
        "strategy": "contract-first",
        "endpoints": [],
        "endpoint_plans": [
            {
                "method": "POST",
                "path": "/upload",
                "limitations": ["Sin limitaciones relevantes detectadas."],
                "cases": [
                    {
                        "level": "HERMETIC_HTTP",
                        "category": "happy_path",
                        "request": {
                            "method": "POST",
                            "path_template": "/upload",
                            "json_body": {"filename": "x"},
                            "expected_status": 200,
                            "allowed_statuses": [200],
                            "response_media_type": "application/json",
                        },
                        "dependency_setup": [
                            {
                                "dependency_fqn": "app.integrations.google_drive_api.build_client",
                                "method_name": "",
                                "action": "provide_auto",
                                "value": None,
                            }
                        ],
                        "assertions": [
                            {"kind": "STATUS_EQUALS", "expected": 200, "metadata": {"evidence": "response.status"}},
                        ],
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

    http_test = rendered["tests/test_http_behavior.py"]
    assert (
        "dependency_overrides_guard.bind_auto_double('app.integrations.google_drive_api.build_client')"
        in http_test
    )
