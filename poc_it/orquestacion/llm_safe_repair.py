from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from poc_it.generador.json_utils import extraer_json_tolerante
from poc_it.llm_client import chat_completion_json

# ============================================
# LLM SAFE REPAIR (PIPELINE A - SUBSECTION)
# ============================================
#
# Objetivo:
# - Reparar código con LLM sin introducir nuevos errores.
# - Sin fixers deterministas.
# - Con gates deterministas + rollback:
#   - AST/compile (in-memory)
#   - import-time verify (external: reparacion_runtime.py)
#   - wiring verify (external: reparacion_runtime.py)
#
# Estrategia:
# - 2 fases: DIAGNOSE (plan) -> PATCH (código)
# - allowlist: solo archivos implicados por el traceback (y/o repair_paths explícitos)
#
# Integración:
# - Este módulo NO ejecuta runtime_verify; devuelve un patch candidato (o None) y logs.
# - reparacion_runtime.py aplica el patch, materializa, y ejecuta gates externos.
#


@dataclass(frozen=True)
class SafeRepairConfig:
    enabled: bool = False
    max_attempts: int = 2
    max_files_to_change: int = 6
    max_chars_per_file: int = 20000
    temperature_diag: float = 0.0
    temperature_patch: float = 0.1


@dataclass
class SafeRepairAttemptLog:
    attempt: int
    stage: str  # diagnose | patch | gate
    ok: bool
    detail: str


@dataclass
class SafeRepairResult:
    ok: bool
    patch: Dict[str, str]
    logs: List[SafeRepairAttemptLog]
    allowlist: List[str]


_TRACE_FILE_RE = re.compile(r'File "([^"]+\.py)"')


def _extract_paths_from_traceback(detail: str) -> List[str]:
    if not detail:
        return []
    out: List[str] = []
    for m in _TRACE_FILE_RE.finditer(detail):
        p = m.group(1)
        if not p:
            continue
        out.append(p)
    # de-dup preserving order
    seen: Set[str] = set()
    uniq: List[str] = []
    for p in out:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return uniq


def _to_repo_rel(path: str, project_dir: str) -> Optional[str]:
    """
    Convierte rutas absolutas del traceback a rutas relativas del proyecto
    tipo app/main.py, app/endpoints/x.py, etc.
    """
    if not path:
        return None
    norm = path.replace("\\", "/")
    proj = project_dir.replace("\\", "/").rstrip("/")
    if norm.startswith(proj + "/"):
        norm = norm[len(proj) + 1 :]
    # recortar a partir de app/
    idx = norm.find("app/")
    if idx >= 0:
        return norm[idx:]
    # permitir archivos top-level comunes
    if norm.endswith("requirements.txt"):
        return "requirements.txt"
    if norm.endswith("main.py") and "app/" not in norm:
        # no asumimos; devolvemos None
        return None
    return None


def _build_allowlist(*, runtime_detail: str, project_dir: str, extra_allow: Optional[Sequence[str]] = None) -> List[str]:
    tb_paths = _extract_paths_from_traceback(runtime_detail)
    rels: List[str] = []
    for p in tb_paths:
        r = _to_repo_rel(p, project_dir)
        if r:
            rels.append(r)

    if extra_allow:
        for p in extra_allow:
            if isinstance(p, str) and p.strip():
                rels.append(p.replace("\\", "/"))

    # de-dup + cap
    seen: Set[str] = set()
    out: List[str] = []
    for p in rels:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _subset_files(estructura: Dict[str, str], allowlist: Sequence[str], max_chars_per_file: int) -> List[Dict[str, str]]:
    files: List[Dict[str, str]] = []
    for p in allowlist:
        c = estructura.get(p)
        if not isinstance(c, str) or not c.strip():
            continue
        if len(c) > max_chars_per_file:
            c = c[:max_chars_per_file] + "\n# [TRUNCATED]\n"
        files.append({"path": p, "content": c})
    return files


