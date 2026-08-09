from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from poc_it.materializacion.codegen import project_validation
from poc_it.materializacion.codegen.models import GeneratedFile
from poc_it.materializacion.codegen.project_validation import (
    classify_generated_project,
    validate_generated_project,
)
from poc_it.materializacion.codegen.test_planning import (
    build_endpoint_test_contracts,
)


def _codes(issues):
    return {issue.code for issue in issues}


def _base_spec(
    path: str = "/upload",
    include_error: bool = False,
    *,
    external_required: bool = True,
    integration_ref: str = "cloud_storage",
    action_ref: str = "upload_asset",
):
    endpoint = {
        "path": path,
        "methods": ["POST" if path == "/upload" else "GET"],
        "actions": [
            {
                "id": action_ref,
                "kind": "external_call" if external_required else "internal_processing",
                "required": True,
                "integration_ref": integration_ref if external_required else "",
            }
        ],
    }
    if include_error:
        endpoint["errors"] = [{"status_code": 403, "required": True}]
    return {"endpoints": [endpoint]}


def _base_contracts(
    *,
    provider_symbol: str = "upload_asset",
    consumer_symbol: str = "upload_asset",
    include_integration: bool = True,
    config_key: str = "CLOUD_STORAGE_API_KEY",
    include_test_contract: bool = False,
    integration_ref: str = "cloud_storage",
    import_roots: list[str] | None = None,
):
    import_roots = import_roots or ["vendor_sdk"]
    contracts = [
        {
            "path": "app/api/endpoints/upload.py",
            "kind": "endpoint",
            "endpoint": {"path": "/upload", "methods": ["POST"]},
            "required_internal_calls": [
                {
                    "module": "app.integrations.cloud_storage",
                    "symbol": consumer_symbol,
                    "action_ref": "upload_asset",
                    "required": True,
                }
            ],
            "allowed_imports": [{"module": "app.integrations.cloud_storage"}],
        },
        {
            "path": "app/core/config.py",
            "kind": "config",
            "config_keys": [{"name": config_key}],
        },
    ]
    if include_integration:
        contracts.append(
            {
                "path": "app/integrations/cloud_storage.py",
                "kind": "integration",
                "integration_refs": [integration_ref],
                "owned_action_refs": ["upload_asset"],
                "provided_interfaces": [
                    {
                        "symbol": provider_symbol,
                        "kind": "function",
                        "action_ref": "upload_asset",
                        "parameters": [{"name": "payload", "required": True}],
                    }
                ],
                "external_dependencies": [
                    {
                        "package": "vendor-sdk",
                        "import_roots": import_roots,
                    }
                ],
                "configuration": [{"key": config_key}],
                "allowed_imports": [{"import_root": root} for root in import_roots],
            }
        )
    if include_test_contract:
        contracts.append(
            {
                "path": "tests/test_upload.py",
                "kind": "test",
            }
        )
    return contracts


def _base_files(
    *,
    provider_symbol: str = "upload_asset",
    consumer_symbol: str = "upload_asset",
    include_import: bool = True,
    include_call: bool = True,
    config_key_in_config: bool = True,
    config_key_used: bool = True,
    include_integration: bool = True,
    integration_uses_settings: bool = True,
    integration_uses_sdk: bool = True,
    upload_route_path: str = "/upload",
    import_root: str = "vendor_sdk",
):
    import_line = ""
    if include_import:
        import_line = (
            f"from app.integrations.cloud_storage import {consumer_symbol}\n"
        )
    call_line = ""
    if include_call:
        call_line = f"    {consumer_symbol}(payload)\n"
    config_body = (
        "def get_settings():\n"
        "    return type('S', (), {'CLOUD_STORAGE_API_KEY': 'x'})()\n"
    )
    if config_key_in_config:
        config_body += "CLOUD_STORAGE_API_KEY = 'x'\n"
    endpoint_content = f"""
from fastapi import APIRouter
{import_line}
router = APIRouter()

@router.post("{upload_route_path}")
async def upload_file(payload: dict):
{call_line}    return {{"ok": True}}
"""
    files = [
        GeneratedFile(
            path="app/api/endpoints/upload.py",
            content=endpoint_content,
        ),
        GeneratedFile(
            path="app/core/config.py",
            content=config_body,
        ),
    ]
    if include_integration:
        settings_line = (
            "    settings = get_settings()\n"
            if integration_uses_settings
            else ""
        )
        if integration_uses_sdk:
            if config_key_used:
                sdk_line = (
                    f"    client = {import_root}.client_factory.create(\n"
                    "        api_key=settings.CLOUD_STORAGE_API_KEY,\n"
                    "    )\n"
                )
            else:
                sdk_line = (
                    f"    client = {import_root}.client_factory.create(\n"
                    "        timeout=10,\n"
                    "    )\n"
                )
        else:
            sdk_line = ""
        files.append(
            GeneratedFile(
                path="app/integrations/cloud_storage.py",
                content=f"""
from app.core.config import get_settings
import {import_root}.client_factory

def {provider_symbol}(payload):
{settings_line}{sdk_line}    return payload
""",
            )
        )
    return files


