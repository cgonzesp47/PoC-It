from __future__ import annotations

from poc_it.generador.implementation_contracts import build_implementation_contracts_from_spec


def test_google_drive_actions_generate_structured_contract() -> None:
    spec = {
        "technology_signals": [
            {
                "name": "google-api-python-client",
                "package": "google-api-python-client",
            },
            {
                "name": "google-auth",
                "package": "google-auth",
            },
        ],
        "configuration": [
            {
                "key": "DRIVE_FOLDER_ID",
                "required": True,
                "secret": False,
                "delivery": "env",
            }
        ],
        "integrations": [
            {
                "id": "google_drive",
                "name": "Google Drive API",
                "kind": "external_api",
                "implementation_level": "integration_skeleton",
                "authentication": {
                    "mechanism": "ADC",
                },
                "technology_refs": [
                    "google-api-python-client",
                    "google-auth",
                ],
                "configuration_refs": [
                    "DRIVE_FOLDER_ID",
                ],
            }
        ],
        "endpoints": [
            {
                "file": "app/api/endpoints/upload.py",
                "method": "POST",
                "path": "/upload",
                "actions": [
                    {
                        "id": "generate_test_file",
                        "kind": "internal_processing",
                        "description": "Generate a test file",
                        "required": True,
                    },
                    {
                        "id": "upload_to_drive",
                        "kind": "external_call",
                        "description": "Upload the generated file",
                        "required": True,
                        "integration_ref": "google_drive",
                    },
                ],
                "errors": [
                    {
                        "status_code": 401,
                        "code": "authentication_error",
                        "required": True,
                    },
                    {
                        "status_code": 403,
                        "code": "permission_denied",
                        "required": True,
                    },
                    {
                        "status_code": 404,
                        "code": "target_not_found",
                        "required": True,
                    },
                ],
                "integration_refs": ["google_drive"],
            }
        ],
    }

    contract = build_implementation_contracts_from_spec(spec)[0]
    assert contract["capability"] in {"upload_file", "call_external_api"}
    assert contract["capability"] == "upload_file"
    assert contract["external_dependencies"][0]["id"] == "google_drive"
    assert "integration_skeleton" in contract["implementation_levels"]
    assert "technology_signals" in contract["source"]
    assert contract["must_implement"]
    assert contract["must_not"]

    joined = " || ".join(contract["must_implement"])
    assert "generate_test_file" in joined
    assert "upload_to_drive" in joined
    assert "google_drive" in joined
    assert "DRIVE_FOLDER_ID" in joined
    assert "ADC" in joined
    assert "401" in joined
    assert "403" in joined
    assert "404" in joined

    forbidden = " || ".join(contract["must_not"]).lower()
    assert "credentials" in forbidden
    assert "fake success" in forbidden or "hard-coded success" in forbidden
    assert "hermetic tests" in forbidden


def test_health_without_external_dependency_stays_safe() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/health.py",
                "method": "GET",
                "path": "/health",
                "actions": [],
                "integration_refs": [],
            }
        ]
    }
    contract = build_implementation_contracts_from_spec(spec)[0]
    assert contract["external_dependencies"] == []
    assert any("external" in item.lower() for item in contract["must_not"])


def test_structured_crud_actions_map_to_all_resource_capabilities() -> None:
    spec = {
        "endpoints": [
            {"file": "app/api/endpoints/items.py", "method": "POST", "path": "/items", "actions": [{"id": "create_item", "kind": "persistence"}]},
            {"file": "app/api/endpoints/items.py", "method": "GET", "path": "/items", "actions": [{"id": "list_items", "kind": "persistence"}]},
            {"file": "app/api/endpoints/items.py", "method": "GET", "path": "/items/{id}", "actions": [{"id": "get_item", "kind": "persistence"}]},
            {"file": "app/api/endpoints/items.py", "method": "PATCH", "path": "/items/{id}", "actions": [{"id": "update_item", "kind": "persistence"}]},
            {"file": "app/api/endpoints/items.py", "method": "DELETE", "path": "/items/{id}", "actions": [{"id": "delete_item", "kind": "persistence"}]},
        ]
    }
    contracts = build_implementation_contracts_from_spec(spec)
    capabilities = {item["capability"] for item in contracts}
    assert capabilities == {
        "create_resource",
        "list_resources",
        "get_resource",
        "update_resource",
        "delete_resource",
    }


def test_notification_email_maps_to_send_notification() -> None:
    spec = {
        "integrations": [{"id": "mail_provider", "name": "Mail Provider", "kind": "external_api"}],
        "endpoints": [
            {
                "file": "app/api/endpoints/mail.py",
                "method": "POST",
                "path": "/mail/send",
                "actions": [
                    {
                        "id": "send_email",
                        "kind": "notification",
                        "integration_ref": "mail_provider",
                    }
                ],
            }
        ],
    }
    contract = build_implementation_contracts_from_spec(spec)[0]
    assert contract["capability"] == "send_notification"


def test_publish_event_action_maps_to_publish_event() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/events.py",
                "method": "POST",
                "path": "/events",
                "actions": [
                    {
                        "id": "publish_event",
                        "kind": "external_call",
                    }
                ],
            }
        ]
    }
    contract = build_implementation_contracts_from_spec(spec)[0]
    assert contract["capability"] == "publish_event"


def test_validation_token_action_maps_to_authenticate_request() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/auth.py",
                "method": "POST",
                "path": "/auth/verify",
                "actions": [
                    {
                        "id": "verify_token",
                        "kind": "validation",
                    }
                ],
            }
        ]
    }
    contract = build_implementation_contracts_from_spec(spec)[0]
    assert contract["capability"] == "authenticate_request"


def test_technology_without_integrations_is_not_external_dependency() -> None:
    spec = {
        "technology_signals": [{"name": "fastapi"}, {"name": "pydantic"}],
        "endpoints": [
            {
                "file": "app/api/endpoints/x.py",
                "method": "GET",
                "path": "/x",
            }
        ],
    }
    contract = build_implementation_contracts_from_spec(spec)[0]
    assert contract["external_dependencies"] == []
    assert "technology_signals" in contract["source"]


def test_unknown_without_structure_is_safe() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/unknown.py",
                "method": "POST",
                "path": "/do-thing",
            }
        ],
        "persistence": {"required": False},
    }
    contract = build_implementation_contracts_from_spec(spec)[0]
    assert contract["capability"] == "unknown_operation"
    assert contract["implementation_plan"] == [
        "validate_input_if_schema",
        "build_response_from_example_or_status",
    ]
    assert contract["must_implement"]
    assert contract["must_not"]
