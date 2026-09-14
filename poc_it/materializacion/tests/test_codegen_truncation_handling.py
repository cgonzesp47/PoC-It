from __future__ import annotations

import json

import pytest

from poc_it.infraestructura.llm_client import (
    CODEGEN_ALIAS,
    DEFAULT_DIRECT_CHAIN,
    DIRECT_CHAIN_BY_ALIAS,
    LLMTruncatedResponseError,
    _extract_gemini_content,
    _extract_openai_compatible_content,
    _resolve_direct_chain,
    _resolve_effective_alias,
    _resolve_policy_phase,
    solicitarRespuestaTextual,
)
from poc_it.materializacion.codegen.file_generator import FileContractGenerator


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
            {"name": "build_client"},
            {"name": "upload_to_external_storage"},
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


def test_extract_openai_compatible_content_raises_on_finish_reason_length():
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


def test_extract_openai_compatible_content_accepts_stop():
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


def test_extract_gemini_content_raises_on_max_tokens():
    response = {
        "candidates": [
            {
                "finishReason": "MAX_TOKENS",
                "content": {
                    "parts": [
                        {
                            "text": '{"path":"app/x.py","content":"partial'
                        }
                    ]
                },
            }
        ]
    }

    with pytest.raises(LLMTruncatedResponseError):
        _extract_gemini_content(
            response,
            model="gemini-test",
        )


def test_extract_gemini_content_accepts_stop():
    response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "text": '{"path":"app/x.py","content":"pass"}'
                        }
                    ]
                },
            }
        ]
    }

    content = _extract_gemini_content(
        response,
        model="gemini-test",
    )

    assert '"path":"app/x.py"' in content


def test_extract_gemini_content_returns_invalid_json_when_stop():
    response = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "text": "{invalid"
                        }
                    ]
                },
            }
        ]
    }

    content = _extract_gemini_content(
        response,
        model="gemini-test",
    )

    assert content == "{invalid"


def test_codegen_subphases_resolve_to_codegen():
    assert _resolve_policy_phase("generacion_codigo") == "generacion_codigo"
    assert (
        _resolve_policy_phase("generacion_codigo_file_contract")
        == "generacion_codigo"
    )
    assert (
        _resolve_policy_phase("generacion_codigo_file_contract_fix")
        == "generacion_codigo"
    )
    assert (
        _resolve_policy_phase("analisis_requisitos")
        == "analisis_requisitos"
    )


def test_codegen_subphase_resolves_code_gen_alias():
    alias = _resolve_effective_alias(
        fase="generacion_codigo_file_contract",
        provider_hint=None,
    )
    assert alias == CODEGEN_ALIAS

    alias_with_hint = _resolve_effective_alias(
        fase="generacion_codigo_file_contract",
        provider_hint=CODEGEN_ALIAS,
    )
    assert alias_with_hint == CODEGEN_ALIAS


def test_codegen_alias_is_canonical_and_gen_code_does_not_exist():
    assert "gen-code" not in DIRECT_CHAIN_BY_ALIAS
    assert CODEGEN_ALIAS == "code-gen"
    assert CODEGEN_ALIAS in DIRECT_CHAIN_BY_ALIAS


def test_codegen_direct_chain_is_specific_and_not_generic():
    chain = _resolve_direct_chain(
        fase="generacion_codigo_file_contract",
        provider_hint=CODEGEN_ALIAS,
    )

    assert chain == ["mistral", "cerebras"]
    assert "groq" not in chain
    assert "gemini" not in chain
    assert "openrouter" not in chain
    assert "ollama" not in chain


def test_unknown_phase_uses_default_direct_chain():
    chain = _resolve_direct_chain(
        fase="unknown_phase",
        provider_hint=None,
    )

    assert chain == DEFAULT_DIRECT_CHAIN


def test_file_generator_retries_after_truncation_and_succeeds():
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
                    "def build_client():\n"
                    "    return object()\n\n"
                    "def upload_to_external_storage():\n"
                    "    return None\n"
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

    assert result.ok is False
    assert result.file is None
    assert result.attempts == 2
    assert result.issues
    issue_codes = {issue.code for issue in result.issues}
    assert "CODE_REQUIRED_CONFIGURATION_UNUSED" in issue_codes
    assert calls == [
        "generacion_codigo_file_contract",
        "generacion_codigo_file_contract_fix",
    ]


def test_file_generator_uses_code_gen_provider_hint():
    captured: list[tuple[str, str]] = []

    def fake_llm_call(**kwargs: str) -> str:
        captured.append((kwargs["fase"], kwargs["provider_hint"]))
        raise LLMTruncatedResponseError(
            provider="mistral",
            model=CODEGEN_ALIAS,
            finish_reason="length",
            partial_content='{"path":"app/integrations/external_service.py","content":"partial',
        )

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
    assert captured == [
        ("generacion_codigo_file_contract", CODEGEN_ALIAS),
    ]