def _test_file(
    *,
    route: str = "/upload",
    method: str = "post",
    include_monkeypatch: bool = True,
    include_symbol_reference: bool = True,
    include_assert_called: bool = True,
    sdk_import_module: str | None = None,
    include_network_call: bool = False,
    network_module: str = "requests",
    include_test_client: bool = True,
    include_assert: bool = True,
    include_error_403: bool = False,
    health_env_token: str | None = None,
    symbol_name: str = "upload_asset",
    direct_sdk_builder: bool = False,
    include_httpx_async_client: bool = False,
    path: str | None = None,
):
    test_path = path or ("tests/test_upload.py" if route != "/health" else "tests/test_health.py")
    sdk_import = f"import {sdk_import_module}\n" if sdk_import_module else ""
    client_import = ""
    request_client_init = ""
    if include_test_client:
        if include_httpx_async_client:
            client_import = "from httpx import AsyncClient, ASGITransport\n"
            request_client_init = (
                "    transport = ASGITransport(app=app)\n"
                "    client = AsyncClient(transport=transport, base_url='http://testserver')\n"
            )
        else:
            client_import = "from fastapi.testclient import TestClient\n"
            request_client_init = "    client = TestClient(app)\n"
    monkeypatch_arg = "monkeypatch, " if include_monkeypatch else ""
    setup_block = ""
    if include_symbol_reference:
        if include_monkeypatch:
            setup_block += "    calls = []\n"
            setup_block += f"    def fake_{symbol_name}(payload):\n"
            setup_block += "        calls.append(payload)\n"
            setup_block += "        return {'id': '1'}\n"
            setup_block += f"    monkeypatch.setattr(upload_module, '{symbol_name}', fake_{symbol_name})\n"
        else:
            setup_block += f"    {symbol_name} = object()\n"
    if direct_sdk_builder and sdk_import_module:
        setup_block += f"    client_builder = {sdk_import_module}.build_client()\n"
        setup_block += "    assert client_builder is not None\n"
    request_block = ""
    if include_test_client:
        request_block += request_client_init
        request_block += f'    response = client.{method}("{route}", json={{"name": "x"}})\n'
    if include_network_call:
        if network_module == "requests":
            request_block += "    import requests\n"
            request_block += "    requests.get('https://example.test')\n"
        elif network_module == "urllib":
            request_block += "    import urllib.request\n"
            request_block += "    urllib.request.urlopen('https://example.test')\n"
    assert_block = ""
    if include_assert:
        assert_block += (
            "    assert response.status_code in (200, 201)\n"
            if include_test_client
            else "    assert True\n"
        )
    if include_assert_called:
        assert_block += "    assert calls\n"
    if include_error_403:
        assert_block += "    assert 403\n"
    if health_env_token:
        assert_block += f"    assert '{health_env_token}'\n"

    return GeneratedFile(
        path=test_path,
        content=f"""
{sdk_import}{client_import}from app.main import app
from app.api.endpoints import upload as upload_module

def test_endpoint({monkeypatch_arg}):
{setup_block}{request_block}{assert_block}
""",
    )


