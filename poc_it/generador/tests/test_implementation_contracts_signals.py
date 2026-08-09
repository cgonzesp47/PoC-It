from __future__ import annotations

from poc_it.generador.implementation_contracts import build_implementation_contracts_from_spec


def test_explicit_capability_is_respected() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/x.py",
                "method": "POST",
                "path": "/x",
                # Legacy fallback: endpoint.capability
                "capability": "receive_webhook",
            }
        ]
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["capability"] == "receive_webhook"


def test_operation_upload_maps_to_upload_file() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/u.py",
                "method": "POST",
                "path": "/documents",
                # Legacy fallback: endpoint.operation
                "operation": "upload",
            }
        ]
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["capability"] == "upload_file"


def test_operation_webhook_maps_to_receive_webhook() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/w.py",
                "method": "POST",
                "path": "/callbacks",
                # Legacy fallback: endpoint.operation
                "operation": "webhook",
            }
        ]
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["capability"] == "receive_webhook"


def test_operation_publish_maps_to_publish_event() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/e.py",
                "method": "POST",
                "path": "/events",
                # Legacy fallback: endpoint.operation
                "operation": "publish",
            }
        ]
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["capability"] == "publish_event"


def test_operation_auth_maps_to_authenticate_request() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/auth.py",
                "method": "POST",
                "path": "/token/verify",
                # Legacy fallback: endpoint.operation
                "operation": "verify-token",
            }
        ]
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["capability"] == "authenticate_request"


def test_resource_extraction_ignores_api_and_version_segments() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/p.py",
                "method": "GET",
                "path": "/api/v1/products/{id}",
            }
        ]
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["resource"] == "products"


def test_technology_signals_do_not_become_external_dependencies() -> None:
    spec = {
        "technology_signals": [{"name": "requests"}, {"name": "fastapi"}],
        "endpoints": [
            {
                "file": "app/api/endpoints/x.py",
                "method": "GET",
                "path": "/x",
            }
        ],
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["external_dependencies"] == []
    assert "technology_signals" in contracts[0]["source"]


def test_declared_external_service_is_in_external_dependencies() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/ext.py",
                "method": "POST",
                "path": "/sync",
                "source": {"external_dependencies": [{"name": "SomeAPI", "kind": "external_api"}]},
            }
        ]
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["external_dependencies"]
    assert contracts[0]["external_dependencies"][0]["name"] == "SomeAPI"


def test_consume_cache_auth_have_specific_must_implement() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/c1.py",
                "method": "POST",
                "path": "/queue/consume",
                # Legacy fallback: endpoint.operation
                "operation": "consume",
            },
            {
                "file": "app/api/endpoints/c2.py",
                "method": "GET",
                "path": "/cache",
                # Legacy fallback: endpoint.operation
                "operation": "cache-lookup",
            },
            {
                "file": "app/api/endpoints/c3.py",
                "method": "POST",
                "path": "/cache",
                # Legacy fallback: endpoint.operation
                "operation": "cache-write",
            },
            {
                "file": "app/api/endpoints/c4.py",
                "method": "POST",
                "path": "/auth/verify",
                # Legacy fallback: endpoint.operation
                "operation": "verify-token",
            },
        ]
    }
    contracts = build_implementation_contracts_from_spec(spec)
    by_cap = {c["capability"]: c for c in contracts}
    assert by_cap["consume_event"]["must_implement"]
    assert by_cap["cache_lookup"]["must_implement"]
    assert by_cap["cache_write"]["must_implement"]
    assert by_cap["authenticate_request"]["must_implement"]


def test_unknown_operation_has_safe_implementation_plan() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/unk.py",
                "method": "POST",
                "path": "/do-thing",
                "operation": "",
            }
        ]
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["capability"] == "unknown_operation"
    assert contracts[0]["implementation_plan"] == [
        "validate_input_if_schema",
        "build_response_from_example_or_status",
    ]
