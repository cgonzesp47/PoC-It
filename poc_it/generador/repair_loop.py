from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple

from poc_it.generador.json_utils import extraer_json_tolerante

# Patch común: lista de {"path": "...", "content": "..."}
PatchItem = Dict[str, str]
Patch = List[PatchItem]

ChatCompletionJson = Callable[..., str]


def normalizar_path(path: str) -> str:
    return (path or "").replace("\\", "/").strip()


def normalizar_patch(patch: Any) -> Patch:
    """
    Normaliza la salida del modelo a Patch (lista de dicts con path+content no vacíos).
    """
    if not isinstance(patch, list):
        return []
    out: Patch = []
    for item in patch:
        if not isinstance(item, dict):
            continue
        p = normalizar_path(str(item.get("path") or ""))
        c = str(item.get("content") or "")
        if not p or not c.strip():
            continue
        out.append({"path": p, "content": c})
    return out


def aplicar_patch_en_memoria(files: List[Dict[str, str]], patch_files: Patch) -> None:
    """
    Aplica patch sobre `files` (in-place). Conservador:
    - solo sobrescribe si content no está vacío
    """
    if not patch_files:
        return
    idx = {
        normalizar_path(f.get("path") or ""): i
        for i, f in enumerate(files)
        if isinstance(f, dict)
    }
    for it in patch_files:
        p = normalizar_path(it.get("path") or "")
        c = it.get("content") or ""
        if not p or not c.strip():
            continue
        if p in idx:
            files[idx[p]]["content"] = c
        else:
            files.append({"path": p, "content": c})


def extraer_repair_paths_de_errores_imports(errores: Sequence[str]) -> List[str]:
    """
    Extrae paths implicados en errores de imports internos.

    Soporta:
    - "<path>: ..."
    - "... (app/x/y.py)"
    """
    repair_paths: List[str] = []
    for err in errores or []:
        s = str(err)
        if ":" in s:
            p = normalizar_path(s.split(":", 1)[0])
            if p and p not in repair_paths:
                repair_paths.append(p)
        m = re.search(r"\\((app\\/[\\^)]+\\.py)\\)", s)
        if m:
            target_path = normalizar_path(m.group(1))
            if target_path and target_path not in repair_paths:
                repair_paths.append(target_path)
    return repair_paths


def aplicar_repair_loop_imports(
    *,
    spec: dict,
    files_generados: List[Dict[str, str]],
    allowed_paths: Iterable[str],
    errores_imports: Sequence[str],
    chat_completion_json: ChatCompletionJson,
    intentos: int = 2,
) -> Tuple[bool, List[str]]:
    """
    Repair loop para imports internos:
    - Pide al LLM un patch sobre `repair_paths` derivados de errores.
    - Aplica patch sobre `files_generados` en memoria.
    - El caller se encarga de revalidar.

    Devuelve: (patch_aplicado_ok, repair_paths)
    """
    repair_paths = extraer_repair_paths_de_errores_imports(errores_imports)
    if not repair_paths:
        return False, []

    prompt_fix_imports = f"""
Hay errores de imports internos (coherencia entre módulos) que impiden ejecutar el proyecto.
Corrige SOLO estos archivos y ninguno más.

Errores:
- {chr(10).join(list(errores_imports))}

Archivos a corregir (paths exactos):
{json.dumps(repair_paths, ensure_ascii=False)}

SPEC (fuente de verdad):
{json.dumps(spec, ensure_ascii=False)}

SALIDA (JSON):
{{ "files": [{{"path":"...", "content":"..."}}] }}

REGLAS:
- Devuelve SOLO los archivos listados.
- No inventes nuevos paths.
- Si un archivo hace `from app.x.y import SIMBOLO`, entonces SIMBOLO debe existir realmente en el módulo importado.
- Si la dependencia importada NO existe en spec.files, elimina ese import y reestructura el código para no necesitarla.
- Mantén los endpoints exactamente como en el SPEC (mismos paths y métodos).
- Si hay try/except: incluye logging obligatorio con logger.exception().

REGLA ANTI-OSCILACIÓN (OBLIGATORIA)
- Objetivo: converger en 1 iteración, evitando “renombrados” que crean nuevos símbolos rotos.
- Si falta un símbolo importado (from-import inválido), prioriza SIEMPRE:
  1) AÑADIR el símbolo faltante en el módulo importado (target), aunque sea como wrapper delegando a una función existente.
  2) Solo si es imposible crear wrapper (por firma/arquitectura), entonces cambia el import y ajusta llamadas,
     pero en ese caso debes actualizar TODOS los módulos afectados en este mismo parche.
- Prohibido introducir nuevos nombres arbitrarios si no están en el SPEC.
""".strip()

    raw = chat_completion_json(
        prompt=prompt_fix_imports,
        system=None,
        temperature=0.1,
        max_tokens=1600,
        fase="generacion_codigo",
    )

    data = extraer_json_tolerante(raw) or {}
    patch = normalizar_patch(data.get("files"))
    if not patch:
        return False, repair_paths

    allowed = {normalizar_path(p) for p in allowed_paths}
    patch = [it for it in patch if normalizar_path(it["path"]) in allowed]

    aplicar_patch_en_memoria(files_generados, patch)
    return True, repair_paths
