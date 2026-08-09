from __future__ import annotations

import re
import json
from pathlib import Path

from poc_it.materializacion.codegen.models import CodegenIssue, GeneratedFile
from poc_it.materializacion.codegen.project_validation import validate_generated_project
from poc_it.materializacion.codegen.semantic_repair import (
    ALL_REPAIRABLE_CODES,
    CONTRACT_STRUCTURE_BLOCKERS,
    IMPLEMENTATION_REPAIRABLE_CODES,
    IssueRepairClass,
    classify_issue_for_repair,
    repair_generated_project,
    repair_paths_for_issues,
    summarize_generated_interfaces,
    _build_structured_issues,
    _is_repairable_issue,
)


def _base_spec() -> dict:
    return {
        "files": [
            "app/api/endpoints/upload.py",
            "app/integrations/google_drive.py",
            "app/api/router.py",
            "requirements.txt",
        ],
        "implementation_files": [
            {"kind": "endpoint", "path": "app/api/endpoints/upload.py"},
            {
                "kind": "integration",
                "path": "app/integrations/google_drive.py",
                "integration_ref": "google_drive",
            },
            {"kind": "router", "path": "app/api/router.py"},
            {"kind": "requirements", "path": "requirements.txt"},
        ],
        "endpoints": [
            {
                "file": "app/api/endpoints/upload.py",
                "path": "/upload",
                "method": "POST",
                "actions": [
                    {
                        "id": "upload_to_drive",
                        "kind": "external_call",
                        "required": True,
                        "integration_ref": "google_drive",
                    }
                ],
                "errors": [{"status_code": 502}],
                "integration_refs": ["google_drive"],
            }
        ],
        "dependencies": ["fastapi", "google-api-python-client"],
    }


def _base_contracts() -> list[dict]:
    return [
        {
            "path": "app/api/endpoints/upload.py",
            "kind": "endpoint",
            "required_symbols": ["router", "upload_handler"],
            "provided_interfaces": [],
            "required_internal_calls": [
                {
                    "module": "app.integrations.google_drive",
                    "symbol": "upload_to_drive",
                    "action_ref": "upload_to_drive",
                    "required": True,
                }
            ],
            "allowed_imports": [{"module": "app.integrations.google_drive"}],
            "integration_refs": ["google_drive"],
            "endpoint": {
                "path": "/upload",
                "methods": ["POST"],
                "errors": [{"status_code": 502}],
                "request": {"content_type": "application/json"},
            },
            "actions": [
                {
                    "id": "upload_to_drive",
                    "kind": "external_call",
                    "required": True,
                }
            ],
            "must_implement": [],
            "must_not": [],
            "implementation_plan": [],
            "errors": [],
            "configuration": [],
        },
        {
            "path": "app/integrations/google_drive.py",
            "kind": "integration",
            "required_symbols": ["upload_to_drive"],
            "provided_interfaces": [
                {
                    "symbol": "upload_to_drive",
                    "kind": "function",
                    "action_ref": "upload_to_drive",
                    "parameters": [{"name": "payload", "required": True}],
                }
            ],
            "required_internal_calls": [],
            "allowed_imports": [
                {
                    "import_root": "googleapiclient",
                    "package": "google-api-python-client",
                }
            ],
            "integration_refs": ["google_drive"],
            "implementation_levels": ["integration_skeleton"],
            "actions": [
                {
                    "id": "upload_to_drive",
                    "kind": "external_call",
                    "required": True,
                }
            ],
            "must_implement": [],
            "must_not": [],
            "implementation_plan": [],
            "errors": [],
            "configuration": [{"name": "GOOGLE_DRIVE_FOLDER_ID"}],
        },
        {
            "path": "app/api/router.py",
            "kind": "router",
            "required_symbols": ["router"],
            "provided_interfaces": [],
            "required_internal_calls": [],
            "allowed_imports": [],
            "must_implement": [],
            "must_not": [],
            "implementation_plan": [],
            "actions": [],
            "errors": [],
            "configuration": [],
        },
        {
            "path": "requirements.txt",
            "kind": "requirements",
            "required_symbols": [],
            "provided_interfaces": [],
            "required_internal_calls": [],
            "allowed_imports": [
                {
                    "import_root": "googleapiclient",
                    "package": "google-api-python-client",
                }
            ],
            "dependencies": ["fastapi", "google-api-python-client"],
            "must_implement": [],
            "must_not": [],
            "implementation_plan": [],
            "actions": [],
            "errors": [],
            "configuration": [],
        },
    ]


