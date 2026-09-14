from __future__ import annotations

import json

from poc_it.orquestacion.test_plan import build_test_plan
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH


def _happy_path_case(plan):
    endpoint = plan.endpoint_plans[0]
    return next((case for case in endpoint.cases if case.category == "happy_path"), None)


def test_overrideable_dependency_gets_auto_mocked_when_project_has_external_risk():
    """Regresión: sin `dependency_behaviors` explícitos (semantic enrichment nunca configurado en
    producción), un endpoint con una dependencia overrideable perteneciente a un proyecto con SDK
    de red/credenciales (p.ej. Google Drive) quedaba sin ningún mock -> la dependencia real se
    ejecutaba de verdad en el test hermético y reventaba (500)."""
    runtime_contracts = {
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
        "imports": [
            "googleapiclient.discovery.build",
            "google.oauth2.service_account.Credentials",
        ],
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

    plan = build_test_plan(
        structure={RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False)},
        spec={"openapi": runtime_contracts["openapi"]},
        mode="PARCIAL",
    )

    happy_case = _happy_path_case(plan)
    assert happy_case is not None
    assert happy_case.level == "HERMETIC_HTTP"
    assert any(
        behavior.dependency_fqn == "app.integrations.google_drive_api.build_client"
        and behavior.action == "provide_auto"
        for behavior in happy_case.dependency_setup
    )


def test_overrideable_dependency_without_external_risk_is_not_auto_mocked():
    """Cuando la dependencia inyectada es un fake/objeto en memoria SIN SDK de red/credenciales
    (p.ej. un repositorio in-memory), debe seguir ejecutándose de verdad en el test: sustituirla
    por un MagicMock genérico rompería aserciones de contenido de respuesta que el fake real sí
    puede satisfacer."""
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

    plan = build_test_plan(
        structure={RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False)},
        spec={"openapi": runtime_contracts["openapi"]},
        mode="PARCIAL",
    )

    happy_case = _happy_path_case(plan)
    assert happy_case is not None
    assert happy_case.dependency_setup == []


def test_explicit_dependency_behavior_is_not_overridden_by_auto_mock():
    """Si ya existe un `dependency_behaviors` explícito (p.ej. producido por semantic enrichment)
    para una dependencia de riesgo externo, el fallback determinista no debe añadir un segundo
    override redundante para la misma dependencia."""
    runtime_contracts = {
        "endpoints": [
            {
                "path": "/upload",
                "method": "POST",
                "status_code": 200,
                "sample_request": {"filename": "test.txt"},
                "depends_imports": ["app.integrations.google_drive_api.build_client"],
                "dependency_behaviors": [
                    {
                        "dependency_fqn": "app.integrations.google_drive_api.build_client",
                        "method_name": "upload",
                        "action": "return",
                        "value": {"id": "abc"},
                    }
                ],
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

    plan = build_test_plan(
        structure={RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False)},
        spec={"openapi": runtime_contracts["openapi"]},
        mode="PARCIAL",
    )

    happy_case = _happy_path_case(plan)
    assert happy_case is not None
    matching = [
        behavior
        for behavior in happy_case.dependency_setup
        if behavior.dependency_fqn == "app.integrations.google_drive_api.build_client"
    ]
    assert len(matching) == 1
    assert matching[0].action == "return"