def _in_memory_ast_ok(patch: Dict[str, str]) -> Tuple[bool, str]:
    """
    Gate mínimo: asegurar que los .py del patch parsean AST.
    """
    for p, c in (patch or {}).items():
        if not isinstance(p, str) or not isinstance(c, str):
            continue
        if not p.endswith(".py"):
            continue
        try:
            ast.parse(c)
        except SyntaxError as e:
            return False, f"AST parse failed for {p}: {e}"
        except Exception as e:
            return False, f"AST parse error for {p}: {e}"
    return True, "OK"


def _render_diag_prompt(
    *,
    spec: Optional[dict],
    runtime_detail: str,
    guardrails_warnings: Optional[List[str]],
    allowlist: Sequence[str],
    files_subset: List[Dict[str, str]],
) -> str:
    return f"""
Eres un senior engineer. Necesito diagnosticar por qué un proyecto FastAPI generado NO es importable / falla en runtime_verify.
NO debes generar código todavía. Solo un plan de reparación.

INPUTS:
- runtime_detail (traceback / verificación):
{runtime_detail}

- guardrails_warnings:
{json.dumps(guardrails_warnings or [], ensure_ascii=False, indent=2)}

- allowlist (únicos archivos que se pueden tocar después):
{json.dumps(list(allowlist), ensure_ascii=False, indent=2)}

- spec (si existe):
{json.dumps(spec or {}, ensure_ascii=False, indent=2)[:6000]}

- files (contenido actual, solo subset allowlist):
{json.dumps(files_subset, ensure_ascii=False, indent=2)[:12000]}

SALIDA OBLIGATORIA (JSON puro):
{{
  "root_causes": ["... (max 3)"],
  "files_to_change": ["path1.py", "path2.py"],
  "patch_plan": {{
    "path1.py": ["cambio 1", "cambio 2"],
    "path2.py": ["..."]
  }},
  "risk": "low|med|high"
}}

REGLAS:
- files_to_change debe ser SUBCONJUNTO de allowlist.
- No inventes archivos. No propongas refactors.
- Prioriza: (1) que importe, (2) wiring básico, (3) luego funcionalidad.
""".strip()


def _render_patch_prompt(
    *,
    spec: Optional[dict],
    runtime_detail: str,
    allowlist: Sequence[str],
    files_subset: List[Dict[str, str]],
    diag: dict,
    previous_rejected_patch: Optional[Dict[str, str]] = None,
    gate_errors: Optional[List[str]] = None,
) -> str:
    return f"""
Eres un senior engineer. Genera un PATCH mínimo para corregir el fallo de import/runtime.

Contexto:
- runtime_detail:
{runtime_detail}

- allowlist (solo puedes modificar estos archivos):
{json.dumps(list(allowlist), ensure_ascii=False, indent=2)}

- diag (plan aprobado):
{json.dumps(diag or {}, ensure_ascii=False, indent=2)[:6000]}

- spec:
{json.dumps(spec or {}, ensure_ascii=False, indent=2)[:6000]}

- files actuales (subset allowlist):
{json.dumps(files_subset, ensure_ascii=False, indent=2)[:14000]}

- patch rechazado previo (si existe):
{json.dumps(previous_rejected_patch or {}, ensure_ascii=False, indent=2)[:6000]}

- errores de gates (si existe):
{json.dumps(gate_errors or [], ensure_ascii=False, indent=2)}

SALIDA OBLIGATORIA (JSON puro):
{{
  "files": [
    {{"path": "path1.py", "content": "..." }},
    ...
  ]
}}

REGLAS DURAS:
- Solo paths dentro de allowlist.
- Modifica el mínimo posible. No reescribas archivos completos si no hace falta.
- No añadas dependencias nuevas salvo si el traceback es ModuleNotFoundError (y SOLO en requirements.txt).
- Mantén Pydantic v2 (ConfigDict/from_attributes); no uses orm_mode ni from_orm.
- Prohibido @lru_cache sobre async def.
""".strip()


