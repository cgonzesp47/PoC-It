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

# Carga automática del .env de la raíz del proyecto
load_dotenv()

# ==========================================================
# CONFIGURACIÓN BÁSICA
# ==========================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Ollama (fallback local mediante librería oficial)
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen7b:latest")

# Modelos por defecto (puedes ajustarlos según tus preferencias)
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_OPENROUTER_MODEL = OPENROUTER_MODEL


class LLMError(Exception):
    """Error genérico de llamadas LLM."""


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

    resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
    if resp.status_code != 200:
        raise LLMError(f"Error Groq {resp.status_code}: {resp.text}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as exc:  # pragma: no cover - fallback defensivo
        raise LLMError(f"Respuesta Groq inesperada: {data}") from exc


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

    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
    if resp.status_code != 200:
        raise LLMError(f"Error OpenRouter {resp.status_code}: {resp.text}")

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
    prefer: str = "groq",
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

    # Orden fijo: Groq → OpenRouter → Ollama
    providers = ["groq", "openrouter", "ollama"]

    for provider in providers:
        try:
            if provider == "groq":
                return _call_groq(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            elif provider == "openrouter":
                return _call_openrouter(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            else:
                return _call_ollama(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
        except Exception as exc:
            if provider == "groq":
                groq_error = exc
            elif provider == "openrouter":
                openrouter_error = exc
            else:
                ollama_error = exc
            continue

    error_msg = "All providers failed.\n"
    if groq_error:
        error_msg += f"- Groq error: {groq_error}\n"
    if openrouter_error:
        error_msg += f"- OpenRouter error: {openrouter_error}\n"
    if ollama_error:
        error_msg += f"- Ollama error: {ollama_error}\n"

    raise LLMError(error_msg)


def chat_completion_json(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    prefer: str = "groq",
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
        prefer=prefer,
    )

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
