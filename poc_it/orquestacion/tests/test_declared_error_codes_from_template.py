from __future__ import annotations

import json

from poc_it.orquestacion.contract_test_renderer import render_tests_from_test_plan
from poc_it.orquestacion.test_plan import build_test_plan
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH
from poc_it.testing.domain.models import TEST_PLAN_PATH


def _base_runtime_contracts() -> dict:
    return {
        "endpoints": [
            {
                "path": "/upload",
                "method": "POST",
                "status_code": 200,
                "sample_request": {"filename": "test.txt"},
                "depends_imports": ["app.integrations.google_drive_api.build_client"],
                "response_json_required_keys": ["status"],
            }
        ],
        "allowed_dependency_overrides": ["app.integrations.google_drive_api.build_client"],
        "imports": ["googleapiclient.discovery.build"],
        "openapi": {
            "paths": {
                "/upload": {
                    "post": {
                        "responses": {
                            "200": {
                                "content": {"application/json": {"schema": {"required": ["status"]}}}
                            }
                        }
                    }
                }
            }
        },
    }


def _base_spec(runtime_contracts: dict) -> dict:
    return {
        "openapi": runtime_contracts["openapi"],
        "endpoints": [
            {
                "path": "/upload",
                "method": "POST",
                "errors": [
                    {
                        "status_code": 409,
                        "code": "ALREADY_EXISTS",
                        "description": "El fichero ya existe en el destino.",
                        "required": True,
                        "source": "explicit",
                    }
                ],
            }
        ],
    }


def test_declared_template_error_produces_hermetic_case_when_external_risk():
    """Un error declarado por el usuario en la plantilla (`ContratoAPI.errors`, status_code 409)
    debe traducirse en un caso HERMETIC_HTTP ejecutable en Fase B cuando el endpoint tiene una
    dependencia overrideable con riesgo de integración externa detectado."""
    runtime_contracts = _base_runtime_contracts()
    spec = _base_spec(runtime_contracts)

    plan = build_test_plan(
        structure={RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False)},
        spec=spec,
        mode="PARCIAL",
    )

    endpoint = plan.endpoint_plans[0]
    declared_case = next((c for c in endpoint.cases if c.category == "declared_error"), None)
    assert declared_case is not None
    assert declared_case.level == "HERMETIC_HTTP"
    assert declared_case.request.expected_status == 409
    assert declared_case.request.allowed_statuses == [409]

    raising_behavior = next(
        b for b in declared_case.dependency_setup if b.action == "provide_auto_raise"
    )
    assert raising_behavior.dependency_fqn == "app.integrations.google_drive_api.build_client"
    assert raising_behavior.exception_type == "HTTPException"
    assert raising_behavior.exception_status_code == 409


def test_declared_template_error_renders_as_executable_pytest_case():
    """El caso HERMETIC_HTTP generado para un error declarado debe renderizarse (no descartarse
    como ocurría cuando el renderer solo tomaba el primer caso HERMETIC_HTTP por endpoint)."""
    runtime_contracts = _base_runtime_contracts()
    spec = _base_spec(runtime_contracts)

    plan = build_test_plan(
        structure={RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False)},
        spec=spec,
        mode="PARCIAL",
    )
    from poc_it.testing.planning.test_plan_builder import persist_test_plan

    structure = persist_test_plan(structure={}, plan=plan)

    patch = render_tests_from_test_plan(
        structure=structure,
        runtime_contracts=runtime_contracts,
        runtime_facts=None,
    )

    rendered = patch.get("tests/test_http_behavior.py", "")
    assert "def test_contract_post_upload__post_upload__declared_error_409(" in rendered
    assert "HTTPException(status_code=409" in rendered
    assert "from fastapi import HTTPException" in rendered
    assert "assert resp.status_code == 409" in rendered

    # También debe seguir presente el caso happy_path (regresión: antes solo se renderizaba
    # el primer caso HERMETIC_HTTP del endpoint, descartando el resto).
    assert "def test_contract_post_upload__post_upload__happy_path(" in rendered


def test_declared_error_not_generated_without_external_risk_dependency():
    """Sin riesgo de integración externa detectado, no generamos el caso: no hay forma segura de
    forzar el fallo sin adivinar el nombre real del método invocado (ver StrictDouble)."""
    runtime_contracts = {
        "endpoints": [
            {
                "path": "/items",
                "method": "POST",
                "status_code": 201,
                "sample_request": {"name": "demo"},
                "depends_imports": ["app.main.get_repository"],
                "response_json_required_keys": ["id", "name"],
            }
        ],
        "allowed_dependency_overrides": ["app.main.get_repository"],
        "openapi": {
            "paths": {
                "/items": {
                    "post": {
                        "responses": {
                            "201": {
                                "content": {"application/json": {"schema": {"required": ["id", "name"]}}}
                            }
                        }
                    }
                }
            }
        },
    }
    spec = {
        "openapi": runtime_contracts["openapi"],
        "endpoints": [
            {
                "path": "/items",
                "method": "POST",
                "errors": [
                    {"status_code": 409, "code": "DUPLICATE", "description": "ya existe", "required": True}
                ],
            }
        ],
    }

    plan = build_test_plan(
        structure={RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False)},
        spec=spec,
        mode="PARCIAL",
    )

    endpoint = plan.endpoint_plans[0]
    assert not any(c.category == "declared_error" for c in endpoint.cases)
