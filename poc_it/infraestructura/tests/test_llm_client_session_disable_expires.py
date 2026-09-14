from __future__ import annotations

import pytest

from poc_it.infraestructura import llm_client as mod
from poc_it.infraestructura.llm_client import LLMError, solicitarRespuestaTextual


def test_session_disabled_provider_recovers_after_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regresión: antes, un rate limit marcaba el proveedor como deshabilitado
    PERMANENTEMENTE para el resto del proceso (`SESSION_PROVIDER_DISABLED[provider] = True`,
    sin expiración). En un pipeline largo (codegen + tests + estimaciones + documentación, ~10-15
    min), un rate limit puntual muy al principio dejaba ese proveedor descartado justo cuando más
    falta hacía como fallback al final (fase de documentación). Ahora debe expirar."""
    monkeypatch.setattr(mod, "LITELLM_PROXY_ONLY", False)
    monkeypatch.setattr(mod, "LLM_DIRECT_ONLY", True)
    monkeypatch.setattr(mod, "PROVIDER_COOLDOWN", {})
    monkeypatch.setattr(mod, "SESSION_PROVIDER_DISABLED", {})

    fake_now = [1_000_000.0]
    monkeypatch.setattr(mod._time, "time", lambda: fake_now[0])

    def _rate_limited(*_args, **_kwargs):
        raise mod.LLMError("429 rate limit exceeded")

    monkeypatch.setattr(mod, "_call_groq", _rate_limited)
    monkeypatch.setattr(mod, "_call_gemini", lambda *_a, **_k: (_ for _ in ()).throw(mod.LLMError("no key")))
    monkeypatch.setattr(mod, "_call_mistral", lambda *_a, **_k: (_ for _ in ()).throw(mod.LLMError("no key")))
    monkeypatch.setattr(mod, "_call_cerebras", lambda *_a, **_k: (_ for _ in ()).throw(mod.LLMError("no key")))
    monkeypatch.setattr(mod, "_call_openrouter", lambda *_a, **_k: (_ for _ in ()).throw(mod.LLMError("no key")))
    monkeypatch.setattr(mod, "_call_ollama", lambda *_a, **_k: (_ for _ in ()).throw(mod.LLMError("no key")))

    # Primera llamada: groq da 429 -> queda marcado en SESSION_PROVIDER_DISABLED con expiración.
    with pytest.raises(LLMError):
        solicitarRespuestaTextual(prompt="hola", fase="documentacion")
    assert "groq" in mod.SESSION_PROVIDER_DISABLED
    expiry = mod.SESSION_PROVIDER_DISABLED["groq"]
    assert expiry == pytest.approx(fake_now[0] + mod.SESSION_DISABLE_SECONDS)

    # Justo antes de expirar: groq se sigue saltando (no debería ni intentarse).
    fake_now[0] = expiry - 1
    groq_called = []
    monkeypatch.setattr(mod, "_call_groq", lambda *_a, **_k: groq_called.append(1) or (_ for _ in ()).throw(mod.LLMError("429")))
    with pytest.raises(LLMError):
        solicitarRespuestaTextual(prompt="hola", fase="documentacion")
    assert groq_called == []

    # Tras expirar: groq vuelve a intentarse (y esta vez responde bien).
    fake_now[0] = expiry + 1
    monkeypatch.setattr(mod, "_call_groq", lambda *_a, **_k: "respuesta ok")
    result = solicitarRespuestaTextual(prompt="hola", fase="documentacion")
    assert result == "respuesta ok"
