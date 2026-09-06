from __future__ import annotations

import pytest

from poc_it.infraestructura import llm_client
from poc_it.infraestructura.llm_client import LLMError, solicitarRespuestaTextual


def _raise(message: str):
    def _inner(*_args, **_kwargs):
        raise RuntimeError(message)

    return _inner


def test_all_providers_failed_reports_every_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regresión: cuando todos los proveedores fallan, el mensaje final ('All providers failed.')
    solo incluía el detalle de Groq/OpenRouter/Ollama. Si el/los proveedor(es) que realmente
    fallaban eran otros (litellm_proxy, gemini, cerebras, mistral — como pasa en la fase de
    documentación, que sí intenta esos), el mensaje quedaba vacío de contexto y era imposible
    diagnosticar la causa real sin activar logging DEBUG."""
    monkeypatch.setattr(llm_client, "_call_litellm_proxy", _raise("proxy connection refused"))
    monkeypatch.setattr(llm_client, "_call_openrouter", _raise("openrouter 401"))
    monkeypatch.setattr(llm_client, "_call_gemini", _raise("gemini quota exceeded"))
    monkeypatch.setattr(llm_client, "_call_groq", _raise("groq rate limited"))
    monkeypatch.setattr(llm_client, "_call_cerebras", _raise("cerebras 500"))
    monkeypatch.setattr(llm_client, "_call_mistral", _raise("mistral timeout"))
    monkeypatch.setattr(llm_client, "_call_ollama", _raise("ollama not running"))

    monkeypatch.setattr(llm_client, "SESSION_PROVIDER_DISABLED", {})
    monkeypatch.setattr(llm_client, "PROVIDER_COOLDOWN", {})
    # Forzamos que se intente la cadena de fallback directa (proxy -> openrouter/gemini/groq/
    # cerebras/mistral) independientemente de cómo esté configurado este entorno local
    # (aquí LITELLM_PROXY_ONLY=True, que en producción limita a solo litellm_proxy).
    monkeypatch.setattr(llm_client, "LITELLM_PROXY_ONLY", False)
    monkeypatch.setattr(llm_client, "LLM_DIRECT_ONLY", False)

    with pytest.raises(LLMError) as excinfo:
        solicitarRespuestaTextual(
            prompt="hola",
            fase="documentacion",
        )

    message = str(excinfo.value)
    assert "All providers failed." in message
    # Antes del fix, solo groq/openrouter/ollama aparecían aquí: gemini/cerebras/mistral/
    # litellm_proxy se descartaban en silencio.
    assert "gemini quota exceeded" in message
    assert "cerebras 500" in message
    assert "mistral timeout" in message
    assert "proxy connection refused" in message
    assert "groq rate limited" in message
    assert "openrouter 401" in message