def _valid_generated_files() -> list[GeneratedFile]:
    return [
        GeneratedFile(
            path="app/api/endpoints/upload.py",
            content=(
                "from fastapi import APIRouter, HTTPException\n"
                "from app.integrations.google_drive import upload_to_drive\n\n"
                "router = APIRouter()\n\n"
                "@router.post('/upload')\n"
                "async def upload_handler():\n"
                "    result = upload_to_drive(payload={})\n"
                "    if not result:\n"
                "        raise HTTPException(status_code=502, detail='upload failed')\n"
                "    return result\n"
            ),
        ),
        GeneratedFile(
            path="app/integrations/google_drive.py",
            content=(
                "from app.core.config import get_settings\n"
                "from googleapiclient.discovery import build\n\n"
                "def upload_to_drive(payload):\n"
                "    settings = get_settings()\n"
                "    service = build('drive', 'v3', cache_discovery=False)\n"
                "    request = service.files().create(\n"
                "        body={'name': payload.get('filename', 'test.txt'), 'parents': [settings.GOOGLE_DRIVE_FOLDER_ID]},\n"
                "        media_body=payload.get('content'),\n"
                "        fields='id,name',\n"
                "    )\n"
                "    return request.execute()\n"
            ),
        ),
        GeneratedFile(
            path="app/api/router.py",
            content=(
                "from fastapi import APIRouter\n"
                "from app.api.endpoints.upload import router as upload_router\n\n"
                "router = APIRouter()\n"
                "router.include_router(upload_router)\n"
            ),
        ),
        GeneratedFile(path="requirements.txt", content="fastapi\ngoogle-api-python-client\n"),
    ]


def _requested_path_from_prompt(prompt: str) -> str:
    patterns = [
        r'"path"\s*:\s*"([^"]+)"',
        r'ARCHIVO OBJETIVO\s*[:\n]+\s*([^\s]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.MULTILINE)
        if match is not None:
            return match.group(1).strip()
    raise AssertionError("No se encontró el path objetivo en el prompt de reparación")


def _fixed_endpoint_response() -> str:
    return (
        '{"path":"app/api/endpoints/upload.py","content":"'
        'from fastapi import APIRouter, HTTPException\\n'
        'from app.integrations.google_drive import upload_to_drive\\n\\n'
        'router = APIRouter()\\n\\n'
        '@router.post(\\"/upload\\")\\n'
        'async def upload_handler():\\n'
        '    result = upload_to_drive(payload={})\\n'
        '    if not result:\\n'
        '        raise HTTPException(status_code=502, detail=\\"upload failed\\")\\n'
        '    return result\\n"}'
    )


def _fixed_integration_response() -> str:
    return (
        '{"path":"app/integrations/google_drive.py","content":"'
        'from app.core.config import get_settings\\n'
        'from googleapiclient.discovery import build\\n\\n'
        'def upload_to_drive(payload):\\n'
        '    settings = get_settings()\\n'
        '    service = build(\\"drive\\", \\"v3\\", cache_discovery=False)\\n'
        '    request = service.files().create(\\n'
        '        body={\\"name\\": payload.get(\\"filename\\", \\"test.txt\\"), \\"parents\\": [settings.GOOGLE_DRIVE_FOLDER_ID]},\\n'
        '        media_body=payload.get(\\"content\\"),\\n'
        '        fields=\\"id,name\\",\\n'
        '    )\\n'
        '    return request.execute()\\n"}'
    )


def _fixed_router_response() -> str:
    return (
        '{"path":"app/api/router.py","content":"'
        'from fastapi import APIRouter\\n'
        'from app.api.endpoints.upload import router as upload_router\\n\\n'
        'router = APIRouter()\\n'
        'router.include_router(upload_router)\\n"}'
    )


def _fixed_requirements_response() -> str:
    return '{"path":"requirements.txt","content":"fastapi\\ngoogle-api-python-client\\n"}'


class FakeRepairLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, **kwargs) -> str:
        prompt = kwargs["prompt"]
        self.calls.append(prompt)
        requested_path = _requested_path_from_prompt(prompt)
        if requested_path == "app/api/endpoints/upload.py":
            return _fixed_endpoint_response()
        if requested_path == "app/integrations/google_drive.py":
            return _fixed_integration_response()
        if requested_path == "app/api/router.py":
            return _fixed_router_response()
        if requested_path == "requirements.txt":
            return _fixed_requirements_response()
        raise AssertionError(f"Path inesperado: {requested_path}")


def test_summarize_generated_interfaces_uses_contract_exports() -> None:
    summaries = summarize_generated_interfaces(
        generated_files=[
            GeneratedFile(
                path="app/integrations/google_drive.py",
                content="def upload_to_drive(payload):\n    return payload\n",
            )
        ],
        file_contracts=_base_contracts(),
    )
    assert summaries[0].module == "app.integrations.google_drive"
    assert "upload_to_drive" in summaries[0].exported_symbols


