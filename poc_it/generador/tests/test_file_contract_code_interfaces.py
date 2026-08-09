from __future__ import annotations

from copy import deepcopy

from poc_it.generador.file_contracts import (
    build_file_contracts_from_spec,
    file_contracts_to_dict,
)
from poc_it.generador.file_contracts_validation import validate_file_contracts
from poc_it.generador.file_planner import enrich_spec_files_for_implementation
from poc_it.generador.utils_python_names import module_path_from_file_path


def _base_spec() -> dict:
    return {
        "files": [
            "app/main.py",
            "app/api/router.py",
            "app/api/endpoints/upload.py",
            "app/core/config.py",
            "requirements.txt",
        ],
        "dependencies": [],
        "env": [],
        "persistence": {},
        "test_strategy": {},
        "source": {},
        "configuration": [
            {
                "key": "DRIVE_FOLDER_ID",
                "required": True,
                "secret": False,
                "delivery": "env",
            }
        ],
        "technology_signals": [
            {
                "name": "google-api-python-client",
                "package": "google-api-python-client",
            }
        ],
        "integrations": [
            {
                "id": "google_drive",
                "name": "Google Drive",
                "required": True,
                "technology_refs": ["google-api-python-client"],
                "configuration_refs": ["DRIVE_FOLDER_ID"],
                "authentication": {"mechanism": "ADC"},
                "implementation_level": "integration_skeleton",
            }
        ],
        "implementation_files": [],
        "endpoints": [
            {
                "method": "POST",
                "path": "/upload",
                "file": "app/api/endpoints/upload.py",
                "func": "upload",
                "request": {
                    "type": "json",
                    "schema": {
                        "filename": {"type": "string"},
                        "content": {"type": "string"},
                        "config_token": {"type": "string"},
                    },
                },
                "response": {"json_example": {"ok": True}},
                "actions": [
                    {
                        "id": "generate_test_file",
                        "kind": "internal_processing",
                        "required": True,
                        "description": "Generate test content",
                    },
                    {
                        "id": "upload_to_drive",
                        "kind": "external_call",
                        "integration_ref": "google_drive",
                        "required": True,
                        "description": "Upload generated content",
                    },
                ],
                "errors": [],
                "integration_refs": ["google_drive"],
            }
        ],
    }


def _build_file_contracts(spec: dict | None = None) -> tuple[dict, list[dict]]:
    working_spec = deepcopy(spec or _base_spec())
    planned = enrich_spec_files_for_implementation(
        working_spec,
        implementation_contracts=[],
    )
    contracts = build_file_contracts_from_spec(
        planned.spec,
        implementation_contracts=[],
    )
    return planned.spec, file_contracts_to_dict(contracts)


def _find_contract(contracts: list[dict], path: str) -> dict:
    for contract in contracts:
        if contract.get("path") == path:
            return contract
    raise AssertionError(f"missing contract for {path}")


def _error_codes(errors) -> set[str]:
    return {item.code for item in errors}


def test_external_action_generates_interface_in_integration_and_required_call_in_endpoint() -> None:
    spec, contracts = _build_file_contracts()

    integration_contract = _find_contract(contracts, "app/integrations/google_drive.py")
    endpoint_contract = _find_contract(contracts, "app/api/endpoints/upload.py")

    assert any(
        item["symbol"] == "upload_to_drive"
        for item in integration_contract["provided_interfaces"]
    )
    assert {
        item["symbol"] for item in endpoint_contract["required_internal_calls"]
    } == {"upload_to_drive"}


def test_internal_action_generates_interface_in_endpoint() -> None:
    _, contracts = _build_file_contracts()
    endpoint_contract = _find_contract(contracts, "app/api/endpoints/upload.py")

    provided = {
        item["symbol"]: item for item in endpoint_contract["provided_interfaces"]
    }
    assert "generate_test_file" in provided
    assert provided["generate_test_file"]["action_ref"] == "generate_test_file"


def test_symbols_are_derived_from_action_id_only() -> None:
    spec = _base_spec()
    spec["endpoints"][0]["actions"][0]["id"] = "Generate Test File"
    spec["endpoints"][0]["actions"][0]["description"] = "Descripción distinta"
    spec["integrations"][0]["name"] = "Otro proveedor"

    _, contracts = _build_file_contracts(spec)
    endpoint_contract = _find_contract(contracts, "app/api/endpoints/upload.py")
    symbols = {
        item["symbol"] for item in endpoint_contract["provided_interfaces"]
    }

    assert "generate_test_file" in symbols


def test_collisions_are_rejected() -> None:
    spec = _base_spec()
    spec["endpoints"][0]["actions"] = [
        {
            "id": "Upload To Drive",
            "kind": "internal_processing",
            "required": True,
            "description": "One",
        },
        {
            "id": "upload_to_drive",
            "kind": "internal_processing",
            "required": True,
            "description": "Two",
        },
    ]

    try:
        _build_file_contracts(spec)
    except ValueError as exc:
        assert "FILE_INTERFACE_SYMBOL_COLLISION" in str(exc)
    else:
        raise AssertionError("expected collision error")


def test_configuration_does_not_appear_as_parameter() -> None:
    _, contracts = _build_file_contracts()
    integration_contract = _find_contract(contracts, "app/integrations/google_drive.py")

    provided = next(
        item
        for item in integration_contract["provided_interfaces"]
        if item["symbol"] == "upload_to_drive"
    )
    parameter_names = [item["name"] for item in provided["parameters"]]

    assert "config_token" not in parameter_names
    assert "filename" in parameter_names
    assert "content" in parameter_names


def test_packages_or_providers_do_not_affect_symbol_name() -> None:
    spec = _base_spec()
    spec["integrations"][0]["name"] = "Google Drive SDK"
    spec["technology_signals"][0]["name"] = "custom-provider-package"

    _, contracts = _build_file_contracts(spec)
    integration_contract = _find_contract(contracts, "app/integrations/google_drive.py")

    assert any(
        item["symbol"] == "upload_to_drive"
        for item in integration_contract["provided_interfaces"]
    )


def test_internal_module_is_converted_correctly_from_path() -> None:
    assert module_path_from_file_path("app/integrations/google_drive.py") == (
        "app.integrations.google_drive"
    )
    assert module_path_from_file_path("app/integrations/__init__.py") == (
        "app.integrations"
    )


def test_every_required_action_has_owner() -> None:
    spec, contracts = _build_file_contracts()

    errors = validate_file_contracts(
        spec=spec,
        implementation_contracts=[],
        file_contracts=contracts,
    )

    assert "FILE_ACTION_OWNER_MISSING" not in _error_codes(errors)


def test_output_is_deterministic() -> None:
    spec_a, contracts_a = _build_file_contracts()
    spec_b, contracts_b = _build_file_contracts()

    assert spec_a == spec_b
    assert contracts_a == contracts_b
