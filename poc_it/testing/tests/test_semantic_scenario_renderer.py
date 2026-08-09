from __future__ import annotations

from poc_it.testing.rendering.semantic_scenario_renderer import render_semantic_scenarios


def test_render_semantic_scenarios_binds_real_shared_fake_workflow_file():
    rendered = render_semantic_scenarios(
        {
            "scenario_plans": [
                {
                    "scenario_id": "create_and_read_product",
                    "name": "create and read product",
                    "confidence": 0.91,
                    "evidence": ["openapi_create_read", "shared_id_capture"],
                    "shared_dependencies": ["app.dependencies.product_repo"],
                    "operations": [
                        {
                            "name": "create",
                            "request": {
                                "method": "POST",
                                "path_template": "/products",
                                "json_body": {"name": "Keyboard"},
                            },
                            "assertions": [
                                {"kind": "STATUS_EQUALS", "expected": 201},
                                {"kind": "JSON_HAS_KEYS", "expected": ["id", "name"]},
                            ],
                            "capture": {"response.id": "created_id"},
                        },
                        {
                            "name": "read",
                            "request": {
                                "method": "GET",
                                "path_template": "/products/{created_id}",
                            },
                            "assertions": [
                                {"kind": "STATUS_EQUALS", "expected": 200},
                                {"kind": "JSON_FIELD_EQUALS", "field": "name", "expected": "Keyboard"},
                            ],
                        },
                    ],
                }
            ]
        }
    )

    assert "class InMemoryEntityStore:" in rendered
    assert "def test_scenario_create_and_read_product" in rendered
    assert "dependency_overrides_guard.clear()" in rendered
    assert "repository_fake = InMemoryEntityStore()" in rendered
    assert "dependency_overrides_guard.bind('app.dependencies.product_repo', repository_fake)" in rendered
    assert "response_1 = client.request('POST', step_1_path, json=step_1_json)" in rendered
    assert "context['created_id'] = capture_data_1['id']" in rendered
    assert "step_2_path = _interpolate_template('/products/{created_id}', context)" in rendered
    assert "Scenario step 2 failed: unexpected value for name" in rendered
    assert "finally:" in rendered
    assert "context.clear()" in rendered


def test_render_semantic_scenarios_skips_low_confidence_scenarios():
    rendered = render_semantic_scenarios(
        {
            "scenario_plans": [
                {
                    "scenario_id": "too_uncertain",
                    "confidence": 0.3,
                    "operations": [
                        {
                            "request": {
                                "method": "POST",
                                "path_template": "/products",
                            }
                        }
                    ],
                }
            ]
        }
    )

    assert rendered == "# No stateful scenarios were applicable for this project.\n"
    assert "too_uncertain" not in rendered
    assert "def test_" not in rendered


def test_render_semantic_scenarios_reports_missing_capture_placeholders_precisely():
    rendered = render_semantic_scenarios(
        {
            "scenario_plans": [
                {
                    "scenario_id": "read_without_capture",
                    "confidence": 0.8,
                    "operations": [
                        {
                            "request": {
                                "method": "GET",
                                "path_template": "/products/{missing_id}",
                            },
                            "assertions": [{"kind": "STATUS_EQUALS", "expected": 200}],
                        }
                    ],
                }
            ]
        }
    )

    assert "missing captured value for placeholder" in rendered
    assert "def _interpolate_template(value, context):" in rendered


def test_render_semantic_scenarios_rejects_multi_step_stateful_scenarios_without_binding():
    rendered = render_semantic_scenarios(
        {
            "scenario_plans": [
                {
                    "scenario_id": "missing_binding",
                    "confidence": 0.9,
                    "shared_dependencies": [],
                    "operations": [
                        {
                            "name": "create",
                            "request": {"method": "POST", "path_template": "/products"},
                            "assertions": [{"kind": "STATUS_EQUALS", "expected": 201}],
                        },
                        {
                            "name": "read",
                            "request": {"method": "GET", "path_template": "/products/1"},
                            "assertions": [{"kind": "STATUS_EQUALS", "expected": 200}],
                        },
                    ],
                }
            ]
        }
    )

    assert rendered == "# No stateful scenarios were applicable for this project.\n"
    assert "missing_binding" not in rendered


def test_render_semantic_scenarios_rejects_scenarios_without_observable_assertions():
    rendered = render_semantic_scenarios(
        {
            "scenario_plans": [
                {
                    "scenario_id": "symbolic_only",
                    "confidence": 0.95,
                    "shared_dependencies": ["app.dependencies.product_repo"],
                    "operations": [
                        {
                            "name": "create",
                            "request": {"method": "POST", "path_template": "/products"},
                            "assertions": [{"kind": "JSON_HAS_KEYS", "expected": ["id"]}],
                        }
                    ],
                }
            ]
        }
    )

    assert rendered == "# No stateful scenarios were applicable for this project.\n"
    assert "symbolic_only" not in rendered