def run_llm_safe_repair(
    *,
    config: SafeRepairConfig,
    project_dir: str,
    spec: Optional[dict],
    estructura: Dict[str, str],
    runtime_detail: str,
    guardrails_warnings: Optional[List[str]] = None,
    extra_allow_paths: Optional[Sequence[str]] = None,
) -> SafeRepairResult:
    logs: List[SafeRepairAttemptLog] = []

    if not config.enabled:
        return SafeRepairResult(ok=False, patch={}, logs=[], allowlist=[])

    allowlist = _build_allowlist(
        runtime_detail=runtime_detail,
        project_dir=project_dir,
        extra_allow=extra_allow_paths,
    )[: config.max_files_to_change]

    if not allowlist:
        return SafeRepairResult(
            ok=False,
            patch={},
            logs=[SafeRepairAttemptLog(0, "diagnose", False, "allowlist vacío (no paths extraíbles del traceback).")],
            allowlist=[],
        )

    files_subset = _subset_files(estructura, allowlist, config.max_chars_per_file)

    previous_rejected_patch: Optional[Dict[str, str]] = None
    last_gate_errors: List[str] = []

    for attempt in range(1, max(1, config.max_attempts) + 1):
        # 1) DIAGNOSE
        diag_prompt = _render_diag_prompt(
            spec=spec,
            runtime_detail=runtime_detail,
            guardrails_warnings=guardrails_warnings,
            allowlist=allowlist,
            files_subset=files_subset,
        )
        raw_diag = chat_completion_json(
            prompt=diag_prompt,
            system=None,
            temperature=config.temperature_diag,
            max_tokens=900,
            fase="code-gen",
        )
        diag = extraer_json_tolerante(raw_diag) or {}
        files_to_change = diag.get("files_to_change")

        if not isinstance(files_to_change, list) or not files_to_change:
            logs.append(SafeRepairAttemptLog(attempt, "diagnose", False, "diag sin files_to_change"))
            continue

        files_to_change_norm = [str(p).replace("\\", "/") for p in files_to_change if isinstance(p, (str,))]
        files_to_change_norm = [p for p in files_to_change_norm if p in set(allowlist)]
        if not files_to_change_norm:
            logs.append(SafeRepairAttemptLog(attempt, "diagnose", False, "diag propone archivos fuera de allowlist"))
            continue

        # 2) PATCH
        patch_allowlist = files_to_change_norm  # restringir aún más
        patch_subset = _subset_files(estructura, patch_allowlist, config.max_chars_per_file)

        patch_prompt = _render_patch_prompt(
            spec=spec,
            runtime_detail=runtime_detail,
            allowlist=patch_allowlist,
            files_subset=patch_subset,
            diag=diag,
            previous_rejected_patch=previous_rejected_patch,
            gate_errors=last_gate_errors,
        )
        raw_patch = chat_completion_json(
            prompt=patch_prompt,
            system=None,
            temperature=config.temperature_patch,
            max_tokens=2000,
            fase="code-gen",
        )
        data = extraer_json_tolerante(raw_patch) or {}
        files = data.get("files")
        if not isinstance(files, list) or not files:
            logs.append(SafeRepairAttemptLog(attempt, "patch", False, "patch sin files[]"))
            continue

        patch: Dict[str, str] = {}
        for f in files:
            if not isinstance(f, dict):
                continue
            p = (f.get("path") or "").replace("\\", "/")
            c = f.get("content")
            if not p or not isinstance(c, str) or not c.strip():
                continue
            if p not in set(patch_allowlist):
                continue
            patch[p] = c

        if not patch:
            logs.append(SafeRepairAttemptLog(attempt, "patch", False, "patch vacío tras filtrar allowlist"))
            continue

        # Gate mínimo local: AST en los .py del patch
        ok_ast, ast_detail = _in_memory_ast_ok(patch)
        if not ok_ast:
            previous_rejected_patch = patch
            last_gate_errors = [ast_detail]
            logs.append(SafeRepairAttemptLog(attempt, "gate", False, ast_detail))
            continue

        logs.append(SafeRepairAttemptLog(attempt, "patch", True, f"patch candidate ok (files={list(patch.keys())})"))
        return SafeRepairResult(ok=True, patch=patch, logs=logs, allowlist=list(allowlist))

    return SafeRepairResult(ok=False, patch={}, logs=logs, allowlist=list(allowlist))
