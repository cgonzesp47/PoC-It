from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

from .models import CodegenIssue, GeneratedFile

LOGGER = logging.getLogger(__name__)


def _debug_safe_path_token(path: str) -> str:
    return re.sub(
        r"[^a-zA-Z0-9._-]+",
        "_",
        (path or "unknown").replace("/", "_").replace("\\", "_"),
    )


def dump_codegen_raw_for_debug(*, path: str, intento: int, raw: str) -> None:
    try:
        safe = _debug_safe_path_token(path)
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"codegen_raw_{safe}_attempt_{intento}.txt").write_text(
            raw or "",
            encoding="utf-8",
        )
    except Exception as exc:
        LOGGER.warning("No se pudo persistir RAW de codegen para %s: %s", path, exc)


def dump_codegen_contract_errors(*, path: str, issues: Iterable[CodegenIssue]) -> None:
    try:
        safe = _debug_safe_path_token(path)
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "path": path,
            "issues": [
                issue.to_dict()
                for issue in issues
            ],
        }
        (debug_dir / f"codegen_contract_errors_{safe}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        LOGGER.warning(
            "No se pudo persistir errores de codegen para %s: %s",
            path,
            exc,
        )


def dump_codegen_attempt_metadata(*, path: str, intento: int, payload: dict) -> None:
    try:
        safe = _debug_safe_path_token(path)
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"codegen_llm_attempt_{safe}_{intento}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        LOGGER.warning(
            "No se pudo persistir metadata de intento de codegen para %s: %s",
            path,
            exc,
        )


def dump_codegen_file_accepted(*, generated_file: GeneratedFile) -> None:
    try:
        safe = _debug_safe_path_token(generated_file.path)
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "path": generated_file.path,
            "content": generated_file.content,
        }
        (debug_dir / f"codegen_file_{safe}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        LOGGER.warning(
            "No se pudo persistir archivo aceptado de codegen para %s: %s",
            generated_file.path,
            exc,
        )


def _debug_candidate_filename(path: str, attempt: int, *, suffix: str = ".py") -> str:
    normalized = (
        str(path or "unknown")
        .replace("\\", "_")
        .replace("/", "_")
        .replace(".", "_")
    )
    return f"codegen_candidate_{normalized}_attempt_{attempt}{suffix}"


def dump_codegen_candidate_for_debug(
    *,
    path: str,
    attempt: int,
    content: str,
    suffix: str = ".py",
) -> str | None:
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        filename = _debug_candidate_filename(path, attempt, suffix=suffix)
        (debug_dir / filename).write_text(
            content or "",
            encoding="utf-8",
        )
        return filename
    except Exception as exc:
        LOGGER.warning(
            "No se pudo persistir candidato de codegen para %s: %s",
            path,
            exc,
        )
        return None


def dump_codegen_candidate_metadata(
    *,
    path: str,
    attempt: int,
    payload: dict,
) -> str | None:
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        filename = _debug_candidate_filename(path, attempt, suffix=".json")
        (debug_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return filename
    except Exception as exc:
        LOGGER.warning(
            "No se pudo persistir metadata de candidato de codegen para %s: %s",
            path,
            exc,
        )
        return None