def test_repair_paths_only_repairs_implicated_paths() -> None:
    issues = [
        CodegenIssue(
            code="PROJECT_INTERNAL_SYMBOL_NOT_CALLED",
            path="app/api/endpoints/upload.py",
            message="missing call",
            symbol="upload_to_drive",
            action_ref="upload_to_drive",
        ),
        CodegenIssue(
            code="PROJECT_CONFIGURATION_CONSUMER_MISSING",
            path="app/integrations/google_drive.py",
            message="missing config",
        ),
    ]
    assert repair_paths_for_issues(issues, file_contracts=_base_contracts()) == [
        "app/api/endpoints/upload.py",
        "app/integrations/google_drive.py",
    ]


def test_contractual_issue_does_not_call_llm() -> None:
    llm = FakeRepairLLM()
    contracts = _base_contracts()
    endpoint_contract = next(
        contract
        for contract in contracts
        if contract["path"] == "app/api/endpoints/upload.py"
    )
    endpoint_contract["required_internal_calls"] = [
        {
            "module": "app.integrations.missing_provider",
            "symbol": "upload_to_drive",
            "action_ref": "upload_to_drive",
            "required": True,
        }
    ]

    files, issues = repair_generated_project(
        generated_files=_valid_generated_files(),
        file_contracts=contracts,
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )
    assert files
    assert issues
    assert any(
        issue.code in {"PROJECT_INTERNAL_MODULE_MISSING", "PROJECT_INTERNAL_SYMBOL_CONTRACT_MISSING"}
        for issue in issues
    )
    assert llm.calls == []


def test_endpoint_no_llama_integracion_y_se_repara() -> None:
    llm = FakeRepairLLM()
    generated_files = _valid_generated_files()
    generated_files[0] = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content=(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/upload')\n"
            "async def upload_handler():\n"
            "    return {'ok': True}\n"
        ),
    )
    repaired_files, issues = repair_generated_project(
        generated_files=generated_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=2,
    )
    endpoint = next(item for item in repaired_files if item.path.endswith("upload.py"))
    assert "upload_to_drive" in endpoint.content
    assert not issues
    assert any("VIOLACIONES ESTRUCTURADAS" in prompt for prompt in llm.calls)
    assert any("INTERFACES REALES DISPONIBLES" in prompt for prompt in llm.calls)


def test_proveedor_no_define_simbolo_y_se_repara_sin_fake() -> None:
    llm = FakeRepairLLM()
    generated_files = _valid_generated_files()
    generated_files[1] = GeneratedFile(
        path="app/integrations/google_drive.py",
        content="from app.core.config import get_settings\n\nvalue = get_settings()\n",
    )
    repaired_files, issues = repair_generated_project(
        generated_files=generated_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=2,
    )
    integration = next(item for item in repaired_files if "google_drive.py" in item.path)
    assert "def upload_to_drive" in integration.content
    assert "NotImplementedError" not in integration.content
    assert "pass" not in integration.content
    assert not issues


def test_repair_no_elimina_otra_accion() -> None:
    llm = FakeRepairLLM()
    contracts = _base_contracts()
    contracts[0]["actions"].append(
        {"id": "audit_upload", "kind": "internal_processing", "required": True}
    )
    generated_files = _valid_generated_files()
    generated_files[0] = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content=(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/upload')\n"
            "async def upload_handler():\n"
            "    audit_upload = 'keep'\n"
            "    return {'ok': True, 'audit_upload': audit_upload}\n"
        ),
    )
    repaired_files, _issues = repair_generated_project(
        generated_files=generated_files,
        file_contracts=contracts,
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )
    endpoint = next(item for item in repaired_files if item.path.endswith("upload.py"))
    assert "audit_upload" in endpoint.content


def test_config_no_usada_se_repara() -> None:
    llm = FakeRepairLLM()
    generated_files = _valid_generated_files()
    generated_files[1] = GeneratedFile(
        path="app/integrations/google_drive.py",
        content=(
            "from googleapiclient.discovery import build\n\n"
            "def upload_to_drive(payload):\n"
            "    service = build('drive', 'v3', cache_discovery=False)\n"
            "    return {'ok': True, 'service': bool(service), 'payload': payload}\n"
        ),
    )
    repaired_files, issues = repair_generated_project(
        generated_files=generated_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )
    integration = next(item for item in repaired_files if item.path.endswith("google_drive.py"))
    assert "get_settings" in integration.content
    assert not issues


def test_limite_de_rondas() -> None:
    class BadLLM(FakeRepairLLM):
        def __call__(self, **kwargs) -> str:
            prompt = kwargs["prompt"]
            self.calls.append(prompt)
            requested_path = _requested_path_from_prompt(prompt)
            if requested_path == "app/api/endpoints/upload.py":
                return (
                    '{"path":"app/api/endpoints/upload.py","content":"'
                    'from fastapi import APIRouter\\n\\n'
                    'router = APIRouter()\\n\\n'
                    '@router.post(\\"/upload\\")\\n'
                    'async def upload_handler():\\n'
                    '    return {\\"ok\\": True}\\n"}'
                )
            return super().__call__(**kwargs)

    llm = BadLLM()
    generated_files = _valid_generated_files()
    generated_files[0] = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content=(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/upload')\n"
            "async def upload_handler():\n"
            "    return {'ok': True}\n"
        ),
    )
    _repaired_files, issues = repair_generated_project(
        generated_files=generated_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )
    assert issues
    assert len(llm.calls) == 1


