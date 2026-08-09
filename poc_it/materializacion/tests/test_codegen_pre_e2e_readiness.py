from __future__ import annotations

from poc_it.materializacion.codegen.models import GeneratedFile
from poc_it.materializacion.codegen.project_validation import classify_generated_project
from poc_it.materializacion.generador_artefactos import (
    _is_materializable_status,
    finalize_codegen_classification,
)


def test_local_project_is_pending_before_runtime_and_materializable_after_green_pytest() -> None:
    files = [
        GeneratedFile(
            path="app/api/endpoints/health.py",
            content=(
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n\n"
                "@router.get('/health')\n"
                "async def health():\n"
                "    return {'ok': True}\n"
            ),
        ),
        GeneratedFile(
            path="tests/test_health.py",
            content=(
                "from fastapi.testclient import TestClient\n"
                "from app.main import app\n\n"
                "def test_health():\n"
                "    client = TestClient(app)\n"
                "    response = client.get('/health')\n"
                "    assert response.status_code == 200\n"
            ),
        ),
    ]
    contracts = [
        {
            "path": "app/api/endpoints/health.py",
            "kind": "endpoint",
            "endpoint": {"path": "/health", "methods": ["GET"]},
        },
        {
            "path": "tests/test_health.py",
            "kind": "test",
        },
    ]
    spec = {
        "endpoints": [
            {
                "path": "/health",
                "methods": ["GET"],
                "actions": [],
            }
        ]
    }

    preliminary = classify_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
        static_validation_passed=True,
        runtime_tests_passed=None,
    )

    assert preliminary["status"] == "pending_runtime_tests"
    assert _is_materializable_status(preliminary["status"]) is False

    final = finalize_codegen_classification(
        preliminary_result={
            "files": [file.__dict__ for file in files],
            "spec": spec,
            "validation_report": {
                "file_contracts": contracts,
                "codegen_status": preliminary["status"],
                "external_connectivity_verified": preliminary[
                    "external_connectivity_verified"
                ],
                "runtime_tests_passed": preliminary["runtime_tests_passed"],
            },
            "codegen_status": preliminary["status"],
            "materializable": False,
        },
        runtime_tests_passed=True,
    )

    assert final["codegen_status"] == "valid_local"
    assert final["materializable"] is True
    assert final["runtime_tests_passed"] is True


