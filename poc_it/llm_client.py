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
import os
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from poc_it.rate_limiter import (
    GLOBAL_BUCKET,
    exponential_backoff_sleep,
    handle_rate_limit_headers,
    MAX_RETRIES,
)

# Carga automática del .env de la raíz del proyecto
load_dotenv()

# ==========================================================
# CONFIGURACIÓN BÁSICA
# ==========================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "codestral-latest")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "llama-3.3-70b")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Usamos API estable v1 (no v1beta)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1/models"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"

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

LLM_POLICY: Dict[str, str] = {
    "normalizacion_contexto": "gemini",
    "clasificacion": "gemini",
    "generacion_codigo": "mistral",   # ahora usamos Mistral para código
    "documentacion": "gemini",
    "estimacion": "gemini",
}

FALLBACK_PROVIDER = "gemini"

# ==========================================================
# CIRCUIT BREAKER SIMPLE POR PROVEEDOR
# ==========================================================

import time as _time

PROVIDER_COOLDOWN: Dict[str, float] = {}
COOLDOWN_SECONDS = 60  # desactiva proveedor 60s tras rate limit crítico


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
            print("[RATE LIMIT] Groq 429 detected. Applying backoff...")
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


def _call_openai(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    **extra: Any,
) -> str:
    if not OPENAI_API_KEY:
        raise LLMError("OPENAI_API_KEY no está configurada en el entorno")

    payload: Dict[str, Any] = {
        "model": model or OPENAI_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    resp = requests.post(OPENAI_URL, headers=headers, json=payload, timeout=60)

    if resp.status_code == 429:
        raise LLMError("OpenAI 429 rate limit")

    if resp.status_code != 200:
        raise LLMError(f"Error OpenAI {resp.status_code}: {resp.text}")

    data = resp.json()
    return data["choices"][0]["message"]["content"]


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
            print("[RATE LIMIT] OpenRouter 429 detected. Applying backoff...")
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


def chat_completion_text(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    provider_hint: Optional[str] = None,
) -> str:
    """
    Obtiene una respuesta de texto libre del LLM.

    - Intenta primero con Groq.
    - Si falla y hay API key de OpenRouter, usa OpenRouter como fallback.
    """

    messages = _build_messages(prompt, system)

    last_error: Optional[Exception] = None

    groq_error: Optional[Exception] = None
    openrouter_error: Optional[Exception] = None
    ollama_error: Optional[Exception] = None

    # Cadena base de proveedores
    default_chain = [
        "groq",
        "openai",
        "cerebras",
        "mistral",
        "gemini",
        "openrouter",
        "ollama",
    ]

    # Si se especifica provider_hint, se prioriza ese proveedor
    if provider_hint and provider_hint in default_chain:
        providers = [provider_hint] + [
            p for p in default_chain if p != provider_hint
        ]
    else:
        providers = default_chain

    LLM_METRICS["total_calls"] += 1

    for idx, provider in enumerate(providers):

        # -------------------------------
        # Circuit breaker: proveedor en cooldown
        # -------------------------------
        now = _time.time()
        cooldown_until = PROVIDER_COOLDOWN.get(provider, 0)
        if now < cooldown_until:
            print(f"[LLM] {provider.upper()} en cooldown. Saltando proveedor.")
            continue

        try:
            if provider == "groq":
                print("[LLM] Provider: GROQ")
                return _call_groq(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            elif provider == "openai":
                print("[LLM] Provider: OPENAI")
                return _call_openai(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            elif provider == "cerebras":
                print("[LLM] Provider: CEREBRAS")
                return _call_cerebras(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            elif provider == "mistral":
                print("[LLM] Provider: MISTRAL")
                return _call_mistral(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            elif provider == "gemini":
                print("[LLM] Provider: GEMINI")
                return _call_gemini(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            elif provider == "openrouter":
                print("[LLM] Provider: OPENROUTER")
                return _call_openrouter(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            else:
                print("[LLM] Provider: OLLAMA (local)")
                return _call_ollama(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

        except Exception as exc:
            print(f"[LLM] ERROR en {provider.upper()}: {exc}")

            # Si es error fuerte de rate limit, activar cooldown
            if "rate limit" in str(exc).lower() or "429" in str(exc):
                PROVIDER_COOLDOWN[provider] = _time.time() + COOLDOWN_SECONDS
                print(f"[LLM] {provider.upper()} desactivado durante {COOLDOWN_SECONDS}s por rate limit.")

            # Métricas de fallo por proveedor
            if provider == "groq":
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
            else:
                ollama_error = exc
                LLM_METRICS["ollama_failures"] += 1

            # Si no es el último proveedor, cuenta como fallback
            if idx < len(providers) - 1:
                LLM_METRICS["fallbacks"] += 1
                print(f"[LLM] → Activando fallback al siguiente proveedor...")

            continue

    error_msg = "All providers failed.\n"
    if groq_error:
        error_msg += f"- Groq error: {groq_error}\n"
    if openrouter_error:
        error_msg += f"- OpenRouter error: {openrouter_error}\n"
    if ollama_error:
        error_msg += f"- Ollama error: {ollama_error}\n"

    print("\n[LLM METRICS]")
    for k, v in LLM_METRICS.items():
        print(f"  - {k}: {v}")

    raise LLMError(error_msg)


def chat_completion_json(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    provider_hint: Optional[str] = None,
) -> str:
    """
    Igual que chat_completion_text, pero reforzado para devolver JSON.

    Esta función NO parsea a dict, solo devuelve la cadena. El módulo
    llamador es responsable de hacer json.loads y gestionar errores.
    """

    # Refuerza instrucción de JSON en el system prompt
    system_prompt = (system or "") + "\nResponde exclusivamente con JSON válido."

    raw = chat_completion_text(
        prompt=prompt,
        system=system_prompt.strip(),
        temperature=temperature,
        max_tokens=max_tokens,
        provider_hint=provider_hint,
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