def test_route_prefix_duplicado_se_dirige_a_router_o_endpoint() -> None:
    issues = [
        CodegenIssue(
            code="PROJECT_ROUTE_DUPLICATED_PREFIX",
            path="app/api/router.py",
            message="duplicated prefix",
        )
    ]
    assert repair_paths_for_issues(issues, file_contracts=_base_contracts()) == [
        "app/api/router.py"
    ]


def test_project_issues_preserve_symbol_and_action_ref() -> None:
    generated_files = _valid_generated_files()
    generated_files[0] = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content=(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/upload')\n"
            "async def upload_handler():\n"
            "    return {'ok': True}\n"
        ),
    )
    issues = validate_generated_project(
        generated_files=generated_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    target = next(issue for issue in issues if issue.code == "PROJECT_INTERNAL_SYMBOL_NOT_CALLED")
    assert target.symbol == "upload_to_drive"
    assert target.action_ref == "upload_to_drive"


def test_not_reachable_action_targets_consumer() -> None:
    generated_files = _valid_generated_files()
    generated_files[0] = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content=(
            "from fastapi import APIRouter, HTTPException\n"
            "from app.integrations.google_drive import upload_to_drive\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/upload')\n"
            "async def upload_handler():\n"
            "    if False:\n"
            "        raise HTTPException(status_code=502, detail='upload failed')\n"
            "    return {'ok': True}\n"
        ),
    )
    issues = validate_generated_project(
        generated_files=generated_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    target = next(issue for issue in issues if issue.code == "PROJECT_REQUIRED_ACTION_NOT_REACHABLE")
    assert target.path == "app/api/endpoints/upload.py"


def test_repair_path_selection_does_not_require_current_content() -> None:
    issues = [
        CodegenIssue(
            code="PROJECT_INTERNAL_SYMBOL_NOT_CALLED",
            path="app/api/endpoints/upload.py",
            message="missing call",
            symbol="upload_to_drive",
            action_ref="upload_to_drive",
        )
    ]
    assert repair_paths_for_issues(issues, file_contracts=_base_contracts()) == [
        "app/api/endpoints/upload.py"
    ]


def test_repair_path_blocked_by_non_repairable_contract_issue() -> None:
    issues = [
        CodegenIssue(
            code="PROJECT_INTERNAL_MODULE_MISSING",
            path="app/api/endpoints/upload.py",
            message="missing module",
        ),
        CodegenIssue(
            code="FILE_CONTRACT_SYMBOL_MISSING",
            path="app/api/endpoints/upload.py",
            message="missing symbol",
        ),
    ]
    assert "app/api/endpoints/upload.py" not in repair_paths_for_issues(
        issues,
        file_contracts=_base_contracts(),
    )


def test_repair_path_blocker_is_scoped_per_path() -> None:
    issues = [
        CodegenIssue(
            code="PROJECT_INTERNAL_MODULE_MISSING",
            path="app/api/endpoints/upload.py",
            message="missing module",
        ),
        CodegenIssue(
            code="ROUTE_PATH_MISMATCH",
            path="app/api/router.py",
            message="route mismatch",
        ),
    ]
    repair_paths = repair_paths_for_issues(issues, file_contracts=_base_contracts())
    assert "app/api/endpoints/upload.py" not in repair_paths
    assert "app/api/router.py" in repair_paths


def test_symbol_missing_with_existing_module_remains_repairable() -> None:
    issues = [
        CodegenIssue(
            code="FILE_CONTRACT_SYMBOL_MISSING",
            path="app/api/endpoints/upload.py",
            message="missing symbol",
            symbol="upload_to_drive",
            action_ref="upload_to_drive",
        )
    ]
    assert repair_paths_for_issues(issues, file_contracts=_base_contracts()) == [
        "app/api/endpoints/upload.py"
    ]


def test_round_debug_file_is_persisted() -> None:
    llm = FakeRepairLLM()
    generated_files = _valid_generated_files()
    generated_files[0] = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content=(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/upload')\n"
            "async def upload_handler():\n"
            "    return {'ok': True}\n"
        ),
    )
    repair_generated_project(
        generated_files=generated_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )
    assert Path("output/_debug/codegen_repair_round_1.json").exists()


