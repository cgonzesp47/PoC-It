from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Set, Any, Optional

import re


def _extraer_json_tolerante(respuesta: str) -> Optional[dict]:
    """
    Intenta parsear JSON de forma tolerante.

    Casos soportados:
    - JSON limpio
    - Texto extra antes/después del JSON
    - Respuestas con bloques Markdown (```json ... ```)
    - Respuestas con múltiples bloques: extrae el primer {...} que parezca JSON

    Nota:
    - Si el JSON está truncado y NO hay cierre '}', no se puede recuperar aquí.
    - Si el JSON está truncado pero contiene al menos una '}' final de algún objeto,
      intentamos extraer el mayor bloque {...} posible.
    """
    if not isinstance(respuesta, str) or "{" not in respuesta:
        return None

    s = respuesta.strip()

    # 1) strip de fences Markdown si existen
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL | re.IGNORECASE)
        if m:
            s = m.group(1).strip()

    # 2) intento directo
    try:
        return json.loads(s)
    except Exception:
        pass

    # 3) fallback "greedy": del primer '{' al último '}' (si existe)
    try:
        start = s.index("{")
        end = s.rindex("}")
        candidate = s[start : end + 1]
        return json.loads(candidate)
    except Exception:
        return None

def normalizar_paths(files: List[Dict[str, str]]) -> List[str]:
    return [f.get("path", "").replace("\\", "/") for f in files if f.get("path")]


def carpetas_de_codigo(py_paths: List[str]) -> Set[str]:
    carpetas: Set[str] = set()
    for p in py_paths:
        if "/" in p:
            carpetas.add(p.rsplit("/", 1)[0])
    return carpetas


def completar_inits_en_files(files: List[str]) -> List[str]:
    """
    Asegura que toda carpeta que contenga un .py tenga su __init__.py declarado.
    Esto evita que el SPEC falle por olvidos mecánicos del modelo.
    """
    norm_files = [str(p).replace("\\", "/") for p in files]
    extra: Set[str] = set()

    py_paths = [p for p in norm_files if p.endswith(".py") and p.startswith("app/")]
    for carpeta in carpetas_de_codigo(py_paths):
        init_path = f"{carpeta}/__init__.py"
        if init_path not in norm_files:
            extra.add(init_path)

    # Orden estable: originales primero, luego añadidos (para trazabilidad)
    return norm_files + sorted(extra)


def validar_spec(spec: dict) -> Tuple[bool, List[str]]:
    errores: List[str] = []

    if not isinstance(spec, dict):
        return False, ["SPEC no es un objeto JSON"]

    entrypoint = spec.get("entrypoint")
    run_command = spec.get("run_command")
    imports_policy = spec.get("imports_policy")
    files = spec.get("files")
    endpoints = spec.get("endpoints", [])

    if entrypoint != "app.main:app":
        errores.append("SPEC.entrypoint debe ser exactamente 'app.main:app'")

    if run_command != "uvicorn app.main:app --reload":
        errores.append("SPEC.run_command debe ser exactamente 'uvicorn app.main:app --reload'")

    if imports_policy not in ("absolute_from_app", None):
        errores.append("SPEC.imports_policy debe ser 'absolute_from_app'")

    if not isinstance(files, list) or not files:
        errores.append("SPEC.files debe ser una lista no vacía")

    # Validación de paths y mínimos
    if isinstance(files, list):
        norm_files = [str(p).replace("\\", "/") for p in files]

        if "app/main.py" not in norm_files:
            errores.append("SPEC.files debe incluir 'app/main.py'")

        # Solo permitimos .py bajo app/ ; docs/requirements en raíz
        for p in norm_files:
            if p.endswith(".py") and not p.startswith("app/"):
                errores.append(f"Archivo Python fuera de app/: {p}")
            if p.startswith("/"):
                errores.append(f"Path inválido (no relativo): {p}")

        # NOTA: __init__.py no se valida aquí, porque se completa automáticamente
        # para tolerar olvidos mecánicos del modelo.

        # endpoints[*].file debe estar en files
        if isinstance(endpoints, list):
            for ep in endpoints:
                if isinstance(ep, dict) and "file" in ep:
                    ep_file = str(ep["file"]).replace("\\", "/")
                    if ep_file not in norm_files:
                        errores.append(
                            f"Endpoint referencia archivo no incluido en files: {ep_file}"
                        )

    return (len(errores) == 0), errores


def persistir_spec_debug(
    nombre_archivo: str,
    spec: dict,
    descripcion_global: str,
    contexto_normalizado: dict | None,
) -> None:
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_path = debug_dir / nombre_archivo
        payload: Dict[str, Any] = {
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "descripcion_global": descripcion_global,
            "contexto_normalizado": contexto_normalizado,
            "spec": spec,
        }
        debug_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[DEBUG] SPEC persistido en: {debug_path.as_posix()}")
    except Exception as e:
        print(f"[DEBUG] No se pudo persistir SPEC: {e}")
