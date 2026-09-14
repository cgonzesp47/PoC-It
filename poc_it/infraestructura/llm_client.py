"""
Cliente LLM centralizado para PoC-it.

Responsabilidad:
- Encapsular llamadas a LLM (Groq como principal, OpenRouter como fallback).
- Proveer funciones de alto nivel para obtener texto o JSON.
- Evitar que el resto del código conozca detalles de cada proveedor.

Uso esperado en otros módulos:
- from PoC_it.llm_client import chat_completion_text, chat_completion_json
- NO usar más `ollama.chat` directamente.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from poc_it.entrada.demo_progress import is_demo_mode
from poc_it.infraestructura.rate_limiter import (
    GLOBAL_BUCKET,
    exponential_backoff_sleep,
    handle_rate_limit_headers,
    MAX_RETRIES,
)

logger = logging.getLogger(__name__)

# Carga automática del .env de la raíz del proyecto
load_dotenv()

# ==========================================================
# CONFIGURACIÓN BÁSICA
# ==========================================================

# ==========================================================
# LiteLLM Proxy (recomendado para gestionar múltiples proveedores)
# ==========================================================
# En Windows, "localhost" puede resolver a IPv6 (::1) y provocar cuelgues si el servicio solo está accesible por IPv4.
# Por defecto preferimos 127.0.0.1 para entorno local.
_default_proxy_url = "http://127.0.0.1:4000" if os.name == "nt" else "http://localhost:4000"
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", _default_proxy_url).strip().rstrip("/")
LITELLM_PROXY_KEY = os.getenv("LITELLM_PROXY_KEY")  # opcional (si proteges el proxy)

# Si quieres forzar que NUNCA se hagan llamadas directas a proveedores (solo proxy), activa:
#   LITELLM_PROXY_ONLY=1
#
# Si quieres hacer justo lo contrario (saltarte el proxy SIEMPRE y usar solo llamadas directas),
# activa:
#   LLM_DIRECT_ONLY=1
#
# Orden de precedencia:
# - LLM_DIRECT_ONLY=1  => fuerza llamadas directas y desactiva proxy aunque LITELLM_PROXY_ONLY=1
# - LITELLM_PROXY_ONLY=1 => fuerza proxy (sin fallbacks directos)
# - Ninguno => proxy + fallbacks directos como último recurso
LITELLM_PROXY_ONLY = os.getenv("LITELLM_PROXY_ONLY", "0").strip() in ("1", "true", "True", "yes", "YES")
LLM_DIRECT_ONLY = os.getenv("LLM_DIRECT_ONLY", "0").strip() in ("1", "true", "True", "yes", "YES")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "codestral-latest")

# OpenAI deshabilitado (requiere crédito / insufficient_quota en este entorno)
OPENAI_API_KEY = None
OPENAI_MODEL = None

CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Usamos API estable v1 (no v1beta)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1/models"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
OPENAI_URL = None
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"

# LiteLLM Proxy (OpenAI-compatible)
LITELLM_CHAT_URL = f"{LITELLM_BASE_URL}/v1/chat/completions"

# Ollama (fallback local mediante librería oficial)
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen7b:latest")

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_OPENROUTER_MODEL = OPENROUTER_MODEL


class LLMError(Exception):
    """Error genérico de llamadas LLM."""


# Tope al que dejamos crecer max_tokens al reintentar una respuesta truncada (evita reintentos
# desproporcionados/costosos si el prompt en sí induce respuestas muy largas indefinidamente).
_TRUNCATION_RETRY_MAX_TOKENS = 6000

# Cuántos intentos (inicial + reintentos duplicando max_tokens) permitimos por alias antes de
# darlo por perdido y pasar al siguiente. Con un max_tokens inicial pequeño (p.ej. 1200 para el
# README) hacen falta varias duplicaciones para alcanzar el tope: 1200 -> 2400 -> 4800 -> 6000.
_TRUNCATION_RETRY_MAX_ATTEMPTS = 4


class LLMTruncatedResponseError(LLMError):
    def __init__(
        self,
        *,
        provider: str,
        model: Optional[str],
        finish_reason: str,
        partial_content: str,
    ) -> None:
        self.provider = provider
        self.model = model
        self.finish_reason = finish_reason
        self.partial_content = partial_content
        super().__init__(
            "Respuesta LLM truncada "
            f"(provider={provider}, model={model}, "
            f"finish_reason={finish_reason}, chars={len(partial_content)})"
        )


# ==========================================================
# MÉTRICAS DE EJECUCIÓN (observabilidad básica)
# ==========================================================

LLM_METRICS = {
    "total_calls": 0,
    "fallbacks": 0,
    "groq_failures": 0,
    "openrouter_failures": 0,
    "gemini_failures": 0,
    "mistral_failures": 0,
    "openai_failures": 0,
    "cerebras_failures": 0,
    "ollama_failures": 0,
}

# ==========================================================
# POLÍTICA DE MODELOS POR FASE (Multi‑modelo)
# ==========================================================

# Política por fase usando ALIAS del proxy (ver litellm_config.yaml)
CODEGEN_ALIAS = "code-gen"

LLM_POLICY: Dict[str, str] = {
    "normalizacion_contexto": "ctx-json",
    "clasificacion": "cls-json",
    "generacion_codigo": CODEGEN_ALIAS,
    "documentacion": "docs",
    "estimacion": "estimate",
}

# Si no hay hint, este alias suele ser un buen “generalista” barato
FALLBACK_PROVIDER = "docs"

PHASE_ALIAS_FALLBACKS: Dict[str, List[str]] = {
    "normalizacion_contexto": ["ctx-json"],
    "clasificacion": ["cls-json"],
    "documentacion": ["docs"],
    "estimacion": ["estimate"],
    "generacion_codigo": [CODEGEN_ALIAS],
}

DIRECT_CHAIN_BY_ALIAS: Dict[str, List[str]] = {
    "ctx-json": ["mistral", "groq", "gemini", "openrouter"],
    "cls-json": ["mistral", "groq", "gemini", "openrouter"],
    CODEGEN_ALIAS: ["mistral", "cerebras"],
    "docs": ["openrouter", "gemini", "groq", "cerebras", "mistral"],
    "estimate": ["cerebras", "gemini", "groq", "openrouter"],
}

DEFAULT_DIRECT_CHAIN = ["groq", "gemini", "mistral", "cerebras", "openrouter", "ollama"]

# ==========================================================
# CIRCUIT BREAKER SIMPLE POR PROVEEDOR
# ==========================================================

import time as _time

PROVIDER_COOLDOWN: Dict[str, float] = {}
COOLDOWN_SECONDS = 60  # desactiva proveedor 60s tras rate limit crítico

# Cooldown largo del "circuit breaker por ejecución" (ver SESSION_PROVIDER_DISABLED más abajo).
# Antes era permanente para el resto del proceso: en un pipeline de PoC-it que dura 10-15 minutos
# (codegen + tests + estimaciones + documentación), un rate limit puntual muy al principio dejaba
# ese proveedor descartado hasta el final — justo cuando más falta hacía como fallback en la fase
# de documentación. Las ventanas de rate limit de los proveedores gratuitos suelen ser por minuto,
# así que un cooldown largo pero finito recupera el proveedor para el resto de la ejecución sin
# volver a golpearlo inmediatamente.
SESSION_DISABLE_SECONDS = 300

# Si un provider devuelve 429, NO hacemos retries (pasamos a fallback) para no
# quemar tokens/tiempo en la misma ejecución.
NO_RETRY_ON_429 = True

# “Circuit breaker” por ejecución: si un provider entra en cooldown una vez durante esta
# ejecución, se evita durante SESSION_DISABLE_SECONDS (no permanentemente — ver comentario junto a
# esa constante). Esto sigue siendo útil cuando varias fases (cls-json/ctx-json/docs/estimate)
# comparten el mismo provider upstream: evita machacarlo en los minutos siguientes al rate limit.
# Valor = timestamp de expiración (epoch), no un booleano.
SESSION_PROVIDER_DISABLED: Dict[str, float] = {}


# ==========================================================
# HELPERS PARA CADA PROVEEDOR
# ==========================================================

POCIT_INSECURE_SSL = os.getenv("POCIT_INSECURE_SSL", "0").strip() in ("1", "true", "True", "yes", "YES")


_TRUNCATED_FINISH_REASONS = {
    "length",
    "max_tokens",
}


def _requests_verify() -> bool:
    # Solo para diagnóstico / entornos controlados.
    # NO recomendable para producción: desactiva la verificación TLS.
    return not POCIT_INSECURE_SSL


def _extract_openai_compatible_content(
    data: Dict[str, Any],
    *,
    provider: str,
    model: Optional[str] = None,
) -> str:
    try:
        choice = data["choices"][0]
        message = choice["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(
            f"Respuesta inesperada de {provider}: faltan choices/message/content"
        ) from exc

    finish_reason = str(choice.get("finish_reason") or "").strip().lower()

    if content is None or not isinstance(content, str) or not content.strip():
        raise LLMError(
            f"{provider} devolvió contenido vacío"
            + (f" (model={model})" if model else "")
        )

    if finish_reason in _TRUNCATED_FINISH_REASONS:
        raise LLMTruncatedResponseError(
            provider=provider,
            model=model,
            finish_reason=finish_reason,
            partial_content=content,
        )

    return content


def _call_groq(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    **extra: Any,
) -> str:
    if not GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY no está configurada en el entorno")

    payload: Dict[str, Any] = {
        "model": model or DEFAULT_GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt in range(MAX_RETRIES):
        GLOBAL_BUCKET.consume(payload.get("max_tokens", 1000))
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60, verify=_requests_verify())

        if resp.status_code == 429:
            # Política: 429 => no reintentar en este provider, saltar al fallback
            logger.debug("[RATE LIMIT] Groq 429 detected.")
            if NO_RETRY_ON_429:
                raise LLMError("Groq 429 rate limit")
            logger.debug("[RATE LIMIT] Applying backoff...")
            exponential_backoff_sleep(attempt)
            continue

        handle_rate_limit_headers(resp)

        if resp.status_code != 200:
            raise LLMError(f"Error Groq {resp.status_code}: {resp.text}")

        break
    else:
        raise LLMError("Groq failed after retries (rate limit).")

    data = resp.json()
    return _extract_openai_compatible_content(
        data,
        provider="groq",
        model=payload["model"],
    )


def _extract_gemini_content(
    data: Dict[str, Any],
    *,
    model: Optional[str] = None,
) -> str:
    try:
        candidate = data["candidates"][0]
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict)
        )
    except (IndexError, TypeError, AttributeError, KeyError) as exc:
        raise LLMError("Respuesta Gemini inesperada") from exc

    finish_reason = str(
        candidate.get("finishReason")
        or candidate.get("finish_reason")
        or ""
    ).strip().upper()

    if not text.strip():
        raise LLMError("Gemini devolvió contenido vacío")

    if finish_reason in {"MAX_TOKENS", "LENGTH"}:
        raise LLMTruncatedResponseError(
            provider="gemini",
            model=model,
            finish_reason=finish_reason,
            partial_content=text,
        )

    return text


def _resolve_policy_phase(fase: Optional[str]) -> Optional[str]:
    if not fase:
        return None

    normalized = fase.strip()
    if normalized.startswith("generacion_codigo"):
        return "generacion_codigo"
    return normalized


def _resolve_effective_alias(
    *,
    fase: Optional[str],
    provider_hint: Optional[str],
) -> Optional[str]:
    if provider_hint and provider_hint.strip():
        return provider_hint.strip()

    resolved_phase = _resolve_policy_phase(fase)
    if not resolved_phase:
        return None

    return LLM_POLICY.get(resolved_phase)


def _dedupe_stable(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _resolve_direct_chain(
    *,
    fase: Optional[str],
    provider_hint: Optional[str],
) -> List[str]:
    effective_alias = _resolve_effective_alias(
        fase=fase,
        provider_hint=provider_hint,
    )
    return DIRECT_CHAIN_BY_ALIAS.get(
        effective_alias or "",
        DEFAULT_DIRECT_CHAIN,
    )


def _call_gemini(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    **extra: Any,
) -> str:
    if not GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY no está configurada en el entorno")

    url = f"{GEMINI_URL}/{model or GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

    # Convertimos formato OpenAI → Gemini
    contents = []
    for m in messages:
        role = "user" if m["role"] != "system" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens or 2048,
        },
    }

    resp = requests.post(url, json=payload, timeout=60, verify=_requests_verify())

    if resp.status_code == 429:
        raise LLMError("Gemini 429 rate limit")

    if resp.status_code != 200:
        raise LLMError(f"Error Gemini {resp.status_code}: {resp.text}")

    data = resp.json()
    return _extract_gemini_content(
        data,
        model=model or GEMINI_MODEL,
    )


def _call_mistral(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    **extra: Any,
) -> str:
    if not MISTRAL_API_KEY:
        raise LLMError("MISTRAL_API_KEY no está configurada en el entorno")

    payload: Dict[str, Any] = {
        "model": model or MISTRAL_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }

    resp = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=60, verify=_requests_verify())

    if resp.status_code == 429:
        raise LLMError("Mistral 429 rate limit")

    if resp.status_code != 200:
        raise LLMError(f"Error Mistral {resp.status_code}: {resp.text}")

    data = resp.json()
    return _extract_openai_compatible_content(
        data,
        provider="mistral",
        model=payload["model"],
    )


def _call_openai(*args: Any, **kwargs: Any) -> str:
    raise LLMError("OpenAI deshabilitado en este entorno (requiere crédito).")


def _call_cerebras(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    **extra: Any,
) -> str:
    if not CEREBRAS_API_KEY:
        raise LLMError("CEREBRAS_API_KEY no está configurada en el entorno")

    payload: Dict[str, Any] = {
        "model": model or CEREBRAS_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {CEREBRAS_API_KEY}",
        "Content-Type": "application/json",
    }

    resp = requests.post(CEREBRAS_URL, headers=headers, json=payload, timeout=60, verify=_requests_verify())

    if resp.status_code == 429:
        raise LLMError("Cerebras 429 rate limit")

    if resp.status_code != 200:
        raise LLMError(f"Error Cerebras {resp.status_code}: {resp.text}")

    data = resp.json()
    return _extract_openai_compatible_content(
        data,
        provider="cerebras",
        model=payload["model"],
    )


def _call_openrouter(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    **extra: Any,
) -> str:
    if not OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY no está configurada en el entorno")

    payload: Dict[str, Any] = {
        "model": model or DEFAULT_OPENROUTER_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # OpenRouter recomienda enviar el origen y nombre del modelo cliente
        "HTTP-Referer": "https://poc-it.local",
        "X-Title": "PoC-it",
    }

    for attempt in range(MAX_RETRIES):
        GLOBAL_BUCKET.consume(payload.get("max_tokens", 1000))
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60, verify=_requests_verify())

        if resp.status_code == 429:
            logger.debug("[RATE LIMIT] OpenRouter 429 detected.")
            if NO_RETRY_ON_429:
                raise LLMError("OpenRouter 429 rate limit")
            logger.debug("[RATE LIMIT] Applying backoff...")
            exponential_backoff_sleep(attempt)
            continue

        handle_rate_limit_headers(resp)

        if resp.status_code != 200:
            raise LLMError(f"Error OpenRouter {resp.status_code}: {resp.text}")

        break
    else:
        raise LLMError("OpenRouter failed after retries (rate limit).")

    data = resp.json()
    return _extract_openai_compatible_content(
        data,
        provider="openrouter",
        model=payload["model"],
    )


def _call_litellm_proxy(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    timeout: int = 70,
    **extra: Any,
) -> str:
    """
    Llama a LiteLLM Proxy (OpenAI-compatible) para centralizar proveedores/routing.

    Espera endpoint:
      POST {LITELLM_BASE_URL}/v1/chat/completions

    Body OpenAI:
      { "model": "<alias o provider/model>", "messages": [...], ... }

    Response OpenAI:
      { "choices": [ { "message": { "content": "..." } } ] }
    """
    payload: Dict[str, Any] = {
        "model": model or FALLBACK_PROVIDER,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {"Content-Type": "application/json"}
    if LITELLM_PROXY_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_PROXY_KEY}"

    # Reutilizamos tu bucket global como protección “cliente”
    for attempt in range(MAX_RETRIES):
        GLOBAL_BUCKET.consume(payload.get("max_tokens", 1000))
        try:
            resp = requests.post(
                LITELLM_CHAT_URL,
                headers=headers,
                json=payload,
                timeout=timeout,
                verify=_requests_verify(),
            )
        except requests.exceptions.ReadTimeout as exc:
            # Timeout hablando con el PROXY (no con el upstream directamente).
            # Para diagnosticar qué upstream está atascado, activa logs del proxy (ver README).
            alias = model or FALLBACK_PROVIDER
            logger.debug(
                "[TIMEOUT] LiteLLM Proxy ReadTimeout (alias=%s, timeout=%ss, url=%s). "
                "Esto suele indicar que el proxy está esperando respuesta de un upstream lento/bloqueado.",
                alias,
                timeout,
                LITELLM_CHAT_URL,
            )
            raise LLMError(f"LiteLLM Proxy timeout (alias={alias})") from exc
        except requests.exceptions.ConnectTimeout as exc:
            alias = model or FALLBACK_PROVIDER
            logger.debug(
                "[TIMEOUT] LiteLLM Proxy ConnectTimeout (alias=%s, timeout=%ss, url=%s). "
                "Esto suele indicar problema de red/host/puerto o saturación.",
                alias,
                timeout,
                LITELLM_CHAT_URL,
            )
            raise LLMError(f"LiteLLM Proxy connect-timeout (alias={alias})") from exc

        if resp.status_code == 429:
            logger.debug("[RATE LIMIT] LiteLLM Proxy 429 detected.")
            # El body suele incluir el upstream/provider real que rate-limitó.
            try:
                body = (resp.text or "")[:500]
            except Exception:
                body = ""
            if body:
                logger.debug("[RATE LIMIT] LiteLLM Proxy 429 body (trunc): %s", body)
            if NO_RETRY_ON_429:
                raise LLMError("LiteLLM Proxy 429 rate limit")
            logger.debug("[RATE LIMIT] Applying backoff...")
            exponential_backoff_sleep(attempt)
            continue

        handle_rate_limit_headers(resp)

        if resp.status_code != 200:
            # Muy útil para 400/404 del proxy indicando upstream/model inválido.
            # Caso especial: 402 (insufficient credits / max_tokens demasiado alto) -> tratamos como "no fatal"
            # para permitir fallback al siguiente alias/proveedor.
            try:
                body = (resp.text or "")[:2000]
            except Exception:
                body = ""

            if resp.status_code == 402:
                raise LLMError(f"LiteLLM Proxy 402 (insufficient credits / max_tokens too high): {body}")

            raise LLMError(f"Error LiteLLM Proxy {resp.status_code}: {body}")

        data = resp.json()
        return _extract_openai_compatible_content(
            data,
            provider="litellm_proxy",
            model=payload["model"],
        )

    raise LLMError("LiteLLM Proxy failed after retries (rate limit).")


def _call_ollama(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    **extra: Any,
) -> str:
    """
    Llamada local a Ollama usando la librería oficial `ollama`.
    Replica el comportamiento previo basado en ollama.chat().
    """
    try:
        import ollama
    except ImportError as exc:
        raise LLMError(
            "La librería 'ollama' no está instalada en el entorno."
        ) from exc

    try:
        response = ollama.chat(
            model=model or OLLAMA_MODEL,
            messages=messages,
            options={
                "temperature": temperature,
                "num_predict": max_tokens or 400,
            },
        )

        return response["message"]["content"]

    except Exception as exc:
        raise LLMError(f"Error Ollama local: {exc}") from exc


# ==========================================================
# INTERFAZ DE ALTO NIVEL
# ==========================================================

def _build_messages(user_prompt: str, system_prompt: Optional[str]) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return messages


def solicitarRespuestaTextual(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    provider_hint: Optional[str] = None,
    fase: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    """
    Obtiene una respuesta de texto libre del LLM.

    - Intenta primero con Groq.
    - Si falla y hay API key de OpenRouter, usa OpenRouter como fallback.
    """

    resolved_phase = _resolve_policy_phase(fase)
    effective_alias = _resolve_effective_alias(
        fase=fase,
        provider_hint=provider_hint,
    )

    proxy_aliases: List[Optional[str]] = []
    if resolved_phase:
        candidates = PHASE_ALIAS_FALLBACKS.get(resolved_phase, [])
        if effective_alias:
            if effective_alias in candidates:
                proxy_aliases = [effective_alias] + [c for c in candidates if c != effective_alias]
            else:
                proxy_aliases = [effective_alias] + candidates
        else:
            proxy_aliases = candidates
    else:
        proxy_aliases = [effective_alias] if effective_alias else []

    if not resolved_phase and effective_alias:
        inferred = {
            "docs": "documentacion",
            "ctx-json": "normalizacion_contexto",
            "cls-json": "clasificacion",
            "estimate": "estimacion",
            CODEGEN_ALIAS: "generacion_codigo",
        }.get(effective_alias)
        if inferred:
            resolved_phase = inferred
            candidates = PHASE_ALIAS_FALLBACKS.get(inferred, [])
            if candidates:
                proxy_aliases = [effective_alias] + [c for c in candidates if c != effective_alias]

    direct_chain = _resolve_direct_chain(
        fase=fase,
        provider_hint=provider_hint,
    )

    logger.debug(
        "[LLM-ROUTING] fase=%s resolved_phase=%s provider_hint=%s effective_alias=%s direct_chain=%s",
        fase,
        resolved_phase,
        provider_hint,
        effective_alias,
        direct_chain,
    )

    messages = _build_messages(prompt, system)

    last_error: Optional[Exception] = None

    # Captura el último error de CADA proveedor intentado (en orden), incluyendo litellm_proxy,
    # gemini, cerebras y mistral. Antes solo se guardaban groq/openrouter/ollama, así que si
    # fallaban únicamente proveedores fuera de esa lista (p.ej. el proxy local no estaba
    # levantado, o solo gemini/mistral tenían API key configurada), el mensaje final quedaba
    # vacío ("All providers failed." sin ningún detalle) y no había forma de diagnosticar la
    # causa real sin activar logging DEBUG.
    provider_errors: Dict[str, Exception] = {}

    # Cadena base de proveedores
    # - Política: primero LiteLLM Proxy (API Park).
    # - Si el proxy (incluyendo sus fallbacks internos) no consigue respuesta útil,
    #   activamos un fallback manual de ÚLTIMO RECURSO a proveedores directos.
    default_chain = ["litellm_proxy"]

    # IMPORTANTE:
    # - Fallback manual SOLO tras fallo del proxy (salvo LLM_DIRECT_ONLY).
    # - Si quieres desactivar completamente llamadas directas, exporta LITELLM_PROXY_ONLY=1.
    # - Si quieres saltarte el proxy y usar SOLO llamadas directas, exporta LLM_DIRECT_ONLY=1.
    if LLM_DIRECT_ONLY:
        providers = _dedupe_stable(direct_chain)
    elif LITELLM_PROXY_ONLY:
        providers = default_chain
    else:
        providers = _dedupe_stable(default_chain + direct_chain)

    LLM_METRICS["total_calls"] += 1

    for idx, provider in enumerate(providers):

        # -------------------------------
        # Circuit breaker: proveedor en cooldown
        # -------------------------------
        now = _time.time()
        if now < SESSION_PROVIDER_DISABLED.get(provider, 0):
            logger.debug("[LLM] %s deshabilitado temporalmente en esta ejecución. Saltando proveedor.", provider.upper())
            continue

        cooldown_until = PROVIDER_COOLDOWN.get(provider, 0)
        if now < cooldown_until:
            logger.debug("[LLM] %s en cooldown. Saltando proveedor.", provider.upper())
            continue

        try:
            if provider == "litellm_proxy":
                # provider_hint aquí es "model alias" del proxy (p.ej. code-gen).
                # Implementamos fallback por alias ANTES de saltar a proveedores directos.
                # IMPORTANTE: evitar bug de precedencia de operadores.
                # Queremos: si hay proxy_aliases úsalo, si no y hay provider_hint úsalo, si no [None]
                if proxy_aliases:
                    aliases_to_try = proxy_aliases
                elif provider_hint:
                    aliases_to_try = [provider_hint]
                else:
                    aliases_to_try = [None]

                # Safety-net: si el alias de grupo (p.ej. "docs") timeoutea de forma recurrente,
                # permitimos reintentar en el proxy con deployments concretos.
                # Esto mantiene al proxy en el centro, pero evita quedar bloqueados si el routing interno
                # no llega a ejecutar fallbacks antes de que el cliente corte.
                #
                # NOTA: sólo aplica a documentación, porque es donde más se observan timeouts.
                if resolved_phase == "documentacion":
                    extra_doc_aliases = ["docs-gemini", "docs-openrouter-2", "docs-groq"]
                    for a in extra_doc_aliases:
                        if a not in aliases_to_try:
                            aliases_to_try.append(a)

                if not aliases_to_try:
                    aliases_to_try = [None]

                proxy_last_exc: Optional[Exception] = None
                effective_timeout = timeout if isinstance(timeout, int) and timeout > 0 else 70
                for alias in aliases_to_try:
                    if not is_demo_mode():
                        logger.debug("[LLM] Provider: LITELLM_PROXY (model=%s)", alias or FALLBACK_PROVIDER)
                    alias_max_tokens = max_tokens
                    # Un truncamiento (`finish_reason=length`) significa que el modelo quería
                    # escribir más de lo que le dejamos: cambiar de alias con el MISMO presupuesto
                    # probablemente vuelva a truncar. Antes de descartar este alias, seguimos
                    # duplicando `max_tokens` (tope `_TRUNCATION_RETRY_MAX_TOKENS`) hasta que quepa
                    # o hasta agotar `_TRUNCATION_RETRY_MAX_ATTEMPTS` intentos — un único reintento
                    # no bastaba cuando la respuesta deseada era varias veces mayor que el
                    # presupuesto inicial (p.ej. un README de partida en max_tokens=1200).
                    for retry_attempt in range(_TRUNCATION_RETRY_MAX_ATTEMPTS):
                        try:
                            return _call_litellm_proxy(
                                messages,
                                model=alias or None,
                                temperature=temperature,
                                max_tokens=alias_max_tokens,
                                timeout=effective_timeout,
                            )
                        except LLMTruncatedResponseError as exc:
                            proxy_last_exc = exc
                            if not is_demo_mode():
                                logger.debug(
                                    "[LLM] Respuesta truncada en LITELLM_PROXY (model=%s, max_tokens=%s): %s",
                                    alias or FALLBACK_PROVIDER,
                                    alias_max_tokens,
                                    exc,
                                )
                            can_boost = (
                                retry_attempt < _TRUNCATION_RETRY_MAX_ATTEMPTS - 1
                                and isinstance(alias_max_tokens, int)
                                and alias_max_tokens > 0
                                and alias_max_tokens < _TRUNCATION_RETRY_MAX_TOKENS
                            )
                            if can_boost:
                                alias_max_tokens = min(
                                    alias_max_tokens * 2, _TRUNCATION_RETRY_MAX_TOKENS
                                )
                                if not is_demo_mode():
                                    logger.debug(
                                        "[LLM] Reintentando alias=%s con max_tokens=%s tras truncamiento.",
                                        alias or FALLBACK_PROVIDER,
                                        alias_max_tokens,
                                    )
                                continue
                            break
                        except Exception as exc:
                            proxy_last_exc = exc
                            # Si es rate limit/fallo, probamos el siguiente alias
                            if not is_demo_mode():
                                logger.debug(
                                    "[LLM] ERROR en LITELLM_PROXY (model=%s): %s",
                                    alias or FALLBACK_PROVIDER,
                                    exc,
                                )
                            break

                # Si todos los aliases fallan (p.ej. timeouts en docs), saltamos al siguiente proveedor
                # del chain (groq/cerebras/mistral/gemini/openrouter/ollama).
                raise proxy_last_exc or LLMError("LiteLLM Proxy failed for all aliases.")
            elif provider == "groq":
                if not is_demo_mode():
                    logger.debug("[LLM] Provider: GROQ")
                return _call_groq(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            elif provider == "cerebras":
                if not is_demo_mode():
                    logger.debug("[LLM] Provider: CEREBRAS")
                return _call_cerebras(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            elif provider == "mistral":
                if not is_demo_mode():
                    logger.debug("[LLM] Provider: MISTRAL")
                return _call_mistral(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            elif provider == "gemini":
                if not is_demo_mode():
                    logger.debug("[LLM] Provider: GEMINI")
                return _call_gemini(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            elif provider == "openrouter":
                if not is_demo_mode():
                    logger.debug("[LLM] Provider: OPENROUTER")
                return _call_openrouter(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            else:
                if not is_demo_mode():
                    logger.debug("[LLM] Provider: OLLAMA (local)")
                return _call_ollama(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

        except Exception as exc:
            if not is_demo_mode():
                logger.debug("[LLM] ERROR en %s: %s", provider.upper(), exc)

            # Si es error fuerte de rate limit, activar cooldown
            if "rate limit" in str(exc).lower() or "429" in str(exc):
                PROVIDER_COOLDOWN[provider] = _time.time() + COOLDOWN_SECONDS
                SESSION_PROVIDER_DISABLED[provider] = _time.time() + SESSION_DISABLE_SECONDS
                logger.debug(
                    "[LLM] %s desactivado durante %ss por rate limit (cooldown corto) y durante %ss "
                    "adicionales como circuit breaker de esta ejecución.",
                    provider.upper(),
                    COOLDOWN_SECONDS,
                    SESSION_DISABLE_SECONDS,
                )

            # Guardamos el error de ESTE proveedor sin importar cuál sea, para poder reportarlo
            # si al final todos fallan (ver construcción de error_msg más abajo).
            provider_errors[provider] = exc

            # Métricas de fallo por proveedor
            if provider == "litellm_proxy":
                # Si falla el proxy, DEBE saltar al siguiente proveedor del chain.
                pass
            elif provider == "groq":
                LLM_METRICS["groq_failures"] += 1
            elif provider == "openai":
                LLM_METRICS["openai_failures"] += 1
            elif provider == "cerebras":
                LLM_METRICS["cerebras_failures"] += 1
            elif provider == "mistral":
                LLM_METRICS["mistral_failures"] += 1
            elif provider == "gemini":
                LLM_METRICS["gemini_failures"] += 1
            elif provider == "openrouter":
                LLM_METRICS["openrouter_failures"] += 1
            elif provider == "ollama":
                LLM_METRICS["ollama_failures"] += 1
            else:
                # proveedor no reconocido (defensivo)
                pass

            # Si no es el último proveedor, cuenta como fallback
            if idx < len(providers) - 1:
                LLM_METRICS["fallbacks"] += 1
                if not is_demo_mode():
                    logger.debug("[LLM] → Activando fallback al siguiente proveedor...")

            continue

    error_msg = "All providers failed.\n"
    if provider_errors:
        for provider_name, provider_exc in provider_errors.items():
            error_msg += f"- {provider_name} error: {provider_exc}\n"
    else:
        # Ningún proveedor llegó siquiera a intentarse (p.ej. todos en cooldown/deshabilitados).
        error_msg += "- No provider was attempted (all skipped: cooldown/disabled).\n"

    if not is_demo_mode():
        logger.debug("[LLM METRICS]\n%s", "\n".join([f"  - {k}: {v}" for k, v in LLM_METRICS.items()]))

    raise LLMError(error_msg)


def solicitarJSONEstructurado(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    provider_hint: Optional[str] = None,
    fase: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    """
    Igual que chat_completion_text, pero reforzado para devolver JSON.

    Esta función NO parsea a dict, solo devuelve la cadena. El módulo
    llamador es responsable de hacer json.loads y gestionar errores.
    """

    # Refuerza instrucción de JSON en el system prompt
    system_prompt = (system or "") + "\nResponde exclusivamente con JSON válido."

    raw = solicitarRespuestaTextual(
        prompt=prompt,
        system=system_prompt.strip(),
        temperature=temperature,
        max_tokens=max_tokens,
        provider_hint=provider_hint,
        fase=fase,
        timeout=timeout,
    )

    # Blindaje contra respuestas None o no-string
    if not isinstance(raw, str):
        return "{}"

    raw_stripped = raw.strip()
    if raw_stripped.startswith("{") and raw_stripped.endswith("}"):
        return raw_stripped

    # Heurística simple: buscar primer "{" y último "}"
    try:
        start = raw.index("{")
        end = raw.rindex("}")
        candidate = raw[start : end + 1]
        json.loads(candidate)
        return candidate
    except Exception:
        return raw


# ==========================================================
# COMPATIBILIDAD (nombres antiguos)
# ==========================================================
# Mantener estos aliases evita tener que tocar imports existentes en el repo.
chat_completion_text = solicitarRespuestaTextual
chat_completion_json = solicitarJSONEstructurado