def test_configuration_field_missing_targets_consumer_path() -> None:
    issues = [
        CodegenIssue(
            code="CONFIGURATION_FIELD_MISSING",
            path="app/integrations/google_drive.py",
            message="invalid config field",
            details={"field": "UNKNOWN_FIELD", "allowed_fields": ["RESOURCE_ID"]},
        )
    ]

    assert repair_paths_for_issues(issues, file_contracts=_base_contracts()) == [
        "app/integrations/google_drive.py"
    ]


def test_fix_prompt_contains_structured_configuration_field_context() -> None:
    class InspectingLLM(FakeRepairLLM):
        pass

    llm = InspectingLLM()
    generated_files = _valid_generated_files()
    generated_files[1] = GeneratedFile(
        path="app/integrations/google_drive.py",
        content=(
            "from app.core.config import get_settings\n\n"
            "def upload_to_drive(payload):\n"
            "    cfg = get_settings()\n"
            "    return cfg.UNKNOWN_FIELD\n"
        ),
    )
    contracts = _base_contracts()
    contracts[1]["configuration_access"] = {
        "provider_module": "app.core.config",
        "provider_symbol": "get_settings",
        "allowed_fields": ["RESOURCE_ID"],
        "access_mode": "lazy",
    }
    contracts[1]["authentication_constraints"] = {
        "credential_source": "runtime",
        "allows_embedded_secret": False,
        "allows_static_credential_file": False,
    }
    contracts[1]["authentication_runtime_contract"] = {
        "discovery": "ambient",
        "requires_configuration_field": False,
        "requires_static_credential_file": False,
        "requires_embedded_secret": False,
    }

    repair_generated_project(
        generated_files=generated_files,
        file_contracts=contracts,
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )

    prompt = "\n".join(llm.calls)
    assert "UNKNOWN_FIELD" in prompt
    assert "RESOURCE_ID" in prompt
    assert "AUTHENTICATION RUNTIME CONTRACT" in prompt
    assert "requires_configuration_field=false" in prompt
    assert "requires_static_credential_file=false" in prompt


def test_rejected_candidate_is_persisted_with_same_issue_metadata() -> None:
    class BadConfigFieldLLM(FakeRepairLLM):
        def __call__(self, **kwargs) -> str:
            prompt = kwargs["prompt"]
            self.calls.append(prompt)
            requested_path = _requested_path_from_prompt(prompt)
            if requested_path == "app/integrations/google_drive.py":
                return (
                    '{"path":"app/integrations/google_drive.py","content":"'
                    'from app.core.config import get_settings\\n\\n'
                    'def upload_to_drive(payload):\\n'
                    '    cfg = get_settings()\\n'
                    '    return cfg.ANOTHER_UNKNOWN_FIELD\\n"}'
                )
            return super().__call__(**kwargs)

    llm = BadConfigFieldLLM()
    generated_files = _valid_generated_files()
    generated_files[1] = GeneratedFile(
        path="app/integrations/google_drive.py",
        content=(
            "from app.core.config import get_settings\n\n"
            "def upload_to_drive(payload):\n"
            "    cfg = get_settings()\n"
            "    return cfg.UNKNOWN_FIELD\n"
        ),
    )
    contracts = _base_contracts()
    contracts[1]["configuration_access"] = {
        "provider_module": "app.core.config",
        "provider_symbol": "get_settings",
        "allowed_fields": ["RESOURCE_ID"],
        "access_mode": "lazy",
    }
    contracts[1]["authentication_constraints"] = {
        "credential_source": "runtime",
        "allows_embedded_secret": False,
        "allows_static_credential_file": False,
    }
    contracts[1]["authentication_runtime_contract"] = {
        "discovery": "ambient",
        "requires_configuration_field": False,
        "requires_static_credential_file": False,
        "requires_embedded_secret": False,
    }

    _files, issues = repair_generated_project(
        generated_files=generated_files,
        file_contracts=contracts,
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )

    assert any(issue.code == "CONFIGURATION_FIELD_MISSING" for issue in issues)
    candidate_path = Path(
        "output/_debug/codegen_candidate_app_integrations_google_drive_py_attempt_1.py"
    )
    metadata_path = Path(
        "output/_debug/codegen_candidate_app_integrations_google_drive_py_attempt_1.json"
    )
    assert candidate_path.exists()
    assert metadata_path.exists()

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["candidate_persisted"] is True
    assert "CONFIGURATION_FIELD_MISSING" in metadata["issue_codes_before"]
    assert "CONFIGURATION_FIELD_MISSING" in metadata["issue_codes_after"]
    assert metadata["path"] == "app/integrations/google_drive.py"


