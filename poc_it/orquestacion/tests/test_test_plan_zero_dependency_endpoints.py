from __future__ import annotations

import json

from poc_it.orquestacion.test_plan import build_test_plan
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH


def test_endpoint_without_dependencies_is_promoted_to_hermetic_http():
    """Regresión: un endpoint sin ninguna dependencia externa (p.ej. GET /health) quedaba
    estructuralmente capado a OPENAPI_CONTRACT para siempre, porque "cero dependencias" se
    trataba como "no overrideable" en vez de "trivialmente hermético"."""
    runtime_contracts = {
        "endpoints": [
            {
                "path": "/health",
                "method": "GET",
                "status_code": 200,
                "sample_request": None,
                "depends_imports": [],
                "response_json_required_keys": ["status"],
            }
        ],
        "allowed_dependency_overrides": [],
        "imports": [
            "app.integrations.google_drive_api.upload_to_google_drive",
            "googleapiclient.discovery.build",
        ],
        "openapi": {
            "paths": {
                "/health": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {"schema": {"required": ["status"]}}
                                }
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

    endpoint = plan.endpoint_plans[0]
    assert endpoint.capabilities.dependencies_overrideable is True

    happy_case = next((case for case in endpoint.cases if case.category == "happy_path"), None)
    assert happy_case is not None, "expected a HERMETIC_HTTP happy_path case for a zero-dependency endpoint"
    assert happy_case.level == "HERMETIC_HTTP"
    assert any(assertion.kind == "STATUS_EQUALS" and assertion.expected == 200 for assertion in happy_case.assertions)


def test_endpoint_without_dependencies_does_not_inherit_unrelated_network_risk():
    """Regresión: el riesgo de integración externa se infería de los imports de TODO el
    proyecto y se aplicaba a cualquier endpoint sin overrides, aunque ese endpoint concreto no
    tuviera ninguna dependencia ni llamada propia (p.ej. GET /health en un proyecto que también
    integra con Google Drive en otro módulo)."""
    runtime_contracts = {
        "endpoints": [
            {
                "path": "/health",
                "method": "GET",
                "status_code": 200,
                "sample_request": None,
                "depends_imports": [],
                "observed_calls": [],
                "response_json_required_keys": ["status"],
            }
        ],
        "allowed_dependency_overrides": [],
        "imports": [
            "app.integrations.google_drive_api.upload_to_google_drive",
            "googleapiclient.discovery.build",
            "google.oauth2.service_account.Credentials",
        ],
        "openapi": {
            "paths": {
                "/health": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {"schema": {"required": ["status"]}}
                                }
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

    endpoint = plan.endpoint_plans[0]
    assert not any("Riesgo de integración externa" in limitation for limitation in endpoint.limitations)


def test_build_test_plan_falls_back_to_openapi_captured_in_runtime_contracts():
    """Regresión: el SPEC determinista nunca conoce el status code real de un endpoint (los
    decoradores de FastAPI rara vez lo declaran explícito), así que `build_test_plan` debe
    consultar el OpenAPI REAL capturado en vivo y persistido en runtime_contracts.json (por
    `reparacion_runtime._persist_openapi_into_runtime_contracts`) cuando el spec no trae uno."""
    runtime_contracts = {
        "endpoints": [
            {
                "path": "/health",
                "method": "GET",
                "status_code": None,
                "sample_request": None,
                "depends_imports": [],
                "response_json_required_keys": None,
            }
        ],
        "allowed_dependency_overrides": [],
        "openapi": {
            "paths": {
                "/health": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {"schema": {"required": ["status"]}}
                                }
                            }
                        }
                    }
                }
            }
        },
    }

    # spec NO trae openapi (caso real: es determinista, construido antes de que exista código).
    plan = build_test_plan(
        structure={RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False)},
        spec={"schema_version": "pocit.spec.v1"},
        mode="PARCIAL",
    )

    endpoint = plan.endpoint_plans[0]
    happy_case = next((case for case in endpoint.cases if case.category == "happy_path"), None)
    assert happy_case is not None, "expected a happy_path case once the real OpenAPI is available"
    assert happy_case.level == "HERMETIC_HTTP"
    assert any(assertion.kind == "STATUS_EQUALS" and assertion.expected == 200 for assertion in happy_case.assertions)


def test_endpoint_with_own_unresolved_dependency_still_flags_risk():
    """El aviso de riesgo debe seguir apareciendo cuando el propio endpoint SÍ toca una
    dependencia que no está en `allowed_dependency_overrides` (caso real: no se pudo overridear)."""
    runtime_contracts = {
        "endpoints": [
            {
                "path": "/upload",
                "method": "POST",
                "status_code": 200,
                "sample_request": {"filename": "test.txt"},
                "depends_imports": ["app.integrations.google_drive_api.upload_to_google_drive"],
                "response_json_required_keys": ["status"],
            }
        ],
        "allowed_dependency_overrides": [],
        "imports": ["googleapiclient.discovery.build"],
        "openapi": {
            "paths": {
                "/upload": {
                    "post": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {"schema": {"required": ["status"]}}
                                }
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

    endpoint = plan.endpoint_plans[0]
    assert endpoint.capabilities.dependencies_overrideable is False
    assert any("Riesgo de integración externa" in limitation for limitation in endpoint.limitations)