def test_project_validation_accepts_compatible_interface() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(),
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    assert "PROJECT_INTERNAL_SYMBOL_CONTRACT_MISSING" not in _codes(issues)


def test_project_validation_detects_symbol_contract_mismatch() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(provider_symbol="upload_file_asset"),
        file_contracts=_base_contracts(provider_symbol="upload_file_asset"),
        spec=_base_spec(),
    )
    codes = _codes(issues)
    assert "PROJECT_INTERNAL_SYMBOL_CONTRACT_MISSING" in codes
    assert "PROJECT_REQUIRED_ACTION_NOT_REACHABLE" in codes


def test_project_validation_detects_provider_code_symbol_mismatch() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(
            provider_symbol="upload_file_asset",
            consumer_symbol="upload_asset",
        ),
        file_contracts=_base_contracts(
            provider_symbol="upload_asset",
            consumer_symbol="upload_asset",
        ),
        spec=_base_spec(),
    )
    codes = _codes(issues)
    assert "PROJECT_INTERNAL_SYMBOL_CODE_MISSING" in codes
    assert "PROJECT_INTERNAL_SYMBOL_CONTRACT_MISSING" not in codes


def test_project_validation_detects_required_symbol_not_imported() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(include_import=False, include_call=False),
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    assert "PROJECT_INTERNAL_SYMBOL_NOT_IMPORTED" in _codes(issues)


def test_project_validation_detects_required_symbol_not_called() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(include_call=False),
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    assert "PROJECT_INTERNAL_SYMBOL_NOT_CALLED" in _codes(issues)


def test_project_validation_detects_missing_module() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(include_integration=False),
        file_contracts=_base_contracts(include_integration=False),
        spec=_base_spec(),
    )
    assert "PROJECT_INTERNAL_MODULE_MISSING" in _codes(issues)


def test_project_validation_detects_action_not_reachable() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(include_call=False),
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    assert "PROJECT_REQUIRED_ACTION_NOT_REACHABLE" in _codes(issues)


def test_project_validation_detects_config_declared_but_not_used() -> None:
    files = _base_files(config_key_used=False)
    files.append(
        GeneratedFile(path="README.md", content="Set CLOUD_STORAGE_API_KEY in env")
    )
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    assert "PROJECT_CONFIGURATION_CONSUMER_MISSING" in _codes(issues)


def test_project_validation_accepts_required_configuration_when_consumed() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(config_key_used=True),
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    assert "PROJECT_CONFIGURATION_CONSUMER_MISSING" not in _codes(issues)


def test_project_validation_detects_integration_not_invoked() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(include_import=False, include_call=False),
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    assert "PROJECT_INTEGRATION_NOT_REFERENCED_BY_CODE" in _codes(issues)


def test_project_validation_detects_health_health() -> None:
    spec = {"endpoints": [{"path": "/health", "methods": ["GET"], "actions": []}]}
    contracts = [
        {
            "path": "app/api/endpoints/health.py",
            "kind": "endpoint",
            "endpoint": {"path": "/health", "methods": ["GET"]},
        }
    ]
    files = [
        GeneratedFile(
            path="app/api/endpoints/health.py",
            content="""
from fastapi import APIRouter
router = APIRouter()
router.include_router(router, prefix="/health")

@router.get("/health")
async def health():
    return {"ok": True}
""",
        )
    ]
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
    )
    assert "PROJECT_ROUTE_DUPLICATED_PREFIX" in _codes(issues)


def test_project_validation_detects_upload_upload() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(upload_route_path="/upload"),
        file_contracts=_base_contracts(),
        spec=_base_spec("/upload"),
    )
    assert "PROJECT_ROUTE_MISSING" not in _codes(issues)