def test_unresolved_symbol_targets_consumer_and_prompt_contains_symbol() -> None:
    class InspectingUnresolvedLLM(FakeRepairLLM):
        def __call__(self, **kwargs) -> str:
            prompt = kwargs["prompt"]
            self.calls.append(prompt)
            requested_path = _requested_path_from_prompt(prompt)
            if requested_path == "app/api/endpoints/upload.py":
                return _fixed_endpoint_response()
            return super().__call__(**kwargs)

    llm = InspectingUnresolvedLLM()
    generated_files = _valid_generated_files()
    generated_files[0] = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content=(
            "from fastapi import APIRouter, HTTPException\n"
            "from app.integrations.google_drive import upload_to_drive\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/upload')\n"
            "async def upload_handler():\n"
            "    result = missing_name\n"
            "    if not result:\n"
            "        raise HTTPException(status_code=502, detail='upload failed')\n"
            "    return result\n"
        ),
    )

    repaired_files, issues = repair_generated_project(
        generated_files=generated_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )

    endpoint = next(item for item in repaired_files if item.path.endswith("upload.py"))
    assert "missing_name" not in endpoint.content
    assert not issues

    prompt = "\n".join(llm.calls)
    assert "UNRESOLVED_PYTHON_SYMBOL" in prompt
    assert "missing_name" in prompt
    assert "unresolved_symbol" in prompt


def test_repair_classification_sets_are_disjoint() -> None:
    assert not (CONTRACT_STRUCTURE_BLOCKERS & IMPLEMENTATION_REPAIRABLE_CODES)


def test_contract_blockers_and_implementation_repairs_do_not_overlap() -> None:
    overlap = CONTRACT_STRUCTURE_BLOCKERS & ALL_REPAIRABLE_CODES
    assert overlap == set()


def test_signature_mismatch_is_classified_as_implementation_repairable() -> None:
    issue = CodegenIssue(
        code="FILE_CONTRACT_CALL_SIGNATURE_MISMATCH",
        path="app/api/endpoints/example.py",
        message="signature mismatch",
        details={"symbol": "persist_resource"},
    )
    contract = {"kind": "endpoint"}
    assert (
        classify_issue_for_repair(issue, contract=contract)
        == IssueRepairClass.IMPLEMENTATION_REPAIRABLE
    )


def test_contract_blocker_is_classified_explicitly() -> None:
    issue = CodegenIssue(
        code="FILE_INTERNAL_CALL_TARGET_MISSING",
        path="app/api/endpoints/example.py",
        message="missing target",
    )
    contract = {"kind": "endpoint"}
    assert (
        classify_issue_for_repair(issue, contract=contract)
        == IssueRepairClass.CONTRACT_BLOCKER
    )


def test_unknown_issue_is_classified_as_unknown() -> None:
    issue = CodegenIssue(
        code="SOME_UNKNOWN_ISSUE",
        path="app/api/endpoints/example.py",
        message="unknown",
    )
    contract = {"kind": "endpoint"}
    assert classify_issue_for_repair(issue, contract=contract) == IssueRepairClass.UNKNOWN


def test_unresolved_python_symbol_is_classified_as_repairable() -> None:
    issue = CodegenIssue(
        code="UNRESOLVED_PYTHON_SYMBOL",
        path="app/integrations/google_drive.py",
        message="missing symbol",
        details={"symbol": "package", "line": 4},
    )
    contract = next(
        item
        for item in _base_contracts()
        if item["path"] == "app/integrations/google_drive.py"
    )
    assert _is_repairable_issue(issue, contract) is True
    assert (
        classify_issue_for_repair(issue, contract=contract)
        == IssueRepairClass.IMPLEMENTATION_REPAIRABLE
    )


def test_unresolved_symbol_structured_issue_uses_details_not_message() -> None:
    issue = CodegenIssue(
        code="UNRESOLVED_PYTHON_SYMBOL",
        path="app/integrations/google_drive.py",
        message="human message without parseable symbol",
        details={"symbol": "package", "line": 4},
    )
    contract = next(
        item
        for item in _base_contracts()
        if item["path"] == "app/integrations/google_drive.py"
    )
    structured = _build_structured_issues(
        path_issues=[issue],
        target_contract=contract,
        related_contracts=[],
    )
    assert structured[0]["unresolved_symbol"] == "package"
    assert "Missing symbol:\npackage" in structured[0]["strict_instruction"]


