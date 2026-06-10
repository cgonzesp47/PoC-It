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

from poc_it.demo_progress import is_demo_mode
from poc_it.rate_limiter import (
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
# A petición del proyecto: por defecto SIEMPRE usamos el proxy (API Park / LiteLLM Proxy).
# Esto garantiza que el routing/fallback centralizado se aplique y evita llamadas directas a proveedores.
# Por defecto: permitimos fallback manual como último recurso (tras fallo del proxy).
# Si quremos que NUNCA se hagan llamadas directas, exporta LITELLM_PROXY_ONLY=1.
LITELLM_PROXY_ONLY = os.getenv("LITELLM_PROXY_ONLY", "0").strip() in ("1", "true", "True", "yes", "YES")

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
LLM_POLICY: Dict[str, str] = {
    "normalizacion_contexto": "ctx-json",
    "clasificacion": "cls-json",
    "generacion_codigo": "code-gen",
    "documentacion": "docs",
    "estimacion": "estimate",
}

# Si no hay hint, este alias suele ser un buen “generalista” barato
FALLBACK_PROVIDER = "docs"

# ==========================================================
# CIRCUIT BREAKER SIMPLE POR PROVEEDOR
# ==========================================================

import time as _time

PROVIDER_COOLDOWN: Dict[str, float] = {}
COOLDOWN_SECONDS = 60  # desactiva proveedor 60s tras rate limit crítico

# Si un provider devuelve 429, NO hacemos retries (pasamos a fallback) para no
# quemar tokens/tiempo en la misma ejecución.
NO_RETRY_ON_429 = True

# “Circuit breaker” por ejecución: si un provider entra en cooldown una vez durante
# esta ejecución, se evita en el resto de llamadas del proceso (además del cooldown temporal).
# Esto es útil cuando varias fases (cls-json/ctx-json/docs/estimate) comparten el mismo provider upstream.
SESSION_PROVIDER_DISABLED: Dict[str, bool] = {}


# ==========================================================
# HELPERS PARA CADA PROVEEDOR
# ==========================================================

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
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)

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
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as exc:  # pragma: no cover - fallback defensivo
        raise LLMError(f"Respuesta Groq inesperada: {data}") from exc


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

    resp = requests.post(url, json=payload, timeout=60)

    if resp.status_code == 429:
        raise LLMError("Gemini 429 rate limit")

    if resp.status_code != 200:
        raise LLMError(f"Error Gemini {resp.status_code}: {resp.text}")

    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as exc:
        raise LLMError(f"Respuesta Gemini inesperada: {data}") from exc


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

    resp = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=60)

    if resp.status_code == 429:
        raise LLMError("Mistral 429 rate limit")

    if resp.status_code != 200:
        raise LLMError(f"Error Mistral {resp.status_code}: {resp.text}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMError(f"Respuesta Mistral inesperada: {data}") from exc


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

    resp = requests.post(CEREBRAS_URL, headers=headers, json=payload, timeout=60)

    if resp.status_code == 429:
        raise LLMError("Cerebras 429 rate limit")

    if resp.status_code != 200:
        raise LLMError(f"Error Cerebras {resp.status_code}: {resp.text}")

    data = resp.json()
    return data["choices"][0]["message"]["content"]


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
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)

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
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as exc:  # pragma: no cover - fallback defensivo
        raise LLMError(f"Respuesta OpenRouter inesperada: {data}") from exc


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
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LLMError(f"Respuesta LiteLLM Proxy inesperada: {data}") from exc

        # Si el proxy responde 200 pero el content viene vacío/None, lo tratamos como fallo
        # para permitir fallback (suele pasar si el upstream devolvió respuesta sin texto).
        if content is None or (isinstance(content, str) and not content.strip()):
            alias = model or FALLBACK_PROVIDER
            raise LLMError(f"LiteLLM Proxy empty content (alias={alias})")

        return content

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

    # Si el caller nos pasa una fase, resolvemos el alias del proxy por política multi-modelo.
    # Esto evita que todo caiga en el alias FALLBACK_PROVIDER="docs".
    if fase and not provider_hint:
        provider_hint = LLM_POLICY.get(fase)

    # ----------------------------------------------------------
    # Fallbacks por fase (punto medio):
    #
    # Queremos que el PROXY haga el routing/fallback principal (por model group alias),
    # y mantener un fallback manual a proveedores directos solo como último recurso.
    #
    # Por eso aquí SOLO probamos el alias de GRUPO (ctx-json/cls-json/docs/estimate/code-gen)
    # dentro del proxy, dejando que `router_settings.fallbacks` del proxy elija
    # docs-gemini/docs-cerebras/docs-groq, etc.
    #
    # Esto mejora trazabilidad (un solo alias por fase) y evita solapar el fallback del proxy
    # con el fallback manual del cliente.
    # ----------------------------------------------------------
    PHASE_ALIAS_FALLBACKS: Dict[str, List[str]] = {
        "normalizacion_contexto": ["ctx-json"],
        "clasificacion": ["cls-json"],
        "documentacion": ["docs"],
        "estimacion": ["estimate"],
        "generacion_codigo": ["code-gen"],
    }

    # Si estamos usando el proxy, construimos una lista de aliases a intentar
    # en el propio proxy (reintentos "horizontales" por alias).
    proxy_aliases: List[Optional[str]] = []
    if fase:
        candidates = PHASE_ALIAS_FALLBACKS.get(fase, [])
        if provider_hint:
            # Prioriza el alias pedido (si es un alias)
            if provider_hint in candidates:
                proxy_aliases = [provider_hint] + [c for c in candidates if c != provider_hint]
            else:
                proxy_aliases = [provider_hint] + candidates
        else:
            proxy_aliases = candidates
    else:
        proxy_aliases = [provider_hint] if provider_hint else []

    # Si no se pasan fase/candidates pero se pasa provider_hint con un alias "principal",
    # inferimos fase para aplicar el set completo de fallbacks de ese grupo.
    if not fase and provider_hint:
        inferred = {
            "docs": "documentacion",
            "ctx-json": "normalizacion_contexto",
            "cls-json": "clasificacion",
            "estimate": "estimacion",
            "code-gen": "generacion_codigo",
        }.get(provider_hint)
        if inferred:
            candidates = PHASE_ALIAS_FALLBACKS.get(inferred, [])
            if candidates:
                proxy_aliases = [provider_hint] + [c for c in candidates if c != provider_hint]
                fase = inferred

    messages = _build_messages(prompt, system)

    last_error: Optional[Exception] = None

    groq_error: Optional[Exception] = None
    openrouter_error: Optional[Exception] = None
    ollama_error: Optional[Exception] = None

    # Cadena base de proveedores
    # - Política: primero LiteLLM Proxy (API Park).
    # - Si el proxy (incluyendo sus fallbacks internos) no consigue respuesta útil,
    #   activamos un fallback manual de ÚLTIMO RECURSO a proveedores directos.
    default_chain = ["litellm_proxy"]

    # IMPORTANTE:
    # - Fallback manual SOLO tras fallo del proxy.
    # - Si quieres desactivar completamente llamadas directas, exporta LITELLM_PROXY_ONLY=1.
    if LITELLM_PROXY_ONLY:
        providers = default_chain
    else:
        providers = default_chain + ["groq", "gemini", "mistral", "cerebras", "openrouter", "ollama"]

    LLM_METRICS["total_calls"] += 1

    for idx, provider in enumerate(providers):

        # -------------------------------
        # Circuit breaker: proveedor en cooldown
        # -------------------------------
        now = _time.time()
        if SESSION_PROVIDER_DISABLED.get(provider):
            logger.debug("[LLM] %s deshabilitado en esta ejecución. Saltando proveedor.", provider.upper())
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
                if fase == "documentacion":
                    extra_doc_aliases = ["docs-gemini", "docs-cerebras", "docs-groq"]
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
                    try:
                        return _call_litellm_proxy(
                            messages,
                            model=alias or None,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            timeout=effective_timeout,
                        )
                    except Exception as exc:
                        proxy_last_exc = exc
                        # Si es rate limit/fallo, probamos el siguiente alias
                        if not is_demo_mode():
                            logger.debug(
                                "[LLM] ERROR en LITELLM_PROXY (model=%s): %s",
                                alias or FALLBACK_PROVIDER,
                                exc,
                            )
                        continue

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
                SESSION_PROVIDER_DISABLED[provider] = True
                logger.debug(
                    "[LLM] %s desactivado durante %ss por rate limit y deshabilitado para el resto de esta ejecución.",
                    provider.upper(),
                    COOLDOWN_SECONDS,
                )

            # Métricas de fallo por proveedor
            if provider == "litellm_proxy":
                # Contabilizamos fallo del proxy como "fallback" potencial y dejamos evidencia.
                # Si falla el proxy, DEBE saltar al siguiente proveedor del chain.
                pass
            elif provider == "groq":
                groq_error = exc
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
                openrouter_error = exc
                LLM_METRICS["openrouter_failures"] += 1
            elif provider == "ollama":
                ollama_error = exc
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
    if groq_error:
        error_msg += f"- Groq error: {groq_error}\n"
    if openrouter_error:
        error_msg += f"- OpenRouter error: {openrouter_error}\n"
    if ollama_error:
        error_msg += f"- Ollama error: {ollama_error}\n"

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