def test_file_generator_keeps_json_parse_failed_for_complete_invalid_json():
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


def test_solicitar_respuesta_textual_codegen_uses_specific_direct_chain_after_proxy_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    def fake_proxy(*args, **kwargs):
        calls.append("litellm_proxy")
        raise RuntimeError("proxy down")

    def fake_mistral(*args, **kwargs):
        calls.append("mistral")
        raise LLMTruncatedResponseError(
            provider="mistral",
            model="codestral-latest",
            finish_reason="length",
            partial_content='{"path":"app/x.py","content":"partial',
        )

    def fake_cerebras(*args, **kwargs):
        calls.append("cerebras")
        return '{"path":"app/x.py","content":"pass"}'

    def fail_if_called(name: str):
        def _raiser(*args, **kwargs):
            raise AssertionError(f"{name} no debería ejecutarse")
        return _raiser

    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client.LLM_DIRECT_ONLY",
        False,
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client.LITELLM_PROXY_ONLY",
        False,
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client._call_litellm_proxy",
        fake_proxy,
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client._call_mistral",
        fake_mistral,
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client._call_cerebras",
        fake_cerebras,
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client._call_groq",
        fail_if_called("groq"),
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client._call_gemini",
        fail_if_called("gemini"),
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client._call_openrouter",
        fail_if_called("openrouter"),
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client._call_ollama",
        fail_if_called("ollama"),
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client.PROVIDER_COOLDOWN",
        {},
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client.SESSION_PROVIDER_DISABLED",
        {},
    )

    text = solicitarRespuestaTextual(
        prompt="devuelve json",
        fase="generacion_codigo_file_contract",
        provider_hint=CODEGEN_ALIAS,
    )

    assert '"path":"app/x.py"' in text
    assert calls == ["litellm_proxy", "mistral", "cerebras"]


def test_litellm_proxy_retries_same_alias_with_more_tokens_on_truncation(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regresión: un truncamiento (`finish_reason=length`) en un alias del proxy (p.ej.
    `docs-groq`) descartaba ese alias y saltaba al siguiente SIN darle más presupuesto de tokens
    — aunque un truncamiento significa exactamente eso: el modelo quería seguir escribiendo. Debe
    reintentar el MISMO alias con más `max_tokens` antes de rendirse y pasar al siguiente."""
    calls: list[tuple[str, int | None]] = []

    def fake_proxy(messages, model=None, temperature=0.0, max_tokens=None, timeout=70, **extra):
        calls.append((model, max_tokens))
        if max_tokens is not None and max_tokens < 2000:
            raise LLMTruncatedResponseError(
                provider="litellm_proxy",
                model=model,
                finish_reason="length",
                partial_content="respuesta parcial truncada",
            )
        return "respuesta completa"

    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client._call_litellm_proxy",
        fake_proxy,
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client.PROVIDER_COOLDOWN",
        {},
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client.SESSION_PROVIDER_DISABLED",
        {},
    )

    text = solicitarRespuestaTextual(
        prompt="genera documentación",
        fase="documentacion",
        max_tokens=1200,
    )

    assert text == "respuesta completa"
    # El primer intento del primer alias truncó con max_tokens=1200; el reintento (mismo alias)
    # debe haber duplicado el presupuesto (2400) antes de tener éxito — nunca debería haber
    # saltado a un alias distinto solo por el truncamiento.
    assert len(calls) == 2
    first_alias, first_tokens = calls[0]
    second_alias, second_tokens = calls[1]
    assert first_alias == second_alias
    assert first_tokens == 1200
    assert second_tokens == 2400


def test_litellm_proxy_keeps_boosting_tokens_across_multiple_truncations(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regresión: un único reintento (1200 -> 2400) no bastaba cuando la respuesta deseada
    necesitaba más presupuesto todavía (visto en producción: un README truncado incluso tras
    duplicar una vez). Debe seguir duplicando (2400 -> 4800 -> 6000) hasta caber o agotar el tope,
    no rendirse tras el primer reintento fallido."""
    calls: list[int | None] = []

    def fake_proxy(messages, model=None, temperature=0.0, max_tokens=None, timeout=70, **extra):
        calls.append(max_tokens)
        if max_tokens is not None and max_tokens < 4800:
            raise LLMTruncatedResponseError(
                provider="litellm_proxy",
                model=model,
                finish_reason="length",
                partial_content="respuesta parcial truncada",
            )
        return "respuesta completa"

    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client._call_litellm_proxy",
        fake_proxy,
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client.PROVIDER_COOLDOWN",
        {},
    )
    monkeypatch.setattr(
        "poc_it.infraestructura.llm_client.SESSION_PROVIDER_DISABLED",
        {},
    )

    text = solicitarRespuestaTextual(
        prompt="genera documentación",
        fase="documentacion",
        max_tokens=1200,
    )

    assert text == "respuesta completa"
    assert calls == [1200, 2400, 4800]