def test_unresolved_symbol_import_repair_is_accepted() -> None:
    class ImportRepairLLM(FakeRepairLLM):
        def __call__(self, **kwargs) -> str:
            prompt = kwargs["prompt"]
            self.calls.append(prompt)
            requested_path = _requested_path_from_prompt(prompt)
            if requested_path == "app/integrations/google_drive.py":
                return (
                    '{"path":"app/integrations/google_drive.py","content":"'
                    'from app.core.config import get_settings\\n'
                    'import package.auth\\n'
                    'from package.transport import Request\\n\\n'
                    'def upload_to_drive(payload):\\n'
                    '    settings = get_settings()\\n'
                    '    creds, _ = package.auth.default()\\n'
                    '    return {\\"creds\\": bool(creds), \\"folder\\": settings.GOOGLE_DRIVE_FOLDER_ID}\\n"}'
                )
            return super().__call__(**kwargs)

    llm = ImportRepairLLM()
    generated_files = _valid_generated_files()
    generated_files[1] = GeneratedFile(
        path="app/integrations/google_drive.py",
        content=(
            "from app.core.config import get_settings\n"
            "from package.transport import Request\n\n"
            "def upload_to_drive(payload):\n"
            "    settings = get_settings()\n"
            "    return package.auth.default()\n"
        ),
    )
    contracts = _base_contracts()
    contracts[1]["allowed_imports"] = [{"import_root": "package", "package": "package-sdk"}]
    spec = _base_spec()
    spec["dependencies"] = ["fastapi", "package-sdk"]

    repaired_files, issues = repair_generated_project(
        generated_files=generated_files,
        file_contracts=contracts,
        spec=spec,
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )

    integration = next(item for item in repaired_files if item.path.endswith("google_drive.py"))
    assert "import package.auth" in integration.content
    assert not issues


def test_target_missing_blocks_while_signature_mismatch_repairs() -> None:
    issues = [
        CodegenIssue(
            code="FILE_INTERNAL_CALL_TARGET_MISSING",
            path="app/api/endpoints/broken_contract.py",
            message="missing target",
            symbol="upload_to_drive",
        ),
        CodegenIssue(
            code="FILE_CONTRACT_CALL_SIGNATURE_MISMATCH",
            path="app/api/endpoints/upload.py",
            message="wrong caller signature",
            symbol="upload_to_drive",
        ),
    ]
    contracts = _base_contracts() + [
        {
            "path": "app/api/endpoints/broken_contract.py",
            "kind": "endpoint",
            "required_symbols": ["router", "broken_handler"],
            "provided_interfaces": [],
            "required_internal_calls": [],
            "allowed_imports": [],
            "actions": [],
            "must_implement": [],
            "must_not": [],
            "implementation_plan": [],
            "errors": [],
            "configuration": [],
        }
    ]

    repair_paths = repair_paths_for_issues(issues, file_contracts=contracts)
    assert "app/api/endpoints/broken_contract.py" not in repair_paths
    assert "app/api/endpoints/upload.py" in repair_paths


def test_real_bug_regression_selects_both_upload_and_integration_paths() -> None:
    issues = [
        CodegenIssue(
            code="FILE_CONTRACT_CALL_SIGNATURE_MISMATCH",
            path="app/api/endpoints/upload.py",
            message="wrong caller signature",
            symbol="upload_to_drive",
        ),
        CodegenIssue(
            code="UNRESOLVED_PYTHON_SYMBOL",
            path="app/integrations/google_drive.py",
            message="missing symbol",
            details={"symbol": "package"},
        ),
    ]

    repair_paths = set(repair_paths_for_issues(issues, file_contracts=_base_contracts()))
    assert repair_paths == {
        "app/api/endpoints/upload.py",
        "app/integrations/google_drive.py",
    }


def test_blocker_is_scoped_per_path_and_independent_path_is_repaired() -> None:
    issues = [
        CodegenIssue(
            code="FILE_INTERNAL_CALL_TARGET_MISSING",
            path="app/api/endpoints/broken_contract.py",
            message="missing target",
        ),
        CodegenIssue(
            code="UNRESOLVED_PYTHON_SYMBOL",
            path="app/integrations/google_drive.py",
            message="missing symbol",
            details={"symbol": "package"},
        ),
    ]
    contracts = _base_contracts() + [
        {
            "path": "app/api/endpoints/broken_contract.py",
            "kind": "endpoint",
            "required_symbols": ["router", "broken_handler"],
            "provided_interfaces": [],
            "required_internal_calls": [],
            "allowed_imports": [],
            "actions": [],
            "must_implement": [],
            "must_not": [],
            "implementation_plan": [],
            "errors": [],
            "configuration": [],
        }
    ]

    repair_paths = set(repair_paths_for_issues(issues, file_contracts=contracts))
    assert "app/api/endpoints/broken_contract.py" not in repair_paths
    assert "app/integrations/google_drive.py" in repair_paths


def test_same_path_blocker_wins_over_repairable_issue() -> None:
    issues = [
        CodegenIssue(
            code="FILE_INTERNAL_CALL_TARGET_MISSING",
            path="app/integrations/google_drive.py",
            message="missing target",
        ),
        CodegenIssue(
            code="UNRESOLVED_PYTHON_SYMBOL",
            path="app/integrations/google_drive.py",
            message="missing symbol",
            details={"symbol": "package"},
        ),
    ]

    repair_paths = repair_paths_for_issues(issues, file_contracts=_base_contracts())
    assert "app/integrations/google_drive.py" not in repair_paths