def test_project_validation_accepts_correct_routes() -> None:
    spec = {"endpoints": [{"path": "/health", "methods": ["GET"], "actions": []}]}
    contracts = [
        {
            "path": "app/api/endpoints/health.py",
            "kind": "endpoint",
            "endpoint": {"path": "/health", "methods": ["GET"]},
        }
    ]
    files = [
        GeneratedFile(
            path="app/api/endpoints/health.py",
            content="""
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"ok": True}
""",
        )
    ]
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
    )
    assert _codes(issues) == set()


def test_health_file_is_static_and_import_safe_without_external_configuration() -> None:
    spec = {"endpoints": [{"path": "/health", "methods": ["GET"], "actions": []}]}
    contracts = [
        {
            "path": "app/api/endpoints/health.py",
            "kind": "endpoint",
            "endpoint": {"path": "/health", "methods": ["GET"]},
        }
    ]
    files = [
        GeneratedFile(
            path="app/api/endpoints/health.py",
            content="""
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"ok": True}
""",
        )
    ]
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
    )
    assert "PROJECT_ROUTE_MISSING" not in _codes(issues)


def test_project_validation_detects_declared_error_not_handled() -> None:
    contracts = _base_contracts()
    contracts[0]["endpoint"]["errors"] = [{"status_code": 403, "required": True}]
    issues = validate_generated_project(
        generated_files=_base_files(),
        file_contracts=contracts,
        spec=_base_spec(include_error=True),
    )
    assert "PROJECT_DECLARED_ERROR_UNHANDLED" in _codes(issues)


def test_external_project_skeleton_can_be_valid() -> None:
    issues = validate_generated_project(
        generated_files=_base_files(),
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    assert "PROJECT_REQUIRED_ACTION_IMPLEMENTATION_MISSING" not in _codes(issues)


def test_build_endpoint_test_contracts_is_deterministic() -> None:
    contracts = build_endpoint_test_contracts(
        spec=_base_spec(),
        file_contracts=_base_contracts(),
    )
    assert len(contracts) == 1
    contract = contracts[0]
    assert contract.method == "POST"
    assert contract.path == "/upload"
    assert contract.endpoint_file == "app/api/endpoints/upload.py"
    assert contract.external_calls_forbidden is True


def test_validation_accepts_hermetic_mocked_test_for_external_integration() -> None:
    files = _base_files()
    files.append(_test_file())
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(include_test_contract=True),
        spec=_base_spec(),
    )
    codes = _codes(issues)
    assert "TEST_EXTERNAL_CALL_NOT_MOCKED" not in codes
    assert "TEST_ENDPOINT_ROUTE_NOT_COVERED" not in codes


def test_validation_rejects_test_without_call_assertion() -> None:
    files = _base_files()
    files.append(_test_file(include_assert_called=False))
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(include_test_contract=True),
        spec=_base_spec(),
    )
    assert "TEST_REQUIRED_CALL_NOT_ASSERTED" in _codes(issues)


def test_validation_rejects_test_with_real_network_call() -> None:
    files = _base_files()
    files.append(_test_file(include_network_call=True))
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(include_test_contract=True),
        spec=_base_spec(),
    )
    assert "TEST_EXTERNAL_CALL_NOT_MOCKED" in _codes(issues)


def test_validation_rejects_declared_error_without_test_coverage() -> None:
    files = _base_files()
    files.append(_test_file())
    contracts = _base_contracts(include_test_contract=True)
    contracts[0]["endpoint"]["errors"] = [{"status_code": 403, "required": True}]
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=_base_spec(include_error=True),
    )
    assert "TEST_DECLARED_ERROR_NOT_COVERED" in _codes(issues)


def test_validation_accepts_declared_error_when_test_covers_it() -> None:
    files = _base_files()
    files.append(_test_file(include_error_403=True))
    contracts = _base_contracts(include_test_contract=True)
    contracts[0]["endpoint"]["errors"] = [{"status_code": 403, "required": True}]
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=_base_spec(include_error=True),
    )
    assert "TEST_DECLARED_ERROR_NOT_COVERED" not in _codes(issues)


