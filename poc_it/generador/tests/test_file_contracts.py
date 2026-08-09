from __future__ import annotations

import pytest

from poc_it.generador.file_contracts import (
    build_file_contracts_from_spec,
    file_contracts_to_dict,
)
from poc_it.generador.implementation_contracts import (
    build_implementation_contracts_from_spec,
)
from poc_it.generador.request_ir import build_request_ir_from_context
from poc_it.generador.spec_builder import build_spec_from_request_ir


def _fixture_context_crud_productos() -> dict:
    # Fixture determinista (NO depende de output/_debug).
    return {
        "objetivo_tecnico": "API CRUD básica de productos en FastAPI",
        "funcionalidades_clave": ["crud productos"],
        "integraciones_externas": [],
        "restricciones_tecnicas": [
            "Sin autenticación",
            "Configurar conexión vía variables de entorno",
        ],
        "contratos_api": [],
        "contratos_api_explicitos": [],
        "contratos_api_propuestos": [
            {
                "method": "POST",
                "path": "/productos",
                "request": {
                    "type": "json",
                    "schema_hint": {
                        "nombre": "string",
                        "descripcion": "string",
                        "precio": "number",
                        "disponible": "boolean",
                    },
                    "evidence": ["fixture"],
                },
                "response": {"json_example": {"id": "number"}},
                "evidence": ["fixture"],
            },
            {
                "method": "GET",
                "path": "/productos",
                "request": {"type": "none"},
                "response": {"json_example": [{"id": "number"}]},
                "evidence": ["fixture"],
            },
            {
                "method": "GET",
                "path": "/productos/{id}",
                "request": {"type": "none"},
                "response": {"json_example": {"id": "number"}},
                "evidence": ["fixture"],
            },
            {
                "method": "PUT",
                "path": "/productos/{id}",
                "request": {
                    "type": "json",
                    "schema_hint": {
                        "nombre": "string",
                        "descripcion": "string",
                        "precio": "number",
                        "disponible": "boolean",
                    },
                    "evidence": ["fixture"],
                },
                "response": {"json_example": {"id": "number"}},
                "evidence": ["fixture"],
            },
            {
                "method": "DELETE",
                "path": "/productos/{id}",
                "request": {"type": "none"},
                "response": {"json_example": {"ok": True}},
                "evidence": ["fixture"],
            },
        ],
        "capability_coverage": [
            {
                "capability": "crud productos",
                "contract_refs": [
                    "POST /productos",
                    "GET /productos",
                    "GET /productos/{id}",
                    "PUT /productos/{id}",
                    "DELETE /productos/{id}",
                ],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "fixture",
                "assumption": "",
            }
        ],
        "persistence": {"required": False},
        "modo_recomendado": "PARCIAL",
        "framework_objetivo": "fastapi",
    }


def _load_fixture_spec_ok() -> dict:
    req_ir = build_request_ir_from_context(
        _fixture_context_crud_productos(),
        descripcion_global="x",
    )
    return build_spec_from_request_ir(req_ir)


