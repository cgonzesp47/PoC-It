from __future__ import annotations

from poc_it.materializacion.generador_artefactos import (
    _is_materializable_status,
    finalize_codegen_classification,
)


def test_materializable_status_matrix() -> None:
    cases = [
        ("valid_local", True),
        ("valid_integration_skeleton", True),
        ("pending_runtime_tests", False),
        ("invalid", False),
        ("", False),
        ("unknown", False),
    ]

    for status, expected in cases:
        assert _is_materializable_status(status) is expected


def test_finalize_classification_promotes_valid_local_after_runtime_green() -> None:
    preliminary_result = {
        "files": [
            {
                "path": "app/api/endpoints/health.py",
                "content": (
                    "from fastapi import APIRouter\n"
                    "router = APIRouter()\n\n"
                    "@router.get('/health')\n"
                    "async def health():\n"
                    "    return {'ok': True}\n"
                ),
            },
            {
                "path": "tests/test_health.py",
                "content": (
                    "from fastapi.testclient import TestClient\n"
                    "from app.main import app\n\n"
                    "def test_health():\n"
                    "    client = TestClient(app)\n"
                    "    response = client.get('/health')\n"
                    "    assert response.status_code == 200\n"
                ),
            },
        ],
        "spec": {
            "endpoints": [
                {
                    "path": "/health",
                    "methods": ["GET"],
                    "actions": [],
                }
            ]
        },
        "validation_report": {
            "file_contracts": [
                {
                    "path": "app/api/endpoints/health.py",
                    "kind": "endpoint",
                    "endpoint": {"path": "/health", "methods": ["GET"]},
                },
                {
                    "path": "tests/test_health.py",
                    "kind": "test",
                },
            ],
            "codegen_status": "pending_runtime_tests",
            "external_connectivity_verified": False,
            "runtime_tests_passed": None,
        },
        "codegen_status": "pending_runtime_tests",
        "materializable": False,
    }

    result = finalize_codegen_classification(
        preliminary_result=preliminary_result,
        runtime_tests_passed=True,
    )

    assert result["codegen_status"] == "valid_local"
    assert result["materializable"] is True
    assert result["runtime_tests_passed"] is True


def test_finalize_classification_keeps_invalid_non_materializable_and_preserves_files() -> None:
    preliminary_result = {
        "files": [
            {
                "path": "app/api/endpoints/upload.py",
                "content": (
                    "from fastapi import APIRouter\n"
                    "router = APIRouter()\n\n"
                    "@router.post('/upload')\n"
                    "async def upload_file(payload: dict):\n"
                    "    return {'ok': True}\n"
                ),
            }
        ],
        "spec": {
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
        },
        "validation_report": {
            "file_contracts": [
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
                }
            ],
            "codegen_status": "pending_runtime_tests",
            "external_connectivity_verified": False,
            "runtime_tests_passed": None,
        },
        "codegen_status": "pending_runtime_tests",
        "materializable": False,
    }

    result = finalize_codegen_classification(
        preliminary_result=preliminary_result,
        runtime_tests_passed=False,
    )

    assert result["codegen_status"] == "invalid"
    assert result["materializable"] is False
    assert result["files"]
    assert result["runtime_tests_passed"] is False
