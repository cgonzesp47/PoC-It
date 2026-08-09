from __future__ import annotations

from poc_it.generador.prompts_file_contracts import (
    build_prompt_file_contract,
    build_prompt_file_contract_fix,
)
from poc_it.materializacion.codegen.models import CodegenIssue, GeneratedFile
from poc_it.materializacion.codegen.semantic_repair import (
    _candidate_repair_is_acceptable,
    _repair_targets_for_issue,
    repair_paths_for_issues,
)
from poc_it.materializacion.codegen.semantic_validation import (
    validate_generated_file_semantics,
)
from poc_it.materializacion.codegen.test_planning import (
    build_endpoint_test_contracts,
    build_test_plan_debug_artifact,
)


def _codes(issues):
    return {issue.code for issue in issues}


def test_configuration_field_allowed_passes() -> None:
    file_contract = {
        "kind": "integration",
        "path": "app/integrations/storage.py",
        "configuration_access": {
            "provider_module": "app.core.config",
            "provider_symbol": "get_settings",
            "allowed_fields": ["RESOURCE_ID"],
            "access_mode": "lazy",
        },
        "provided_interfaces": [{"symbol": "store_resource", "parameters": []}],
    }
    generated_file = GeneratedFile(
        path="app/integrations/storage.py",
        content="""
from app.core.config import get_settings

def store_resource():
    cfg = get_settings()
    value = cfg.RESOURCE_ID
    return value
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "CONFIGURATION_FIELD_MISSING" not in _codes(issues)


def test_configuration_field_invented_fails() -> None:
    file_contract = {
        "kind": "integration",
        "path": "app/integrations/storage.py",
        "configuration_access": {
            "provider_module": "app.core.config",
            "provider_symbol": "get_settings",
            "allowed_fields": ["RESOURCE_ID"],
            "access_mode": "lazy",
        },
        "provided_interfaces": [{"symbol": "store_resource", "parameters": []}],
    }
    generated_file = GeneratedFile(
        path="app/integrations/storage.py",
        content="""
from app.core.config import get_settings

def store_resource():
    cfg = get_settings()
    value = cfg.EXTRA_FIELD
    return value
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "CONFIGURATION_FIELD_MISSING" in _codes(issues)


def test_repair_candidate_accepts_removal_of_invalid_configuration_field_issue() -> None:
    before_issues = [
        CodegenIssue(
            code="CONFIGURATION_FIELD_MISSING",
            path="app/integrations/storage.py",
            message="invalid field",
        )
    ]
    after_issues: list[CodegenIssue] = []

    assert _candidate_repair_is_acceptable(
        before_issues=before_issues,
        after_issues=after_issues,
        target_path="app/integrations/storage.py",
        target_issues=before_issues,
    )


def test_repair_targets_configuration_issue_to_consumer_file_not_config_provider() -> None:
    issue = CodegenIssue(
        code="CONFIGURATION_FIELD_MISSING",
        path="app/integrations/storage.py",
        message="invalid field",
    )

    targets = _repair_targets_for_issue(issue, file_contracts=[])

    assert targets == ["app/integrations/storage.py"]


def test_repair_rejects_candidate_that_keeps_target_issue_identity() -> None:
    before_issues = [
        CodegenIssue(
            code="CONFIGURATION_FIELD_MISSING",
            path="app/integrations/storage.py",
            message="invalid field",
        )
    ]
    after_issues = [
        CodegenIssue(
            code="CONFIGURATION_FIELD_MISSING",
            path="app/integrations/storage.py",
            message="still invalid field",
        )
    ]

    assert not _candidate_repair_is_acceptable(
        before_issues=before_issues,
        after_issues=after_issues,
        target_path="app/integrations/storage.py",
        target_issues=before_issues,
    )


def test_authentication_constraints_are_rendered_in_codegen_prompt() -> None:
    file_contract = {
        "kind": "integration",
        "path": "app/integrations/storage.py",
        "authentication_constraints": {
            "credential_source": "runtime",
            "allows_embedded_secret": False,
            "allows_static_credential_file": False,
        },
    }

    prompt = build_prompt_file_contract(
        spec={},
        file_contract=file_contract,
        related_contracts=[],
        descripcion_global="demo",
        contexto_normalizado={},
    )

    assert "AUTHENTICATION CONTRACT" in prompt
    assert "credential_source:\nruntime" in prompt
    assert "allows_embedded_secret:\nfalse" in prompt
    assert "allows_static_credential_file:\nfalse" in prompt


def test_authentication_constraints_are_rendered_in_repair_prompt() -> None:
    file_contract = {
        "kind": "integration",
        "path": "app/integrations/storage.py",
        "authentication_constraints": {
            "credential_source": "runtime",
            "allows_embedded_secret": False,
            "allows_static_credential_file": False,
        },
    }

    prompt = build_prompt_file_contract_fix(
        spec={},
        file_contract=file_contract,
        previous_content="def x():\n    return None\n",
        errors=["repair"],
        related_contracts=[],
        structured_issues=[],
        generated_interface_summaries=[],
    )

    assert "AUTHENTICATION CONTRACT" in prompt
    assert "credential_source:\nruntime" in prompt
    assert "allows_embedded_secret:\nfalse" in prompt
    assert "allows_static_credential_file:\nfalse" in prompt


def test_producer_consumer_compatible_interface_passes() -> None:
    file_contract = {
        "kind": "endpoint",
        "path": "app/api/endpoints/resource.py",
        "endpoint": {"methods": ["POST"], "path": "/resource"},
        "required_internal_calls": [
            {
                "module": "app.integrations.storage",
                "symbol": "store_resource",
                "required": True,
                "parameters": [
                    {"name": "name", "type": "string", "required": True},
                    {"name": "content", "type": "bytes", "required": True},
                ],
            }
        ],
    }
    generated_file = GeneratedFile(
        path="app/api/endpoints/resource.py",
        content="""
from fastapi import APIRouter
from app.integrations.storage import store_resource

router = APIRouter()

@router.post("/resource")
async def create_resource():
    name = "demo"
    content = b"payload"
    return store_resource(name=name, content=content)
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "FILE_CONTRACT_DATA_FLOW_MISMATCH" not in _codes(issues)


def test_producer_consumer_incompatible_interface_fails() -> None:
    file_contract = {
        "kind": "endpoint",
        "path": "app/api/endpoints/resource.py",
        "endpoint": {"methods": ["POST"], "path": "/resource"},
        "required_internal_calls": [
            {
                "module": "app.integrations.storage",
                "symbol": "store_resource",
                "required": True,
                "parameters": [
                    {"name": "name", "type": "string", "required": True},
                    {"name": "content", "type": "bytes", "required": True},
                ],
            }
        ],
    }
    generated_file = GeneratedFile(
        path="app/api/endpoints/resource.py",
        content="""
from fastapi import APIRouter
from app.integrations.storage import store_resource

router = APIRouter()

@router.post("/resource")
async def create_resource():
    name = "demo"
    return store_resource(name=name)
""",
    )

    issues = validate_generated_file_semantics(
        generated_file=generated_file,
        file_contract=file_contract,
    )

    assert "FILE_CONTRACT_DATA_FLOW_MISMATCH" in _codes(issues)


def test_integration_endpoint_test_plan_uses_mocked_cases() -> None:
    spec = {
        "endpoints": [
            {
                "method": "POST",
                "path": "/resource",
                "integration_refs": ["storage_service"],
                "errors": [401, 403, 404],
                "request": {
                    "content_type": "application/json",
                    "json_schema": {"type": "object"},
                },
            }
        ]
    }
    file_contracts = [
        {
            "kind": "endpoint",
            "path": "app/api/endpoints/resource.py",
            "endpoint": {"method": "POST", "path": "/resource"},
            "integration_refs": ["storage_service"],
            "required_internal_calls": [],
        }
    ]

    contracts = build_endpoint_test_contracts(
        spec=spec,
        file_contracts=file_contracts,
    )
    kinds = contracts[0].test_kinds
    artifact = build_test_plan_debug_artifact(
        contracts=contracts,
        tests_generated=0,
        tests_executed=0,
        skip_reason="codegen_invalid",
    )

    assert {"kind": "mocked_success"} in kinds
    assert {"kind": "mocked_error", "status": 401} in kinds
    assert {"kind": "mocked_error", "status": 403} in kinds
    assert {"kind": "mocked_error", "status": 404} in kinds
    assert {"kind": "request_body_validation"} in kinds
    assert artifact["skip_reason"] == "codegen_invalid"
    assert artifact["tests_generated"] == 0
    assert artifact["tests_executed"] == 0


def test_endpoint_without_integration_uses_direct_functional_test_plan() -> None:
    spec = {
        "endpoints": [
            {
                "method": "GET",
                "path": "/status",
                "integration_refs": [],
                "response": {"json_schema": {"type": "object"}},
            }
        ]
    }
    file_contracts = [
        {
            "kind": "endpoint",
            "path": "app/api/endpoints/status.py",
            "endpoint": {"method": "GET", "path": "/status"},
            "integration_refs": [],
            "required_internal_calls": [],
        }
    ]

    contracts = build_endpoint_test_contracts(
        spec=spec,
        file_contracts=file_contracts,
    )

    assert {"kind": "direct_functional_test"} in contracts[0].test_kinds
    assert {"kind": "deterministic_response_assertion"} in contracts[0].test_kinds


def test_repair_paths_keep_configuration_issue_repairable() -> None:
    file_contracts = [
        {
            "kind": "integration",
            "path": "app/integrations/storage.py",
        }
    ]
    issues = [
        CodegenIssue(
            code="CONFIGURATION_FIELD_MISSING",
            path="app/integrations/storage.py",
            message="invalid field",
        )
    ]

    assert repair_paths_for_issues(issues, file_contracts=file_contracts) == [
        "app/integrations/storage.py"
    ]