def test_external_project_is_pending_before_runtime_and_skeleton_after_green_pytest() -> None:
    files = [
        GeneratedFile(
            path="app/api/endpoints/upload.py",
            content=(
                "from fastapi import APIRouter\n"
                "from app.integrations.cloud_storage import upload_asset\n"
                "router = APIRouter()\n\n"
                "@router.post('/upload')\n"
                "async def upload_file(payload: dict):\n"
                "    upload_asset(payload)\n"
                "    return {'ok': True}\n"
            ),
        ),
        GeneratedFile(
            path="app/core/config.py",
            content=(
                "def get_settings():\n"
                "    return type('S', (), {'CLOUD_STORAGE_API_KEY': 'x'})()\n"
                "CLOUD_STORAGE_API_KEY = 'x'\n"
            ),
        ),
        GeneratedFile(
            path="app/integrations/cloud_storage.py",
            content=(
                "from app.core.config import get_settings\n"
                "import vendor_sdk.client_factory\n\n"
                "def upload_asset(payload):\n"
                "    settings = get_settings()\n"
                "    client = vendor_sdk.client_factory.create(\n"
                "        api_key=settings.CLOUD_STORAGE_API_KEY,\n"
                "    )\n"
                "    return payload\n"
            ),
        ),
        GeneratedFile(
            path="tests/test_upload.py",
            content=(
                "from fastapi.testclient import TestClient\n"
                "from app.main import app\n"
                "from app.api.endpoints import upload as upload_module\n\n"
                "def test_endpoint(monkeypatch):\n"
                "    calls = []\n"
                "    def fake_upload_asset(payload):\n"
                "        calls.append(payload)\n"
                "        return {'id': '1'}\n"
                "    monkeypatch.setattr(upload_module, 'upload_asset', fake_upload_asset)\n"
                "    client = TestClient(app)\n"
                "    response = client.post('/upload', json={'name': 'x'})\n"
                "    assert response.status_code in (200, 201)\n"
                "    assert calls\n"
            ),
        ),
    ]
    contracts = [
        {
            "path": "app/api/endpoints/upload.py",
            "kind": "endpoint",
            "endpoint": {"path": "/upload", "methods": ["POST"]},
            "required_internal_calls": [
                {
                    "module": "app.integrations.cloud_storage",
                    "symbol": "upload_asset",
                    "action_ref": "upload_asset",
                    "required": True,
                }
            ],
            "allowed_imports": [{"module": "app.integrations.cloud_storage"}],
        },
        {
            "path": "app/core/config.py",
            "kind": "config",
            "config_keys": [{"name": "CLOUD_STORAGE_API_KEY"}],
        },
        {
            "path": "app/integrations/cloud_storage.py",
            "kind": "integration",
            "integration_refs": ["cloud_storage"],
            "owned_action_refs": ["upload_asset"],
            "provided_interfaces": [
                {
                    "symbol": "upload_asset",
                    "kind": "function",
                    "action_ref": "upload_asset",
                    "parameters": [{"name": "payload", "required": True}],
                }
            ],
            "external_dependencies": [
                {
                    "package": "vendor-sdk",
                    "import_roots": ["vendor_sdk"],
                }
            ],
            "configuration": [{"key": "CLOUD_STORAGE_API_KEY"}],
            "allowed_imports": [{"import_root": "vendor_sdk"}],
        },
        {
            "path": "tests/test_upload.py",
            "kind": "test",
        },
    ]
    spec = {
        "endpoints": [
            {
                "path": "/upload",
                "methods": ["POST"],
                "actions": [
                    {
                        "id": "upload_asset",
                        "kind": "external_call",
                        "required": True,
                        "integration_ref": "cloud_storage",
                    }
                ],
            }
        ]
    }

    preliminary = classify_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
        static_validation_passed=True,
        runtime_tests_passed=None,
    )

    assert preliminary["status"] == "pending_runtime_tests"
    assert _is_materializable_status(preliminary["status"]) is False
    assert preliminary["external_connectivity_verified"] is False

    final = finalize_codegen_classification(
        preliminary_result={
            "files": [file.__dict__ for file in files],
            "spec": spec,
            "validation_report": {
                "file_contracts": contracts,
                "codegen_status": preliminary["status"],
                "external_connectivity_verified": preliminary[
                    "external_connectivity_verified"
                ],
                "runtime_tests_passed": preliminary["runtime_tests_passed"],
            },
            "codegen_status": preliminary["status"],
            "materializable": False,
        },
        runtime_tests_passed=True,
    )

    assert final["codegen_status"] == "valid_integration_skeleton"
    assert final["materializable"] is True
    assert final["runtime_tests_passed"] is True
    assert final["external_connectivity_verified"] is False


def test_invalid_repair_candidate_remains_invalid_and_non_materializable() -> None:
    files = [
        GeneratedFile(
            path="app/api/endpoints/upload.py",
            content=(
                "from fastapi import APIRouter\n"
                "from app.integrations.cloud_storage import upload_asset\n"
                "router = APIRouter()\n\n"
                "@router.post('/upload')\n"
                "async def upload_file(payload: dict):\n"
                "    upload_asset(payload)\n"
                "    return {'ok': True}\n"
            ),
        ),
        GeneratedFile(
            path="app/integrations/cloud_storage.py",
            content=(
                "def upload_asset(payload):\n"
                "    pass\n"
            ),
        ),
    ]
    contracts = [
        {
            "path": "app/api/endpoints/upload.py",
            "kind": "endpoint",
            "endpoint": {"path": "/upload", "methods": ["POST"]},
            "required_internal_calls": [
                {
                    "module": "app.integrations.cloud_storage",
                    "symbol": "upload_asset",
                    "action_ref": "upload_asset",
                    "required": True,
                }
            ],
        },
        {
            "path": "app/integrations/cloud_storage.py",
            "kind": "integration",
            "integration_refs": ["cloud_storage"],
            "owned_action_refs": ["upload_asset"],
            "provided_interfaces": [
                {
                    "symbol": "upload_asset",
                    "kind": "function",
                    "action_ref": "upload_asset",
                    "parameters": [{"name": "payload", "required": True}],
                }
            ],
        },
    ]
    spec = {
        "endpoints": [
            {
                "path": "/upload",
                "methods": ["POST"],
                "actions": [
                    {
                        "id": "upload_asset",
                        "kind": "external_call",
                        "required": True,
                        "integration_ref": "cloud_storage",
                    }
                ],
            }
        ]
    }

    result = classify_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
        static_validation_passed=False,
        runtime_tests_passed=None,
    )

    assert result["status"] == "invalid"
    assert _is_materializable_status(result["status"]) is False
