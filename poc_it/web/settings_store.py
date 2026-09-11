"""
Lectura/escritura de `.env` para la pantalla de Ajustes de la interfaz web.

No se cargan los valores reales de vuelta al frontend: los campos marcados
como `secret` se devuelven enmascarados (solo se indica si están definidos).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from dotenv import dotenv_values, set_key

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

# (key, etiqueta, grupo, es_secreto)
FIELDS: List[tuple] = [
    ("GROQ_API_KEY", "Groq API key", "llm", True),
    ("GEMINI_API_KEY", "Gemini API key", "llm", True),
    ("MISTRAL_API_KEY", "Mistral API key", "llm", True),
    ("CEREBRAS_API_KEY", "Cerebras API key", "llm", True),
    ("OPENROUTER_API_KEY", "OpenRouter API key", "llm", True),
    ("LITELLM_BASE_URL", "URL del proxy LiteLLM", "llm", False),
    ("LITELLM_PROXY_KEY", "Clave del proxy LiteLLM (opcional)", "llm", True),
    ("POCIT_GITLAB_URL", "URL de la instancia GitLab", "gitlab", False),
    ("POCIT_GITLAB_GROUP", "Grupo/subgrupo GitLab (full_path)", "gitlab", False),
    ("POCIT_GITLAB_GROUP_ID", "ID del grupo GitLab (opcional)", "gitlab", False),
    ("POCIT_GITLAB_TOKEN", "Token de acceso GitLab", "gitlab", True),
]


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"


def get_settings() -> List[Dict[str, Any]]:
    current = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    out = []
    for key, label, group, secret in FIELDS:
        raw = (current.get(key) or "").strip()
        out.append(
            {
                "key": key,
                "label": label,
                "group": group,
                "secret": secret,
                "is_set": bool(raw),
                "value": None if secret else raw,
                "masked": _mask(raw) if secret and raw else None,
            }
        )
    return out


def update_settings(values: Dict[str, str]) -> List[Dict[str, Any]]:
    valid_keys = {f[0] for f in FIELDS}
    if not ENV_PATH.exists():
        ENV_PATH.touch()

    for key, value in values.items():
        if key not in valid_keys:
            continue
        # Cadena vacía = "no tocar este valor" (evita pisar un secreto ya
        # guardado solo porque el frontend lo mostró vacío/enmascarado).
        if value == "" or value is None:
            continue
        set_key(str(ENV_PATH), key, value, quote_mode="always")

    return get_settings()