def test_health_tests_cannot_require_external_configuration() -> None:
    spec = {"endpoints": [{"path": "/health", "methods": ["GET"], "actions": []}]}
    contracts = [
        {
            "path": "app/api/endpoints/health.py",
            "kind": "endpoint",
            "endpoint": {"path": "/health", "methods": ["GET"]},
        },
        {
            "path": "app/integrations/cloud_storage.py",
            "kind": "integration",
            "configuration": [{"key": "DATABASE_URL"}],
        },
        {
            "path": "tests/test_health.py",
            "kind": "test",
        },
    ]
    files = [
        GeneratedFile(
            path="app/api/endpoints/health.py",
            content="""
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"ok": True}
""",
        ),
        _test_file(
            route="/health",
            method="get",
            health_env_token="DATABASE_URL",
        ),
    ]
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
    )
    assert "TEST_HEALTH_REQUIRES_EXTERNAL_CONFIG" in _codes(issues)


def test_classification_returns_pending_runtime_tests_for_local_projects() -> None:
    files = [
        GeneratedFile(
            path="app/api/endpoints/health.py",
            content="""
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"ok": True}
""",
        ),
        GeneratedFile(
            path="tests/test_health.py",
            content="""
from fastapi.testclient import TestClient
from app.main import app

def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
""",
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
    spec = {"endpoints": [{"path": "/health", "methods": ["GET"], "actions": []}]}
    result = classify_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
        static_validation_passed=True,
        runtime_tests_passed=None,
    )
    assert result["status"] == "pending_runtime_tests"
    assert result["external_connectivity_verified"] is False
    assert result["runtime_tests_passed"] is None


def test_classification_returns_valid_local_after_runtime_tests_pass() -> None:
    files = [
        GeneratedFile(
            path="app/api/endpoints/health.py",
            content="""
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"ok": True}
""",
        ),
        GeneratedFile(
            path="tests/test_health.py",
            content="""
from fastapi.testclient import TestClient
from app.main import app

def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
""",
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
    spec = {"endpoints": [{"path": "/health", "methods": ["GET"], "actions": []}]}
    result = classify_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
        static_validation_passed=True,
        runtime_tests_passed=True,
    )
    assert result["status"] == "valid_local"
    assert result["external_connectivity_verified"] is False
    assert result["runtime_tests_passed"] is True


def test_classification_returns_pending_runtime_tests_for_external_projects() -> None:
    files = _base_files()
    files.append(_test_file())
    result = classify_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(include_test_contract=True),
        spec=_base_spec(),
        static_validation_passed=True,
        runtime_tests_passed=None,
    )
    assert result["status"] == "pending_runtime_tests"
    assert result["external_connectivity_verified"] is False
    assert result["runtime_tests_passed"] is None


def test_classification_returns_valid_integration_skeleton_for_external_projects() -> None:
    files = _base_files()
    files.append(_test_file())
    result = classify_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(include_test_contract=True),
        spec=_base_spec(),
        static_validation_passed=True,
        runtime_tests_passed=True,
    )
    assert result["status"] == "valid_integration_skeleton"
    assert result["external_connectivity_verified"] is False
    assert result["runtime_tests_passed"] is True


def test_classification_returns_invalid_when_runtime_tests_fail() -> None:
    files = _base_files()
    files.append(_test_file())
    result = classify_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(include_test_contract=True),
        spec=_base_spec(),
        static_validation_passed=True,
        runtime_tests_passed=False,
    )
    assert result["status"] == "invalid"
    assert result["runtime_tests_passed"] is False


def test_classification_returns_invalid_when_static_validation_fails() -> None:
    files = _base_files()
    files.append(_test_file())
    result = classify_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(include_test_contract=True),
        spec=_base_spec(),
        static_validation_passed=False,
        runtime_tests_passed=True,
    )
    assert result["status"] == "invalid"
    assert result["runtime_tests_passed"] is True


