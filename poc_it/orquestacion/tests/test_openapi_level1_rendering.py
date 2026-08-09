import json

from poc_it.orquestacion.contract_test_renderer import render_tests_from_test_plan
from poc_it.orquestacion.test_plan import TEST_PLAN_PATH


def test_renderer_emits_openapi_contract_and_request_validation_suites_per_operation():
    plan = {
        "mode": "OPENAPI",
        "strategy": "contract-first",
        "endpoints": [
            {
                "method": "GET",
                "path": "/items/{item_id}",
                "parameters": [
                    {"name": "item_id", "in": "path", "required": True},
                    {"name": "expand", "in": "query", "required": False},
                ],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Item"}
                            }
                        }
                    }
                },
            },
            {
                "method": "POST",
                "path": "/items",
                "parameters": [],
                "request_body": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ItemCreate"}
                        }
                    }
                },
                "responses": {
                    "201": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Item"}
                            }
                        }
                    },
                    "422": {"description": "validation error"},
                },
                "invalid_request": {
                    "json_body": {},
                },
            },
        ],
        "endpoint_plans": [],
    }

    patch = render_tests_from_test_plan(
        structure={TEST_PLAN_PATH: json.dumps(plan)},
        runtime_contracts=None,
        runtime_facts=None,
    )

    assert "tests/test_openapi_contract.py" in patch
    assert "tests/test_request_validation.py" in patch
    assert "tests/test_openapi.py" not in patch

    openapi_contract = patch["tests/test_openapi_contract.py"]
    request_validation = patch["tests/test_request_validation.py"]

    assert "def test_openapi_contract_get_items_item_id()" in openapi_contract
    assert "def test_openapi_contract_post_items()" in openapi_contract
    assert "assert '/items/{item_id}' in paths" in openapi_contract
    assert "assert 'get' in" not in openapi_contract
    assert "assert ('item_id', 'path') in param_index" in openapi_contract
    assert "assert '200' in responses" in openapi_contract
    assert "assert '201' in responses" in openapi_contract
    assert "assert 'Item' in components" in openapi_contract
    assert "assert 'ItemCreate' in components" in openapi_contract
    assert "request_body = operation.get('requestBody')" in openapi_contract

    assert "def test_request_validation_post_items()" in request_validation
    assert "client.request('POST', '/items', json={})" in request_validation
    assert "assert response.status_code == 422" in request_validation


def test_renderer_keeps_level1_coverage_for_non_invocable_endpoints():
    plan = {
        "mode": "OPENAPI",
        "strategy": "contract-first",
        "endpoints": [
            {
                "method": "DELETE",
                "path": "/external-resource/{resource_id}",
                "parameters": [
                    {"name": "resource_id", "in": "path", "required": True},
                ],
                "responses": {"204": {"description": "deleted"}},
            }
        ],
        "endpoint_plans": [],
    }

    patch = render_tests_from_test_plan(
        structure={TEST_PLAN_PATH: json.dumps(plan)},
        runtime_contracts=None,
        runtime_facts=None,
    )

    assert "tests/test_openapi_contract.py" in patch
    assert "tests/test_request_validation.py" in patch
    assert "tests/test_endpoint_contracts.py" not in patch

    openapi_contract = patch["tests/test_openapi_contract.py"]
    assert "def test_openapi_contract_delete_external_resource_resource_id()" in openapi_contract
    assert "assert '204' in responses" in openapi_contract


def test_pytest_ini_prioritizes_level1_suites():
    patch = render_tests_from_test_plan(
        structure={},
        runtime_contracts=None,
        runtime_facts=None,
    )

    pytest_ini = patch["pytest.ini"]
    assert "test_openapi_contract.py" in pytest_ini
    assert "test_request_validation.py" in pytest_ini
    assert "test_openapi.py" not in pytest_ini
