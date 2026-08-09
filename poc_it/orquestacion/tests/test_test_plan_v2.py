import json

from poc_it.orquestacion.test_plan import build_test_plan, persist_test_plan
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH


def test_build_test_plan_emits_schema_version_3_and_multiple_cases_per_endpoint():
    runtime_contracts = {
        "allowed_dependency_overrides": ["app.main.get_db"],
        "endpoints": [
            {
                "path": "/items",
                "method": "POST",
                "depends_imports": ["app.main.get_db"],
                "request_required_fields": ["name"],
                "response_json_required_keys": ["id", "name"],
                "status_code": 201,
            }
        ],
        "openapi": {
            "openapi": "3.1.0",
            "paths": {
                "/items": {
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
                                "description": "Created",
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
                                },
                            },
                            "422": {"description": "Validation error"},
                        },
                    }
                }
            },
        },
    }

    structure = {
        RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False, indent=2),
    }
    plan = build_test_plan(
        structure=structure,
        spec={"openapi": runtime_contracts["openapi"]},
        mode="COMPLETO",
    )

    assert plan.schema_version == 3
    assert len(plan.endpoint_plans) == 1

    endpoint_plan = plan.endpoint_plans[0]
    assert endpoint_plan.path == "/items"
    assert endpoint_plan.method == "POST"
    assert endpoint_plan.capabilities.dependencies_overrideable is True
    assert endpoint_plan.capabilities.valid_request_generatable is True
    assert len(endpoint_plan.cases) >= 3
    assert [case.level for case in endpoint_plan.cases] == [
        "STARTUP",
        "OPENAPI_CONTRACT",
        "HERMETIC_HTTP",
        "HERMETIC_HTTP",
    ][: len(endpoint_plan.cases)]

    categories = [case.category for case in endpoint_plan.cases]
    assert "openapi" in categories
    assert "happy_path" in categories
    assert "validation" in categories

    legacy_endpoint = plan.endpoints[0]
    assert legacy_endpoint.deprecated is True
    assert legacy_endpoint.level == "HERMETIC_ENDPOINT_CONTRACT"

    persisted = persist_test_plan(structure={}, plan=plan)
    payload = json.loads(persisted[".poc_it/test_plan.json"])
    assert payload["schema_version"] == 3
    assert "endpoint_plans" in payload
    assert "endpoints" in payload


def test_build_test_plan_keeps_readable_limitations_when_not_overrideable():
    runtime_contracts = {
        "allowed_dependency_overrides": [],
        "endpoints": [
            {
                "path": "/items",
                "method": "GET",
                "depends_imports": ["app.main.get_repo"],
            }
        ],
        "openapi": {
            "openapi": "3.1.0",
            "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
        },
        "imports": ["requests"],
    }

    structure = {
        RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False, indent=2),
    }
    plan = build_test_plan(
        structure=structure,
        spec={"openapi": runtime_contracts["openapi"]},
        mode="PARCIAL",
    )

    endpoint_plan = plan.endpoint_plans[0]
    assert any("overrideables" in limitation or "integración externa" in limitation.lower() for limitation in endpoint_plan.limitations)
    assert any(case.level == "OPENAPI_CONTRACT" for case in endpoint_plan.cases)
