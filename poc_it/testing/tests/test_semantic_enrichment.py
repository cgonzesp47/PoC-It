from __future__ import annotations

import json

from poc_it.testing.semantic_enrichment import (
    SemanticEnrichmentContext,
    SemanticEnrichmentService,
    SemanticEnrichmentValidationError,
)
from poc_it.testing.test_generation_service import TestGenerationService


def _build_context() -> SemanticEnrichmentContext:
    return SemanticEnrichmentContext(
        scope_id="router:products",
        normalized_openapi={
            "paths": {
                "/products": {
                    "post": {
                        "operationId": "post:/products",
                        "responses": {
                            "201": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                            }
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
                "/products/{product_id}": {
                    "get": {
                        "operationId": "get:/products/{product_id}",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "name": {"type": "string"},
                                            }
                                        }
                                    }
                                }
                            },
                            "404": {"description": "not found"},
                        },
                    }
                },
            }
        },
        handlers={
            "post:/products": "def create_product(...): ...",
            "get:/products/{product_id}": "def get_product(...): ...",
        },
        dependencies={
            "post:/products": ["app.dependencies.product_repo"],
            "get:/products/{product_id}": ["app.dependencies.product_repo"],
        },
        observed_methods={
            "post:/products": ["add_product", "commit"],
            "get:/products/{product_id}": ["find_product"],
        },
        related_models={
            "post:/products": ["ProductCreate", "ProductRead"],
            "get:/products/{product_id}": ["ProductRead"],
        },
        deterministic_cases={
            "post:/products": [{"expected_status": 201}],
            "get:/products/{product_id}": [{"expected_status": 200}],
        },
    )


def test_semantic_enrichment_accepts_valid_structured_json():
    context = _build_context()
    service = SemanticEnrichmentService(
        provider=lambda ctx: json.dumps(
            {
                "semantic_cases": [
                    {
                        "operation_id": "post:/products",
                        "expected_status": 201,
                        "response_fields": ["id", "name"],
                        "confidence": 0.9,
                        "evidence": ["deterministic_create_case"],
                    }
                ],
                "stateful_scenarios": [
                    {
                        "name": "create then read",
                        "operations": ["post:/products", "get:/products/{product_id}"],
                        "confidence": 0.8,
                        "evidence": ["shared_product_id"],
                    }
                ],
                "dependency_behaviors": [
                    {
                        "dependency": "app.dependencies.product_repo",
                        "method": "add_product",
                        "mode": "return_entity",
                    }
                ],
                "expected_interactions": [
                    {
                        "operation_id": "post:/products",
                        "dependency": "app.dependencies.product_repo",
                        "method": "add_product",
                    }
                ],
                "uncertainties": [
                    {
                        "operation_id": "get:/products/{product_id}",
                        "reason": "404 branch may depend on repository return none",
                    }
                ],
            }
        )
    )

    result = service.enrich(context)

    assert result.warnings == []
    assert result.semantic_cases[0]["operation_id"] == "post:/products"
    assert result.stateful_scenarios[0]["operations"] == ["post:/products", "get:/products/{product_id}"]
    assert result.dependency_behaviors[0]["method"] == "add_product"


def test_semantic_enrichment_rejects_invented_operation_status_or_fields():
    context = _build_context()

    bad_operation_service = SemanticEnrichmentService(
        provider=lambda ctx: {
            "semantic_cases": [{"operation_id": "delete:/ghost", "expected_status": 204, "confidence": 1.0}],
            "stateful_scenarios": [],
            "dependency_behaviors": [],
            "expected_interactions": [],
            "uncertainties": [],
        }
    )
    try:
        bad_operation_service.enrich(context)
        raise AssertionError("Expected SemanticEnrichmentValidationError")
    except SemanticEnrichmentValidationError as exc:
        assert "unknown operation" in str(exc)

    bad_status_service = SemanticEnrichmentService(
        provider=lambda ctx: {
            "semantic_cases": [{"operation_id": "post:/products", "expected_status": 500, "confidence": 1.0}],
            "stateful_scenarios": [],
            "dependency_behaviors": [],
            "expected_interactions": [],
            "uncertainties": [],
        }
    )
    try:
        bad_status_service.enrich(context)
        raise AssertionError("Expected SemanticEnrichmentValidationError")
    except SemanticEnrichmentValidationError as exc:
        assert "incompatible status" in str(exc)

    bad_field_service = SemanticEnrichmentService(
        provider=lambda ctx: {
            "semantic_cases": [
                {
                    "operation_id": "post:/products",
                    "expected_status": 201,
                    "response_fields": ["invented_field"],
                    "confidence": 1.0,
                }
            ],
            "stateful_scenarios": [],
            "dependency_behaviors": [],
            "expected_interactions": [],
            "uncertainties": [],
        }
    )
    try:
        bad_field_service.enrich(context)
        raise AssertionError("Expected SemanticEnrichmentValidationError")
    except SemanticEnrichmentValidationError as exc:
        assert "invents response field" in str(exc)


def test_semantic_enrichment_rejects_unknown_methods_or_non_isolable_dependencies():
    context = _build_context()
    service = SemanticEnrichmentService(
        provider=lambda ctx: {
            "semantic_cases": [],
            "stateful_scenarios": [],
            "dependency_behaviors": [
                {
                    "dependency": "app.dependencies.unknown_repo",
                    "method": "invent_method",
                }
            ],
            "expected_interactions": [],
            "uncertainties": [],
        }
    )

    try:
        service.enrich(context)
        raise AssertionError("Expected SemanticEnrichmentValidationError")
    except SemanticEnrichmentValidationError as exc:
        assert "non-isolable dependency" in str(exc) or "unknown method" in str(exc)


def test_test_generation_service_degrades_safely_when_semantic_enrichment_fails():
    runtime_contracts = {
        "endpoints": [
            {
                "path": "/products",
                "method": "POST",
                "status_code": 201,
                "sample_request": {"name": "Keyboard"},
                "depends_imports": ["app.dependencies.product_repo"],
                "response_json_required_keys": ["id", "name"],
            }
        ],
        "openapi": {
            "paths": {
                "/products": {
                    "post": {
                        "operationId": "post:/products",
                        "responses": {
                            "201": {
                                "content": {
                                    "application/json": {
                                        "schema": {"properties": {"id": {}, "name": {}}}
                                    }
                                }
                            }
                        },
                    }
                }
            }
        },
    }

    service = TestGenerationService(
        feature_flag_enabled=True,
        fallback_to_legacy_minimal=False,
        semantic_enrichment_service=SemanticEnrichmentService(
            provider=lambda ctx: {
                "semantic_cases": [{"operation_id": "ghost:/missing", "expected_status": 200, "confidence": 1.0}],
                "stateful_scenarios": [],
                "dependency_behaviors": [],
                "expected_interactions": [],
                "uncertainties": [],
            }
        ),
    )

    result = service.generate(
        project_structure={},
        runtime_contracts=runtime_contracts,
        runtime_facts={},
        spec=runtime_contracts["openapi"],
        mode="PARCIAL",
        project_name="TmpProject",
    )

    assert result.ok is True
    assert ".poc_it/test_plan.json" in result.structure_patch
    assert ".poc_it/semantic_enrichment.json" in result.structure_patch
    semantic_payload = json.loads(result.structure_patch[".poc_it/semantic_enrichment.json"])
    assert semantic_payload["semantic_cases"] == []
    assert "tests/test_startup.py" in result.structure_patch
