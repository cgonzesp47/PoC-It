from __future__ import annotations

from poc_it.generador.implementation_contracts import (
    build_implementation_contracts_from_spec,
    implementation_contracts_for_file,
)


def test_build_returns_one_contract_per_endpoint() -> None:
    spec = {
        "endpoints": [
            {"file": "app/api/endpoints/a.py", "method": "GET", "path": "/a"},
            {"file": "app/api/endpoints/b.py", "method": "POST", "path": "/b"},
        ],
        "persistence": {"required": False},
        "technology_signals": [{"name": "fastapi"}],
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert len(contracts) == 2
    assert all("must_implement" in c and c["must_implement"] for c in contracts)
    assert all("must_not" in c and c["must_not"] for c in contracts)
    assert all("implementation_plan" in c and c["implementation_plan"] for c in contracts)
    assert all("response_strategy" in c and c["response_strategy"] for c in contracts)
    assert all("state_strategy" in c and c["state_strategy"] for c in contracts)
    assert all("adapter_strategy" in c and c["adapter_strategy"] for c in contracts)


def test_crud_is_classified_from_structured_persistence_actions() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/items.py",
                "method": "POST",
                "path": "/items",
                "actions": [{"id": "create_item", "kind": "persistence", "description": "Create an item"}],
            },
            {
                "file": "app/api/endpoints/items.py",
                "method": "GET",
                "path": "/items",
                "actions": [{"id": "list_items", "kind": "persistence", "description": "List items"}],
            },
            {
                "file": "app/api/endpoints/items.py",
                "method": "GET",
                "path": "/items/{id}",
                "actions": [{"id": "get_item", "kind": "persistence", "description": "Get item"}],
            },
            {
                "file": "app/api/endpoints/items.py",
                "method": "PUT",
                "path": "/items/{id}",
                "actions": [{"id": "update_item", "kind": "persistence", "description": "Update item"}],
            },
            {
                "file": "app/api/endpoints/items.py",
                "method": "DELETE",
                "path": "/items/{id}",
                "actions": [{"id": "delete_item", "kind": "persistence", "description": "Delete item"}],
            },
        ],
        "persistence": {"required": False},
    }
    contracts = build_implementation_contracts_from_spec(spec)
    caps = {c["capability"] for c in contracts}
    assert "create_resource" in caps
    assert "list_resources" in caps
    assert "get_resource" in caps
    assert "update_resource" in caps
    assert "delete_resource" in caps


def test_crud_shape_fallback_requires_declared_persistence() -> None:
    spec = {
        "endpoints": [
            {"file": "app/api/endpoints/items.py", "method": "POST", "path": "/items"},
            {"file": "app/api/endpoints/items.py", "method": "GET", "path": "/items"},
            {"file": "app/api/endpoints/items.py", "method": "GET", "path": "/items/{id}"},
            {"file": "app/api/endpoints/items.py", "method": "PUT", "path": "/items/{id}"},
            {"file": "app/api/endpoints/items.py", "method": "DELETE", "path": "/items/{id}"},
        ],
        "persistence": {"required": True},
    }
    contracts = build_implementation_contracts_from_spec(spec)
    caps = {c["capability"] for c in contracts}
    assert "create_resource" in caps
    assert "list_resources" in caps
    assert "get_resource" in caps
    assert "update_resource" in caps
    assert "delete_resource" in caps


def test_upload_is_classified_as_upload_file() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/upload.py",
                "method": "POST",
                "path": "/upload",
                "request": {"type": "multipart"},
            }
        ],
        "persistence": {"required": False},
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["capability"] == "upload_file"
    assert any("metadata" in x.lower() or "file" in x.lower() for x in contracts[0]["must_implement"])


def test_upload_is_preferably_classified_from_structured_external_action() -> None:
    spec = {
        "integrations": [
            {
                "id": "document_storage",
                "name": "Document Storage",
                "kind": "external_api",
                "implementation_level": "integration_skeleton",
            }
        ],
        "endpoints": [
            {
                "file": "app/api/endpoints/upload.py",
                "method": "POST",
                "path": "/upload",
                "actions": [
                    {
                        "id": "upload_document",
                        "kind": "external_call",
                        "integration_ref": "document_storage",
                        "description": "Upload document",
                    }
                ],
            }
        ],
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["capability"] == "upload_file"
    assert contracts[0]["external_dependencies"][0]["id"] == "document_storage"


def test_external_integration_is_classified_as_call_external_api() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/ext.py",
                "method": "POST",
                "path": "/sync",
                "source": {"external_dependencies": [{"name": "SomeAPI", "kind": "http"}]},
            }
        ],
        "persistence": {"required": False},
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["capability"] == "call_external_api"
    assert any("integration" in x.lower() or "external" in x.lower() for x in contracts[0]["must_implement"])
    assert contracts[0]["external_dependencies"] and contracts[0]["external_dependencies"][0]["name"] == "SomeAPI"


def test_unknown_operation_fallback() -> None:
    spec = {
        "endpoints": [
            {"file": "app/api/endpoints/x.py", "method": "POST", "path": "/do-thing"},
        ],
        "persistence": {"required": False},
    }
    contracts = build_implementation_contracts_from_spec(spec)
    assert contracts[0]["capability"] == "unknown_operation"
    assert contracts[0]["implementation_plan"] == [
        "validate_input_if_schema",
        "build_response_from_example_or_status",
    ]


def test_contracts_for_file_filters() -> None:
    spec = {
        "endpoints": [
            {"file": "app/api/endpoints/a.py", "method": "GET", "path": "/a"},
            {"file": "app/api/endpoints/b.py", "method": "GET", "path": "/b"},
        ],
        "persistence": {"required": False},
    }
    contracts = build_implementation_contracts_from_spec(spec)
    a = implementation_contracts_for_file("app/api/endpoints/a.py", contracts)
    b = implementation_contracts_for_file("app/api/endpoints/b.py", contracts)
    assert len(a) == 1
    assert a[0]["path"] == "/a"
    assert len(b) == 1
    assert b[0]["path"] == "/b"
