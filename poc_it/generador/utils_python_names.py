from __future__ import annotations

import keyword
import re
from typing import Any


def safe_python_identifier(
    value: Any,
    *,
    fallback: str,
) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")

    if not raw:
        raw = fallback

    if raw[0].isdigit():
        raw = f"{fallback}_{raw}"

    if keyword.iskeyword(raw):
        raw = f"{raw}_value"

    return raw


def module_path_from_file_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()

    if not normalized.endswith(".py"):
        raise ValueError(f"Python module path requires .py file: {path}")

    module = normalized[:-3].replace("/", ".")

    if module.endswith(".__init__"):
        module = module[: -len(".__init__")]

    return module
