from __future__ import annotations

import json

import poc_it.materializacion.generador_artefactos as facade
import pytest

from poc_it.infraestructura.llm_client import (
    LLMTruncatedResponseError,
    _extract_openai_compatible_content,
)
from poc_it.materializacion.codegen import sort_file_contracts_for_generation
from poc_it.materializacion.codegen.file_generator import FileContractGenerator


def test_generation_order_is_exported_by_codegen_package() -> None:
    contracts = [
        {
            "path": "app/api/endpoints/items.py",
            "kind": "endpoint",
        },
        {
            "path": "app/services/items.py",
            "kind": "service",
        },
    ]

    ordered = sort_file_contracts_for_generation(contracts)

    assert [item["kind"] for item in ordered] == [
        "service",
        "endpoint",
    ]


def test_codegen_order_has_no_legacy_private_facade_alias() -> None:
    legacy_private_name = "_" + "sort_file_contracts_for_generation"

    assert not hasattr(
        facade,
        legacy_private_name,
    )


def _file_contract() -> dict:
    return {
        "path": "app/integrations/external_service.py",
        "kind": "integration",
        "required_symbols": ["build_client", "upload_to_external_storage"],
        "must_implement": ["Implement required symbols"],
        "must_not": ["Do not open external connections at import time"],
        "implementation_plan": ["Create lazy client", "Expose upload function"],
        "actions": [
            {
                "id": "upload_to_external_storage",
                "required": True,
                "integration_ref": "external_storage",
            }
        ],
        "errors": [],
        "integration_refs": ["external_storage"],
        "configuration": [{"key": "API_KEY", "delivery": "env"}],
        "provided_interfaces": [
            {"symbol": "build_client", "kind": "function"},
            {
                "symbol": "upload_to_external_storage",
                "kind": "function",
            },
        ],
        "required_internal_calls": [],
    }


def _spec() -> dict:
    return {
        "entrypoint": "app.main:app",
        "dependencies": ["requests"],
        "files": ["app/integrations/external_service.py"],
        "implementation_files": [
            {
                "path": "app/integrations/external_service.py",
                "kind": "integration",
            }
        ],
    }


def test_extract_openai_compatible_content_raises_on_finish_reason_length() -> None:
    response = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "content": '{"path":"app/x.py","content":"partial'
                },
            }
        ]
    }

    with pytest.raises(LLMTruncatedResponseError):
        _extract_openai_compatible_content(
            response,
            provider="test",
            model="model-x",
        )


def test_extract_openai_compatible_content_accepts_stop() -> None:
    response = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": '{"path":"app/x.py","content":"pass"}'
                },
            }
        ]
    }

    content = _extract_openai_compatible_content(
        response,
        provider="test",
        model="model-x",
    )

    assert '"path":"app/x.py"' in content


def test_file_generator_retries_after_truncation_and_succeeds() -> None:
    calls: list[str] = []

    def fake_llm_call(**kwargs: str) -> str:
        calls.append(kwargs["fase"])
        if len(calls) == 1:
            raise LLMTruncatedResponseError(
                provider="litellm_proxy",
                model="code-gen",
                finish_reason="length",
                partial_content='{"path":"app/integrations/external_service.py","content":"partial',
            )
        return json.dumps(
            {
                "path": "app/integrations/external_service.py",
                "content": (
                    "def get_settings():\n"
                    "    return {'api_key': 'env'}\n\n"
                    "def build_client():\n"
                    "    settings = get_settings()\n"
                    "    return object(), settings\n\n"
                    "def upload_to_external_storage():\n"
                    "    client, _settings = build_client()\n"
                    "    return client\n"
                ),
            }
        )

    generator = FileContractGenerator(
        llm_call=fake_llm_call,
        max_attempts=2,
    )

    result = generator.generate(
        spec=_spec(),
        file_contract=_file_contract(),
        related_contracts=[],
        descripcion_global="Generate integration file",
        contexto_normalizado={},
    )

    assert result.ok is True
    assert result.file is not None
    assert result.file.path == "app/integrations/external_service.py"
    assert result.attempts == 2
    assert calls == [
        "generacion_codigo_file_contract",
        "generacion_codigo_file_contract_fix",
    ]


def test_file_generator_keeps_json_parse_failed_for_complete_invalid_json() -> None:
    def fake_llm_call(**kwargs: str) -> str:
        return "{invalid-json"

    generator = FileContractGenerator(
        llm_call=fake_llm_call,
        max_attempts=1,
    )

    result = generator.generate(
        spec=_spec(),
        file_contract=_file_contract(),
        related_contracts=[],
        descripcion_global="Generate integration file",
        contexto_normalizado={},
    )

    assert result.ok is False
    assert result.issues
    assert result.issues[0].code == "CODEGEN_JSON_PARSE_FAILED"
