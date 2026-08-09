from __future__ import annotations

from poc_it.generador.file_contracts import (
    build_file_contracts_from_spec,
    file_contracts_to_dict,
)
from poc_it.generador.implementation_contracts import (
    build_implementation_contracts_from_spec,
)


def _build_spec() -> dict:
    return {
        "files": [
            "app/main.py",
            "app/api/router.py",
            "app/api/endpoints/health.py",
            "app/api/endpoints/upload.py",
            "app/core/config.py",
            "requirements.txt",
            "tests/test_upload.py",
        ],
        "source": {"prompt": "test"},
        "env": [
            {"name": "DRIVE_FOLDER_ID"},
            {"name": "UNUSED_TOKEN"},
        ],
        "configuration": [
            {
                "key": "DRIVE_FOLDER_ID",
                "delivery": "env",
                "required": True,
                "secret": False,
            },
            {
                "key": "UNUSED_TOKEN",
                "delivery": "env",
                "required": True,
                "secret": True,
            },
        ],
        "dependencies": [
            "fastapi",
            "google-auth",
            "google-api-python-client",
        ],
        "technology_signals": [
            {
                "name": "google_drive_sdk",
                "packages": ["google-auth", "google-api-python-client"],
                "import_roots": ["google.auth", "googleapiclient.discovery"],
            },
            {
                "name": "no_import_root_sdk",
                "packages": ["opaque-sdk"],
            },
        ],
        "integrations": [
            {
                "id": "google_drive",
                "name": "Google Drive",
                "kind": "external_api",
                "technology_refs": ["google_drive_sdk"],
                "configuration_refs": ["DRIVE_FOLDER_ID"],
                "implementation_level": "integration_skeleton",
            },
            {
                "id": "unused_integration",
                "name": "Unused Integration",
                "kind": "external_api",
                "technology_refs": ["no_import_root_sdk"],
                "configuration_refs": ["UNUSED_TOKEN"],
                "implementation_level": "integration_skeleton",
            },
        ],
        "test_strategy": {"kind": "hermetic"},
        "persistence": {"required": False},
        "endpoints": [
            {
                "file": "app/api/endpoints/health.py",
                "method": "GET",
                "path": "/health",
                "func": "health",
                "response": {"status_code": 200},
            },
            {
                "file": "app/api/endpoints/upload.py",
                "method": "POST",
                "path": "/upload",
                "func": "upload",
                "request": {"type": "multipart"},
                "integration_refs": ["google_drive"],
                "actions": [
                    {
                        "id": "generate_test_file",
                        "kind": "internal",
                        "description": "Generate test file",
                    },
                    {
                        "id": "upload_to_drive",
                        "kind": "external_call",
                        "integration_ref": "google_drive",
                        "description": "Upload to external storage",
                    },
                ],
                "errors": [
                    {"status_code": 401, "code": "unauthorized"},
                    {"status_code": 403, "code": "forbidden"},
                    {"status_code": 404, "code": "not_found"},
                ],
            },
        ],
    }


def _build_contracts() -> list:
    spec = _build_spec()
    implementation_contracts = build_implementation_contracts_from_spec(spec)
    return build_file_contracts_from_spec(
        spec,
        implementation_contracts=implementation_contracts,
    )


def _by_path(contracts: list) -> dict:
    return {contract.path: contract for contract in contracts}


def test_upload_receives_actions_errors_and_integration() -> None:
    upload = _by_path(_build_contracts())["app/api/endpoints/upload.py"]
    action_ids = [item["id"] for item in upload.actions]
    error_codes = [item["status_code"] for item in upload.errors]

    assert "generate_test_file" in action_ids
    assert "upload_to_drive" in action_ids
    assert 401 in error_codes
    assert 403 in error_codes
    assert 404 in error_codes
    assert "google_drive" in upload.integration_refs


def test_health_does_not_receive_unrelated_integration_or_configuration() -> None:
    health = _by_path(_build_contracts())["app/api/endpoints/health.py"]

    assert health.integration_refs == []
    assert health.configuration == []
    assert "DRIVE_FOLDER_ID" not in [
        item.get("key") for item in health.configuration
    ]


def test_config_receives_global_configuration() -> None:
    config = _by_path(_build_contracts())["app/core/config.py"]
    keys = [item["key"] for item in config.configuration]

    assert "DRIVE_FOLDER_ID" in keys
    assert "UNUSED_TOKEN" in keys
    assert "Declare configuration 'DRIVE_FOLDER_ID' using delivery 'env'" in config.must_implement
    assert "Expose required configuration 'DRIVE_FOLDER_ID'" in config.must_implement


def test_requirements_receives_runtime_dependencies() -> None:
    req = _by_path(_build_contracts())["requirements.txt"]

    assert "google-auth" in req.dependencies
    assert "google-api-python-client" in req.dependencies
    assert "Declare runtime dependency 'google-auth'" in req.must_implement
    assert "Declare runtime dependency 'google-api-python-client'" in req.must_implement


def test_endpoint_does_not_receive_other_integration_configuration() -> None:
    upload = _by_path(_build_contracts())["app/api/endpoints/upload.py"]
    keys = [item["key"] for item in upload.configuration]

    assert "DRIVE_FOLDER_ID" in keys
    assert "UNUSED_TOKEN" not in keys


def test_must_not_for_external_integration_prohibits_fake_success() -> None:
    upload = _by_path(_build_contracts())["app/api/endpoints/upload.py"]

    assert "Do not return a hard-coded success response" in upload.must_not
    assert "Do not embed credentials or secret values" in upload.must_not
    assert "Do not open external connections at import time" in upload.must_not


def test_must_not_for_health_prohibits_external_dependency() -> None:
    health = _by_path(_build_contracts())["app/api/endpoints/health.py"]

    assert "Do not make this endpoint depend on external systems" in health.must_not


def test_allowed_imports_use_import_roots_not_package_names() -> None:
    upload = _by_path(_build_contracts())["app/api/endpoints/upload.py"]

    assert "app.core.config" in upload.allowed_imports
    assert "google.auth" in upload.allowed_imports
    assert "googleapiclient.discovery" in upload.allowed_imports
    assert "google-api-python-client" not in upload.allowed_imports
    assert "google-auth" not in upload.allowed_imports


def test_package_without_import_roots_is_not_converted_to_import() -> None:
    upload = _by_path(_build_contracts())["app/api/endpoints/upload.py"]

    assert "opaque-sdk" not in upload.allowed_imports


def test_output_is_deterministic() -> None:
    first = file_contracts_to_dict(_build_contracts())
    second = file_contracts_to_dict(_build_contracts())

    assert first == second
