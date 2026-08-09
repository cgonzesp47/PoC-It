from __future__ import annotations

from poc_it.materializacion.codegen.models import GeneratedFile
from poc_it.materializacion.codegen.semantic_validation import (
    validate_generated_file_semantics,
)


def _codes(issues):
    return {issue.code for issue in issues}


def test_integration_real_minimal_passes() -> None:
    file_contract = {
        "kind": "integration",
        "path": "app/integrations/google_drive.py",
        "allowed_imports": [{"import_root": "googleapiclient.discovery"}],
        "provided_interfaces": [
            {
                "symbol": "upload_to_drive",
                "kind": "function",
                "action_ref": "upload_to_drive",
                "parameters": [{"name": "payload", "required": True}],
            }
        ],
        "implementation_levels": ["integration_skeleton"],
    }
    generated_file = GeneratedFile(
        path="app/integrations/google_drive.py",
        content="""
from app.core.config import get_settings
from googleapiclient.discovery import build

def upload_to_drive(payload):
    settings = get_settings()
    service = build("drive", "v3", credentials=settings.credentials)
    return service.files().create(body=payload).execute()
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert _codes(issues) == set()


def test_integration_stub_detection_for_pass_and_not_implemented_and_constant_return() -> None:
    file_contract = {
        "kind": "integration",
        "path": "app/integrations/google_drive.py",
        "provided_interfaces": [
            {
                "symbol": "upload_to_drive",
                "kind": "function",
                "action_ref": "upload_to_drive",
                "parameters": [],
            }
        ],
    }

    for content in [
        "def upload_to_drive():\n    pass\n",
        "def upload_to_drive():\n    raise NotImplementedError()\n",
        "def upload_to_drive():\n    return {'status': 'success'}\n",
    ]:
        issues = validate_generated_file_semantics(
            generated_file=GeneratedFile(
                path="app/integrations/google_drive.py",
                content=content,
            ),
            file_contract=file_contract,
        )
        assert "CODE_REQUIRED_ACTION_STUB" in _codes(issues) or "CODE_EXTERNAL_OPERATION_CONSTANT_RETURN" in _codes(issues)


def test_integration_detects_client_created_at_import_time() -> None:
    file_contract = {
        "kind": "integration",
        "path": "app/integrations/google_drive.py",
        "provided_interfaces": [],
    }
    generated_file = GeneratedFile(
        path="app/integrations/google_drive.py",
        content="""
from app.core.config import get_settings
from sdk import build_client

settings = get_settings()
client = build_client()

def upload_to_drive(payload):
    return client.send(payload)
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "CODE_EXTERNAL_CLIENT_CREATED_AT_IMPORT" in _codes(issues)


def test_endpoint_detects_missing_required_internal_call() -> None:
    file_contract = {
        "kind": "endpoint",
        "path": "app/api/endpoints/upload.py",
        "endpoint": {
            "methods": ["POST"],
            "path": "/upload",
            "request": {"content_type": "application/json"},
        },
        "required_internal_calls": [
            {
                "module": "app.integrations.google_drive",
                "symbol": "upload_to_drive",
                "action_ref": "upload_to_drive",
                "required": True,
            }
        ],
        "allowed_imports": [{"module": "app.integrations.google_drive"}],
    }
    generated_file = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content="""
from fastapi import APIRouter

router = APIRouter()

@router.post("/upload")
async def upload_file():
    return {"status": "ok"}
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "CODE_INTERNAL_CALL_IMPORT_MISSING" in _codes(issues)


def test_endpoint_detects_fake_success() -> None:
    file_contract = {
        "kind": "endpoint",
        "path": "app/api/endpoints/upload.py",
        "endpoint": {"methods": ["POST"], "path": "/upload"},
    }
    generated_file = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content="""
from fastapi import APIRouter

router = APIRouter()

@router.post("/upload")
async def upload_file():
    return {"status": "success"}
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "CODE_FAKE_SUCCESS_RESPONSE" in _codes(issues)


def test_endpoint_detects_route_mismatch_and_json_vs_multipart() -> None:
    file_contract = {
        "kind": "endpoint",
        "path": "app/api/endpoints/upload.py",
        "endpoint": {
            "methods": ["POST"],
            "path": "/upload",
            "request": {"content_type": "application/json"},
        },
    }
    generated_file = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content="""
from fastapi import APIRouter, UploadFile, File

router = APIRouter()

@router.post("/wrong")
async def upload_file(file: UploadFile = File(...)):
    return {"done": True}
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )
    codes = _codes(issues)

    assert "CODE_ENDPOINT_ROUTE_MISMATCH" in codes
    assert "CODE_ENDPOINT_REQUEST_TYPE_MISMATCH" in codes


def test_health_endpoint_stays_isolated() -> None:
    file_contract = {
        "kind": "endpoint",
        "path": "app/api/endpoints/health.py",
        "endpoint": {"methods": ["GET"], "path": "/health"},
        "allowed_imports": [],
    }
    generated_file = GeneratedFile(
        path="app/api/endpoints/health.py",
        content="""
from fastapi import APIRouter
from app.integrations.google_drive import upload_to_drive

router = APIRouter()

@router.get("/health")
async def health():
    return {"ok": True}
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "CODE_HEALTH_IMPORTS_INTEGRATION" in _codes(issues)


def test_config_detects_pydantic_incompatibility() -> None:
    file_contract = {
        "kind": "config",
        "path": "app/core/config.py",
        "dependencies": ["pydantic-settings>=2.0"],
        "config_keys": [{"name": "google_api_key"}],
    }
    generated_file = GeneratedFile(
        path="app/core/config.py",
        content="""
from pydantic import BaseSettings

class Settings(BaseSettings):
    pass
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    codes = _codes(issues)
    assert "CODE_CONFIG_BASESETTINGS_INCOMPATIBLE" in codes
    assert "CODE_CONFIG_KEY_MISSING" in codes
    assert "CODE_CONFIG_GET_SETTINGS_MISSING" in codes


def test_config_detects_declared_external_imports_without_provider_hardcoding() -> None:
    file_contract = {
        "kind": "config",
        "path": "app/core/config.py",
        "config_keys": [{"name": "storage_bucket"}],
        "external_dependencies": [
            {"import_roots": ["vendor_sdk.storage"]},
        ],
    }
    generated_file = GeneratedFile(
        path="app/core/config.py",
        content="""
from vendor_sdk.storage import Client

def get_settings():
    return {"storage_bucket": "demo"}
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "CODE_CONFIG_EXTERNAL_IMPORT" in _codes(issues)


def test_config_detects_non_trivial_import_time_calls_without_provider_hardcoding() -> None:
    file_contract = {
        "kind": "config",
        "path": "app/core/config.py",
        "config_keys": [{"name": "storage_bucket"}],
    }
    generated_file = GeneratedFile(
        path="app/core/config.py",
        content="""
def load_remote_defaults():
    return {"storage_bucket": "demo"}

DEFAULTS = load_remote_defaults()

def get_settings():
    return DEFAULTS
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "CODE_CONFIG_IMPORT_TIME_FAILURE" in _codes(issues)


def test_requirements_detects_missing_and_invalid_lines() -> None:
    file_contract = {
        "kind": "requirements",
        "path": "requirements.txt",
        "dependencies": ["fastapi==0.111.0", "google-api-python-client>=2.0"],
        "allowed_imports": [
            {
                "import_root": "googleapiclient",
                "package": "google-api-python-client",
            }
        ],
    }
    generated_file = GeneratedFile(
        path="requirements.txt",
        content="""
# requirements
fastapi==0.111.0
googleapiclient
explanation line
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    codes = _codes(issues)
    assert "CODE_REQUIREMENT_MISSING" in codes
    assert "CODE_REQUIREMENTS_MARKDOWN" in codes
    assert "CODE_REQUIREMENTS_IMPORT_ROOT_USED" in codes
    assert "CODE_REQUIREMENTS_INVALID_LINE" in codes


def test_endpoint_required_internal_call_data_flow_supports_positional_arguments() -> None:
    file_contract = {
        "kind": "endpoint",
        "path": "app/api/endpoints/upload.py",
        "endpoint": {
            "methods": ["POST"],
            "path": "/upload",
            "request": {"content_type": "application/json"},
        },
        "required_internal_calls": [
            {
                "module": "app.integrations.storage",
                "symbol": "store_resource",
                "parameters": [
                    {"name": "name", "required": True},
                    {"name": "content", "required": True},
                ],
            }
        ],
        "allowed_imports": [{"module": "app.integrations.storage"}],
    }
    generated_file = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content="""
from fastapi import APIRouter
from app.integrations.storage import store_resource

router = APIRouter()

@router.post("/upload")
async def upload_file(name, content):
    return store_resource(name, content)
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "FILE_CONTRACT_DATA_FLOW_MISMATCH" not in _codes(issues)


def test_endpoint_required_internal_call_data_flow_detects_missing_positional_argument() -> None:
    file_contract = {
        "kind": "endpoint",
        "path": "app/api/endpoints/upload.py",
        "endpoint": {
            "methods": ["POST"],
            "path": "/upload",
            "request": {"content_type": "application/json"},
        },
        "required_internal_calls": [
            {
                "module": "app.integrations.storage",
                "symbol": "store_resource",
                "parameters": [
                    {"name": "name", "required": True},
                    {"name": "content", "required": True},
                ],
            }
        ],
        "allowed_imports": [{"module": "app.integrations.storage"}],
    }
    generated_file = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content="""
from fastapi import APIRouter
from app.integrations.storage import store_resource

router = APIRouter()

@router.post("/upload")
async def upload_file(name):
    return store_resource(name)
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "FILE_CONTRACT_DATA_FLOW_MISMATCH" in _codes(issues)


def test_endpoint_required_internal_call_data_flow_supports_mixed_arguments() -> None:
    file_contract = {
        "kind": "endpoint",
        "path": "app/api/endpoints/upload.py",
        "endpoint": {
            "methods": ["POST"],
            "path": "/upload",
            "request": {"content_type": "application/json"},
        },
        "required_internal_calls": [
            {
                "module": "app.integrations.storage",
                "symbol": "store_resource",
                "parameters": [
                    {"name": "name", "required": True},
                    {"name": "content", "required": True},
                ],
            }
        ],
        "allowed_imports": [{"module": "app.integrations.storage"}],
    }
    generated_file = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content="""
from fastapi import APIRouter
from app.integrations.storage import store_resource

router = APIRouter()

@router.post("/upload")
async def upload_file(name, content):
    return store_resource(name, content=content)
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "FILE_CONTRACT_DATA_FLOW_MISMATCH" not in _codes(issues)


def test_endpoint_inline_required_action_stub_is_not_flagged_as_required_interface_stub() -> None:
    file_contract = {
        "kind": "endpoint",
        "path": "app/api/endpoints/upload.py",
        "endpoint": {
            "methods": ["POST"],
            "path": "/upload",
        },
        "provided_interfaces": [
            {
                "symbol": "generate_test_file",
                "kind": "function",
                "action_ref": "generate_test_file",
                "parameters": [],
                "interface_required": False,
            }
        ],
    }
    generated_file = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content="""
from fastapi import APIRouter

router = APIRouter()

def generate_test_file():
    pass

@router.post("/upload")
async def upload_file():
    return {"ok": True}
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "CODE_REQUIRED_ACTION_STUB" not in _codes(issues)


def test_integration_required_interface_stub_is_still_flagged() -> None:
    file_contract = {
        "kind": "integration",
        "path": "app/integrations/storage.py",
        "provided_interfaces": [
            {
                "symbol": "store_resource",
                "kind": "function",
                "action_ref": "store_resource",
                "parameters": [],
                "interface_required": True,
            }
        ],
    }
    generated_file = GeneratedFile(
        path="app/integrations/storage.py",
        content="""
def store_resource():
    pass
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=GeneratedFile(
            path="app/integrations/storage.py",
            content=generated_file.content,
        ),
        file_contract=file_contract,
    )

    assert "CODE_REQUIRED_ACTION_STUB" in _codes(issues)