def _fixture_spec_endpoint_contracts() -> dict:
    return {
        "files": [
            "app/__init__.py",
            "app/main.py",
            "app/api/__init__.py",
            "app/api/router.py",
            "app/api/endpoints/__init__.py",
            "app/api/endpoints/items.py",
            "app/api/endpoints/upload.py",
            "app/core/__init__.py",
            "app/core/config.py",
            "requirements.txt",
            "README.md",
        ],
        "dependencies": [
            "fastapi",
            "pydantic",
            "google-api-python-client",
        ],
        "env": [{"name": "ENVIRONMENT", "required": False}],
        "persistence": {"required": False},
        "test_strategy": {"kind": "smoke"},
        "source": {"origin": "test"},
        "integrations": [
            {
                "id": "google_drive",
                "name": "Google Drive",
                "kind": "external_api",
                "implementation_level": "real_integration",
                "configuration_refs": ["DRIVE_FOLDER_ID"],
            },
            {
                "id": "slack",
                "name": "Slack",
                "kind": "external_api",
                "implementation_level": "integration_skeleton",
                "configuration_refs": ["SLACK_TOKEN"],
            },
        ],
        "configuration": [
            {
                "key": "DRIVE_FOLDER_ID",
                "kind": "env",
                "required": True,
                "description": "Folder destino",
                "secret": True,
                "value": "should-not-be-copied",
            },
            {
                "key": "SLACK_TOKEN",
                "kind": "env",
                "required": True,
                "description": "Slack token",
                "value": "should-not-be-copied",
            },
        ],
        "endpoints": [
            {
                "method": "GET",
                "path": "/items",
                "file": "app/api/endpoints/items.py",
                "func": "list_items",
                "request": {"type": "none"},
                "response": {"type": "json"},
            },
            {
                "method": "POST",
                "path": "/items",
                "file": "app/api/endpoints/items.py",
                "func": "create_item",
                "request": {"type": "json"},
                "response": {"type": "json"},
            },
            {
                "method": "POST",
                "path": "/upload",
                "file": "app/api/endpoints/upload.py",
                "func": "upload_file",
                "request": {"type": "multipart"},
                "response": {"type": "json"},
                "actions": [
                    {
                        "id": "generate_test_file",
                        "kind": "internal_processing",
                        "description": "Generate test file",
                    },
                    {
                        "id": "upload_to_drive",
                        "kind": "external_call",
                        "integration_ref": "google_drive",
                        "description": "Upload file to Google Drive",
                    },
                ],
                "errors": [
                    {"status_code": 401, "code": "unauthorized"},
                    {"status_code": 403, "code": "forbidden"},
                    {"status_code": 404, "code": "folder_not_found"},
                ],
                "integration_refs": ["google_drive"],
            },
        ],
    }


def _find_contract(contracts, path: str):
    target = next((c for c in contracts if c.path == path), None)
    assert target is not None
    return target


def test_file_contracts_basic_invariants():
    spec = _load_fixture_spec_ok()
    contracts = build_file_contracts_from_spec(spec)

    assert {c.path for c in contracts} == {
        p.replace("\\", "/") for p in spec["files"]
    }
    assert len({c.path for c in contracts}) == len(contracts)

    ep_files = {
        str(ep.get("file")).replace("\\", "/")
        for ep in spec.get("endpoints", [])
        if isinstance(ep, dict)
    }
    for f in ep_files:
        eps_for_file = [
            c for c in contracts if c.kind == "endpoint" and c.path == f
        ]
        assert len(eps_for_file) == 1


def test_contract_main_required_symbols_app():
    spec = _load_fixture_spec_ok()
    contracts = build_file_contracts_from_spec(spec)
    main = next((c for c in contracts if c.path == "app/main.py"), None)
    if main is None:
        pytest.skip("Fixture no incluye app/main.py")
    assert main.required_symbols == ["app"]


def test_contract_router_required_symbols_api_router():
    spec = _load_fixture_spec_ok()
    contracts = build_file_contracts_from_spec(spec)
    router = next((c for c in contracts if c.path == "app/api/router.py"), None)
    if router is None:
        pytest.skip("Fixture no incluye app/api/router.py")
    assert router.required_symbols == ["api_router"]


def test_contract_config_has_settings_get_settings():
    spec = _load_fixture_spec_ok()
    contracts = build_file_contracts_from_spec(spec)
    cfg = next((c for c in contracts if c.path == "app/core/config.py"), None)
    if cfg is None:
        pytest.skip("Fixture no incluye app/core/config.py")
    assert "Settings" in cfg.required_symbols
    assert "get_settings" in cfg.required_symbols


def test_contracts_endpoints_productos_have_5_endpoints_if_present():
    spec = _load_fixture_spec_ok()
    contracts = build_file_contracts_from_spec(spec)

    target = next(
        (
            c
            for c in contracts
            if c.kind == "endpoint" and c.path.endswith("productos.py")
        ),
        None,
    )
    if target is None:
        pytest.skip("Fixture no incluye app/api/endpoints/productos.py")
    assert len(target.endpoints) == 5