def test_google_declared_dynamically() -> None:
    contracts = _base_contracts(
        include_test_contract=True,
        import_roots=["googleapiclient"],
    )
    files = _base_files(import_root="googleapiclient")
    files.append(_test_file(sdk_import_module="googleapiclient.discovery"))
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=_base_spec(),
    )
    assert "TEST_EXTERNAL_CALL_NOT_MOCKED" in _codes(issues)


def test_sql_declared_dynamically() -> None:
    contracts = _base_contracts(
        include_test_contract=True,
        import_roots=["psycopg"],
    )
    files = _base_files(import_root="psycopg")
    files.append(_test_file(sdk_import_module="psycopg"))
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=_base_spec(),
    )
    assert "TEST_EXTERNAL_CALL_NOT_MOCKED" in _codes(issues)


def test_unknown_provider_declared_dynamically() -> None:
    contracts = _base_contracts(
        include_test_contract=True,
        import_roots=["fictional_cloud"],
        integration_ref="fictional_cloud",
    )
    files = _base_files(import_root="fictional_cloud")
    files.append(_test_file(sdk_import_module="fictional_cloud.client"))
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=_base_spec(integration_ref="fictional_cloud"),
    )
    assert "TEST_EXTERNAL_CALL_NOT_MOCKED" in _codes(issues)


def test_build_function_is_not_considered_network() -> None:
    files = _base_files()
    files.append(_test_file())
    files.append(
        GeneratedFile(
            path="tests/test_builder.py",
            content="""
def build():
    return object()

def test_local_builder():
    result = build()
    assert result is not None
""",
        )
    )
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(include_test_contract=True),
        spec=_base_spec(),
    )
    assert "TEST_EXTERNAL_CALL_NOT_MOCKED" not in _codes(issues)


def test_sdk_constructor_is_not_considered_real_network_call() -> None:
    contracts = _base_contracts(
        include_test_contract=True,
        import_roots=["fictional_cloud"],
        integration_ref="fictional_cloud",
    )
    files = _base_files(import_root="fictional_cloud")
    files.append(
        _test_file(
            sdk_import_module="fictional_cloud",
            direct_sdk_builder=True,
        )
    )
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=_base_spec(integration_ref="fictional_cloud"),
    )
    assert "TEST_EXTERNAL_CALL_NOT_MOCKED" in _codes(issues)


def test_direct_network_call_is_detected() -> None:
    files = _base_files()
    files.append(_test_file(include_network_call=True, network_module="requests"))
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(include_test_contract=True),
        spec=_base_spec(),
    )
    assert "TEST_EXTERNAL_CALL_NOT_MOCKED" in _codes(issues)


def test_health_isolated_from_upload_external_configuration() -> None:
    spec = {
        "endpoints": [
            {"path": "/health", "methods": ["GET"], "actions": []},
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
            },
        ]
    }
    contracts = [
        {
            "path": "app/api/endpoints/health.py",
            "kind": "endpoint",
            "endpoint": {"path": "/health", "methods": ["GET"]},
        },
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
            "configuration": [{"key": "DRIVE_FOLDER_ID"}],
            "integration_refs": ["cloud_storage"],
        },
        {"path": "tests/test_mix.py", "kind": "test"},
    ]
    files = [
        GeneratedFile(
            path="app/api/endpoints/health.py",
            content="""
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"ok": True}
""",
        ),
        GeneratedFile(
            path="app/api/endpoints/upload.py",
            content="""
from fastapi import APIRouter
from app.integrations.cloud_storage import upload_asset
router = APIRouter()

@router.post("/upload")
async def upload(payload: dict):
    upload_asset(payload)
    return {"ok": True}
""",
        ),
        GeneratedFile(
            path="tests/test_mix.py",
            content="""
from fastapi.testclient import TestClient
from app.main import app

def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200

def test_upload(monkeypatch):
    monkeypatch.setenv("DRIVE_FOLDER_ID", "folder")
    client = TestClient(app)
    response = client.post("/upload", json={"filename": "x"})
    assert response.status_code == 200
""",
        ),
    ]
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
    )
    assert "TEST_HEALTH_REQUIRES_EXTERNAL_CONFIG" not in _codes(issues)


