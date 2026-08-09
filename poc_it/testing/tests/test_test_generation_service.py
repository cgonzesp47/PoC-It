import json

from poc_it.testing.semantic_enrichment import SemanticEnrichmentResult, SemanticEnrichmentService
from poc_it.testing.test_generation_service import TestGenerationService


def test_service_generates_contract_first_patch_with_traceability():
    service = TestGenerationService(feature_flag_enabled=True, fallback_to_legacy_minimal=False)

    runtime_contracts = {
        "allowed_dependency_overrides": [],
        "endpoints": [
            {
                "path": "/items",
                "method": "GET",
                "operation_id": "get:/items",
                "status_code": 200,
                "response_json_required_keys": ["id"],
            }
        ],
        "openapi": {
            "openapi": "3.1.0",
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "get:/items",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        },
    }
    runtime_facts = {"app_module": "app.main"}

    result = service.generate(
        project_structure={"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
        runtime_contracts=runtime_contracts,
        runtime_facts=runtime_facts,
        spec={"mode": "PARCIAL"},
        mode="PARCIAL",
        project_name="Demo",
    )

    assert result.ok is True
    assert result.strategy == "contract-first"
    assert ".poc_it/test_plan.json" in result.structure_patch
    assert ".poc_it/test_validation_report.json" in result.structure_patch
    report = json.loads(result.structure_patch[".poc_it/test_validation_report.json"])
    assert report["strategy"] == "contract-first"
    assert report["rendered_tests"]["strategy_by_file"]["tests/test_startup.py"] == "contract-first"
    assert result.strategy_by_file()["tests/test_startup.py"] == "contract-first"
    legacy_files = {
        "tests/test_smoke_import.py",
        "tests/test_openapi.py",
        "tests/test_endpoint_contracts.py",
        "tests/test_endpoints_hermetic.py",
        "tests/test_endpoints_spec.py",
    }
    assert legacy_files.isdisjoint(result.structure_patch)


def test_service_can_be_disabled_by_feature_flag():
    service = TestGenerationService(feature_flag_enabled=False, fallback_to_legacy_minimal=True)

    result = service.generate(
        project_structure={"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
        runtime_contracts=None,
        runtime_facts=None,
        spec={"mode": "PARCIAL"},
        mode="PARCIAL",
        project_name="Demo",
    )

    assert result.ok is True
    assert result.strategy == "minimal-fallback"
    assert "tests/test_startup.py" in result.structure_patch
    assert result.strategy_by_file()["tests/test_startup.py"] == "minimal-fallback"
    assert "tests/test_smoke_import.py" not in result.structure_patch


def test_service_merges_semantic_enrichment_into_persisted_plan():
    service = TestGenerationService(
        feature_flag_enabled=True,
        fallback_to_legacy_minimal=False,
        semantic_enrichment_service=SemanticEnrichmentService(
            provider=lambda context: {
                "semantic_cases": [
                    {
                        "operation_id": "get:/items",
                        "case_id": "get__items__semantic_stock",
                        "category": "semantic",
                        "level": "SEMANTIC",
                        "expected_status": 200,
                        "response_fields": ["stock"],
                        "evidence": ["semantic_case"],
                        "confidence": 0.9,
                    }
                ],
                "stateful_scenarios": [],
                "dependency_behaviors": [],
                "expected_interactions": [],
                "uncertainties": [],
            }
        ),
    )

    runtime_contracts = {
        "allowed_dependency_overrides": [],
        "endpoints": [
            {
                "path": "/items",
                "method": "GET",
                "operation_id": "get:/items",
                "status_code": 200,
                "response_json_required_keys": ["id"],
            }
        ],
        "openapi": {
            "openapi": "3.1.0",
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "get:/items",
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer"}, "stock": {"type": "integer"}},
                                            "required": ["id"],
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
        },
    }

    result = service.generate(
        project_structure={"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
        runtime_contracts=runtime_contracts,
        runtime_facts={"app_module": "app.main"},
        spec={"mode": "PARCIAL"},
        mode="PARCIAL",
        project_name="Demo",
    )

    assert result.ok is True
    plan = json.loads(result.structure_patch[".poc_it/test_plan.json"])
    endpoint_plan = next(item for item in plan["endpoint_plans"] if item["operation_id"] == "get:/items")
    semantic_case = next(item for item in endpoint_plan["cases"] if item["case_id"] == "get__items__semantic_stock")
    assert semantic_case["level"] == "SEMANTIC"
    assert any(assertion["kind"] == "JSON_HAS_KEYS" and assertion["expected"] == ["stock"] for assertion in semantic_case["assertions"])
    assert "semantic_case" in endpoint_plan["evidence"]


def test_service_ignores_invalid_semantic_merge_and_keeps_base_plan():
    service = TestGenerationService(
        feature_flag_enabled=True,
        fallback_to_legacy_minimal=False,
        semantic_enrichment_service=SemanticEnrichmentService(
            provider=lambda context: {
                "semantic_cases": [],
                "stateful_scenarios": [
                    {
                        "scenario_id": "broken-scenario",
                        "operations": ["post:/missing"],
                        "confidence": 0.8,
                    }
                ],
                "dependency_behaviors": [],
                "expected_interactions": [],
                "uncertainties": [],
            }
        ),
    )

    runtime_contracts = {
        "allowed_dependency_overrides": [],
        "endpoints": [
            {
                "path": "/items",
                "method": "GET",
                "operation_id": "get:/items",
                "status_code": 200,
                "response_json_required_keys": ["id"],
            }
        ],
        "openapi": {
            "openapi": "3.1.0",
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "get:/items",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        },
    }

    result = service.generate(
        project_structure={"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
        runtime_contracts=runtime_contracts,
        runtime_facts={"app_module": "app.main"},
        spec={"mode": "PARCIAL"},
        mode="PARCIAL",
        project_name="Demo",
    )

    assert result.ok is True
    plan = json.loads(result.structure_patch[".poc_it/test_plan.json"])
    assert plan["scenario_plans"] == []
    assert any(
        "semantic enrichment ignored" in warning and "post:/missing" in warning
        for warning in result.warnings
    )