def test_file_contracts_can_derive_basic_obligations_without_implementation_contracts():
    spec = _fixture_spec_endpoint_contracts()

    contracts = build_file_contracts_from_spec(spec)

    endpoint = _find_contract(contracts, "app/api/endpoints/items.py")
    main = _find_contract(contracts, "app/main.py")

    assert endpoint.implementation_contracts == []
    assert "Validate the declared request" in endpoint.must_implement
    assert "Build the declared response" in endpoint.must_implement
    assert endpoint.must_not == []
    assert endpoint.implementation_plan == []
    assert endpoint.actions == []
    assert endpoint.errors == []
    assert endpoint.integration_refs == []
    assert endpoint.external_dependencies == []
    assert endpoint.implementation_levels == []
    assert endpoint.configuration == []

    assert main.implementation_contracts == []
    assert main.must_implement == []
    assert main.must_not == []
    assert main.implementation_plan == []
    assert main.actions == []
    assert main.errors == []
    assert main.integration_refs == []
    assert main.external_dependencies == []
    assert main.implementation_levels == []
    assert main.configuration == []


def test_file_contracts_assign_implementation_contracts_by_method_and_path():
    spec = _fixture_spec_endpoint_contracts()
    implementation_contracts = [
        {
            "method": "GET",
            "path": "/items",
            "capability": "list_resources",
            "must_implement": ["List items"],
        },
        {
            "method": "POST",
            "path": "/items",
            "capability": "create_resource",
            "must_implement": ["Create item"],
        },
    ]

    contracts = build_file_contracts_from_spec(
        spec,
        implementation_contracts=implementation_contracts,
    )

    items_contract = _find_contract(contracts, "app/api/endpoints/items.py")

    assert len(items_contract.implementation_contracts) == 2
    assert {
        (item["method"], item["path"], item["capability"])
        for item in items_contract.implementation_contracts
    } == {
        ("GET", "/items", "list_resources"),
        ("POST", "/items", "create_resource"),
    }


def test_file_contracts_aggregate_lists_without_duplicates():
    spec = _fixture_spec_endpoint_contracts()
    implementation_contracts = [
        {
            "method": "GET",
            "path": "/items",
            "capability": "list_resources",
            "must_implement": ["validate_request", "load_items"],
            "must_not": ["hardcode_response"],
            "implementation_plan": ["validate_request_schema", "load_items"],
        },
        {
            "method": "POST",
            "path": "/items",
            "capability": "create_resource",
            "must_implement": ["validate_request", "persist_item"],
            "must_not": ["hardcode_response", "skip_persistence"],
            "implementation_plan": [
                "validate_request_schema",
                "persist_item",
                "build_declared_response",
            ],
        },
    ]

    contracts = build_file_contracts_from_spec(
        spec,
        implementation_contracts=implementation_contracts,
    )

    items_contract = _find_contract(contracts, "app/api/endpoints/items.py")

    assert items_contract.must_implement == [
        "validate_request",
        "load_items",
        "persist_item",
    ]
    assert items_contract.must_not == [
        "hardcode_response",
        "skip_persistence",
    ]
    assert items_contract.implementation_plan == [
        "validate_request_schema",
        "load_items",
        "persist_item",
        "build_declared_response",
    ]


def test_file_contracts_keep_actions_errors_and_external_dependencies():
    spec = _fixture_spec_endpoint_contracts()
    implementation_contracts = build_implementation_contracts_from_spec(spec)

    contracts = build_file_contracts_from_spec(
        spec,
        implementation_contracts=implementation_contracts,
    )

    upload_contract = _find_contract(contracts, "app/api/endpoints/upload.py")

    assert [action["id"] for action in upload_contract.actions] == [
        "generate_test_file",
        "upload_to_drive",
    ]
    assert {
        (error["status_code"], error["code"])
        for error in upload_contract.errors
    } == {
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "folder_not_found"),
    }
    assert [dep["id"] for dep in upload_contract.external_dependencies] == [
        "google_drive"
    ]


def test_file_contracts_only_include_related_configuration():
    spec = _fixture_spec_endpoint_contracts()
    implementation_contracts = build_implementation_contracts_from_spec(spec)

    contracts = build_file_contracts_from_spec(
        spec,
        implementation_contracts=implementation_contracts,
    )

    upload_contract = _find_contract(contracts, "app/api/endpoints/upload.py")

    assert [item["key"] for item in upload_contract.configuration] == [
        "DRIVE_FOLDER_ID"
    ]
    assert "value" not in upload_contract.configuration[0]
    assert "SLACK_TOKEN" not in [item["key"] for item in upload_contract.configuration]