def test_round_debug_file_persists_issue_repair_class() -> None:
    llm = FakeRepairLLM()
    generated_files = _valid_generated_files()
    generated_files[0] = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content=(
            "from fastapi import APIRouter, HTTPException\n"
            "from app.integrations.google_drive import upload_to_drive\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/upload')\n"
            "async def upload_handler():\n"
            "    result = upload_to_drive()\n"
            "    if not result:\n"
            "        raise HTTPException(status_code=502, detail='upload failed')\n"
            "    return result\n"
        ),
    )

    repair_generated_project(
        generated_files=generated_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )

    payload = json.loads(
        Path("output/_debug/codegen_repair_round_1.json").read_text(encoding="utf-8")
    )
    assert any(
        issue["code"] == "FILE_CONTRACT_CALL_SIGNATURE_MISMATCH"
        and issue["repair_class"] == "implementation_repairable"
        for issue in payload["before_issues"]
    )


def test_end_to_end_repair_loop_revalidates_and_clears_codegen_status_blockers() -> None:
    llm = FakeRepairLLM()
    generated_files = _valid_generated_files()
    generated_files[0] = GeneratedFile(
        path="app/api/endpoints/upload.py",
        content=(
            "from fastapi import APIRouter, HTTPException\n"
            "from app.integrations.google_drive import upload_to_drive\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/upload')\n"
            "async def upload_handler():\n"
            "    result = upload_to_drive()\n"
            "    if not result:\n"
            "        raise HTTPException(status_code=502, detail='upload failed')\n"
            "    return result\n"
        ),
    )
    generated_files[1] = GeneratedFile(
        path="app/integrations/google_drive.py",
        content=(
            "from app.core.config import get_settings\n"
            "from googleapiclient.discovery import build\n\n"
            "def upload_to_drive(payload):\n"
            "    settings = get_settings()\n"
            "    service = build('drive', 'v3', cache_discovery=False)\n"
            "    request = package.files().create(body={'parents': [settings.GOOGLE_DRIVE_FOLDER_ID]})\n"
            "    return request.execute()\n"
        ),
    )

    repaired_files, issues = repair_generated_project(
        generated_files=generated_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=2,
    )

    assert not issues
    final_issues = validate_generated_project(
        generated_files=repaired_files,
        file_contracts=_base_contracts(),
        spec=_base_spec(),
    )
    assert [issue for issue in final_issues if issue.severity == "error"] == []


def test_non_converged_configuration_field_writes_diagnostic_file() -> None:
    class BadConfigFieldLLM(FakeRepairLLM):
        def __call__(self, **kwargs) -> str:
            prompt = kwargs["prompt"]
            self.calls.append(prompt)
            requested_path = _requested_path_from_prompt(prompt)
            if requested_path == "app/integrations/google_drive.py":
                return (
                    '{"path":"app/integrations/google_drive.py","content":"'
                    'from app.core.config import get_settings\\n\\n'
                    'def upload_to_drive(payload):\\n'
                    '    cfg = get_settings()\\n'
                    '    return cfg.UNKNOWN_FIELD\\n"}'
                )
            return super().__call__(**kwargs)

    llm = BadConfigFieldLLM()
    generated_files = _valid_generated_files()
    generated_files[1] = GeneratedFile(
        path="app/integrations/google_drive.py",
        content=(
            "from app.core.config import get_settings\n\n"
            "def upload_to_drive(payload):\n"
            "    cfg = get_settings()\n"
            "    return cfg.UNKNOWN_FIELD\n"
        ),
    )
    contracts = _base_contracts()
    contracts[1]["configuration_access"] = {
        "provider_module": "app.core.config",
        "provider_symbol": "get_settings",
        "allowed_fields": ["RESOURCE_ID"],
        "access_mode": "lazy",
    }
    contracts[1]["authentication_constraints"] = {
        "credential_source": "runtime",
        "allows_embedded_secret": False,
        "allows_static_credential_file": False,
    }
    contracts[1]["authentication_runtime_contract"] = {
        "discovery": "ambient",
        "requires_configuration_field": False,
        "requires_static_credential_file": False,
        "requires_embedded_secret": False,
    }

    _files, issues = repair_generated_project(
        generated_files=generated_files,
        file_contracts=contracts,
        spec=_base_spec(),
        descripcion_global="",
        contexto_normalizado={},
        llm_call=llm,
        max_rounds=1,
    )

    assert any(issue.code == "CONFIGURATION_FIELD_MISSING" for issue in issues)

    diagnostic_path = Path(
        "output/_debug/codegen_repair_not_converged_app_integrations_google_drive_py.json"
    )
    assert diagnostic_path.exists()

    payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert payload["original_issue"] == "CONFIGURATION_FIELD_MISSING"
    assert payload["field"] == "UNKNOWN_FIELD"
    assert payload["allowed_fields"] == ["RESOURCE_ID"]
    assert payload["same_issue_persisted"] is True