def test_health_uses_external_configuration_declared_in_contracts() -> None:
    spec = {"endpoints": [{"path": "/health", "methods": ["GET"], "actions": []}]}
    contracts = [
        {
            "path": "app/api/endpoints/health.py",
            "kind": "endpoint",
            "endpoint": {"path": "/health", "methods": ["GET"]},
        },
        {
            "path": "app/repositories/db.py",
            "kind": "repository",
            "configuration": [{"key": "DATABASE_URL"}],
        },
        {"path": "tests/test_health.py", "kind": "test"},
    ]
    files = [
        GeneratedFile(
            path="app/api/endpoints/health.py",
            content="""
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"ok": True}
""",
        ),
        GeneratedFile(
            path="tests/test_health.py",
            content="""
from fastapi.testclient import TestClient
from app.main import app

def test_health(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db")
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
""",
        ),
        GeneratedFile(
            path="README.md",
            content="DATABASE_URL appears in docs but must not affect health analysis.",
        ),
    ]
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
    )
    assert "TEST_HEALTH_REQUIRES_EXTERNAL_CONFIG" in _codes(issues)


def test_readme_does_not_influence_health_analysis() -> None:
    spec = {"endpoints": [{"path": "/health", "methods": ["GET"], "actions": []}]}
    contracts = [
        {
            "path": "app/api/endpoints/health.py",
            "kind": "endpoint",
            "endpoint": {"path": "/health", "methods": ["GET"]},
        },
        {
            "path": "app/repositories/db.py",
            "kind": "repository",
            "configuration": [{"key": "DATABASE_URL"}],
        },
        {"path": "tests/test_health.py", "kind": "test"},
    ]
    files = [
        GeneratedFile(
            path="app/api/endpoints/health.py",
            content="""
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health():
    return {"ok": True}
""",
        ),
        GeneratedFile(
            path="tests/test_health.py",
            content="""
from fastapi.testclient import TestClient
from app.main import app

def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
""",
        ),
        GeneratedFile(
            path="README.md",
            content="DATABASE_URL appears only in documentation.",
        ),
    ]
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=contracts,
        spec=spec,
    )
    assert "TEST_HEALTH_REQUIRES_EXTERNAL_CONFIG" not in _codes(issues)


def test_integration_ref_is_located_structurally() -> None:
    contracts = _base_contracts(
        integration_ref="fictional_cloud",
        import_roots=["fictional_cloud"],
    )
    issues = validate_generated_project(
        generated_files=_base_files(import_root="fictional_cloud"),
        file_contracts=contracts,
        spec=_base_spec(integration_ref="fictional_cloud"),
    )
    assert "PROJECT_INTEGRATION_MODULE_MISSING" not in _codes(issues)


def test_action_and_integration_ids_are_not_mixed() -> None:
    contracts = _base_contracts(
        integration_ref="fictional_cloud",
        import_roots=["fictional_cloud"],
    )
    spec = _base_spec(
        integration_ref="fictional_cloud",
        action_ref="upload_asset",
    )
    issues = validate_generated_project(
        generated_files=_base_files(import_root="fictional_cloud"),
        file_contracts=contracts,
        spec=spec,
    )
    assert "PROJECT_INTEGRATION_MODULE_MISSING" not in _codes(issues)


def test_httpx_async_client_for_asgi_is_allowed() -> None:
    files = _base_files()
    files.append(_test_file(include_httpx_async_client=True))
    issues = validate_generated_project(
        generated_files=files,
        file_contracts=_base_contracts(include_test_contract=True),
        spec=_base_spec(),
    )
    assert "TEST_EXTERNAL_CALL_NOT_MOCKED" not in _codes(issues)


def test_project_validation_has_no_provider_hardcoding() -> None:
    source = Path(project_validation.__file__).read_text(encoding="utf-8")
    forbidden = {
        "googleapiclient",
        "boto3",
        "botocore",
        "GOOGLE_",
        "DRIVE_",
        "SERVICE_ACCOUNT",
    }
    assert not any(token in source for token in forbidden)