def test_file_contracts_raise_for_orphan_implementation_contract():
    spec = _fixture_spec_endpoint_contracts()
    implementation_contracts = [
        {
            "method": "DELETE",
            "path": "/ghost",
            "capability": "delete_resource",
            "must_implement": ["Delete ghost"],
        }
    ]

    with pytest.raises(ValueError):
        build_file_contracts_from_spec(
            spec,
            implementation_contracts=implementation_contracts,
        )


def test_file_contracts_to_dict_preserves_all_new_fields():
    spec = _fixture_spec_endpoint_contracts()
    implementation_contracts = build_implementation_contracts_from_spec(spec)

    contracts = build_file_contracts_from_spec(
        spec,
        implementation_contracts=implementation_contracts,
    )
    payload = file_contracts_to_dict(contracts)

    upload_payload = next(
        item for item in payload if item["path"] == "app/api/endpoints/upload.py"
    )

    assert "implementation_contracts" in upload_payload
    assert "must_implement" in upload_payload
    assert "must_not" in upload_payload
    assert "implementation_plan" in upload_payload
    assert "actions" in upload_payload
    assert "errors" in upload_payload
    assert "integration_refs" in upload_payload
    assert "external_dependencies" in upload_payload
    assert "implementation_levels" in upload_payload
    assert "configuration" in upload_payload

    assert upload_payload["implementation_contracts"]
    assert upload_payload["actions"]
    assert upload_payload["errors"]
    assert upload_payload["external_dependencies"]
    assert upload_payload["configuration"]


def test_inline_required_action_does_not_become_required_symbol() -> None:
    spec = {
        "files": [
            "app/api/endpoints/upload.py",
        ],
        "dependencies": ["fastapi"],
        "env": [],
        "persistence": {"required": False},
        "test_strategy": {"kind": "smoke"},
        "source": {"origin": "test"},
        "integrations": [],
        "configuration": [],
        "endpoints": [
            {
                "method": "POST",
                "path": "/upload",
                "file": "app/api/endpoints/upload.py",
                "func": "upload_file",
                "request": {"type": "json"},
                "response": {"type": "json"},
                "actions": [
                    {
                        "id": "generate_test_file",
                        "kind": "internal_processing",
                        "description": "Generate test file inline",
                    }
                ],
            }
        ],
    }

    contracts = build_file_contracts_from_spec(spec)
    endpoint = _find_contract(contracts, "app/api/endpoints/upload.py")

    assert "generate_test_file" not in endpoint.required_symbols
    provided = next(
        item
        for item in endpoint.provided_interfaces
        if item["symbol"] == "generate_test_file"
    )
    assert provided["interface_required"] is False
    assert provided["owner_path"] == "app/api/endpoints/upload.py"
    assert provided["consumer_path"] == "app/api/endpoints/upload.py"


def test_cross_file_required_action_becomes_required_symbol_on_owner() -> None:
    spec = _fixture_spec_endpoint_contracts()
    spec["files"] = [
        *spec["files"],
        "app/integrations/google_drive.py",
    ]
    spec["implementation_files"] = [
        {
            "path": "app/integrations/google_drive.py",
            "kind": "integration",
            "integration_ref": "google_drive",
        }
    ]
    implementation_contracts = build_implementation_contracts_from_spec(spec)

    contracts = build_file_contracts_from_spec(
        spec,
        implementation_contracts=implementation_contracts,
    )

    integration_contract = _find_contract(contracts, "app/integrations/google_drive.py")
    endpoint_contract = _find_contract(contracts, "app/api/endpoints/upload.py")

    provided = next(
        item
        for item in integration_contract.provided_interfaces
        if item["symbol"] == "upload_to_drive"
    )
    assert provided["interface_required"] is True
    assert provided["owner_path"] == "app/integrations/google_drive.py"
    assert provided["consumer_path"] == "app/api/endpoints/upload.py"
    assert any(
        item["symbol"] == "upload_to_drive"
        for item in endpoint_contract.required_internal_calls
    )
