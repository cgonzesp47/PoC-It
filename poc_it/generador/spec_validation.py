from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Set, Any, Optional

from poc_it.llm_client import chat_completion_json


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


def alinear_spec_con_contexto(
    spec: dict,
    contexto_normalizado: dict,
    intentos: int = 1,
    provider_hint: str = "docs",
    max_tokens: int = 1400,
) -> Tuple[Optional[dict], List[str]]:
    """
    Fase 1.5: Alineación de SPEC con ContextoNormalizado (fuente de verdad del usuario).

    Objetivo:
    - Detectar y corregir inconsistencias de alto nivel SIN hardcodear guardrails por PoC.
    - Evitar SPECs que cumplen formato pero contradicen explícitamente la plantilla
      (endpoints, request type, restricciones, integraciones, etc.).

    Estrategia:
    - 1) Precheck determinista (local): si existe `contexto_normalizado.contratos_api`,
         cualquier mismatch con `spec.endpoints` se considera MUST error y fuerza repair.
         Esto evita depender del auditor LLM cuando hay timeouts/429 o salida no parseable.
    - 2) LLM auditor devuelve JSON con:
      - ok: bool
      - errors: [string]
      - patched_spec: object|null (si puede devolver el SPEC corregido)

    Si patched_spec es parseable, lo devolvemos; si no, devolvemos el SPEC actual + errores
    para alimentar el repair patch-style.
    """
    if not isinstance(spec, dict) or not isinstance(contexto_normalizado, dict):
        return None, ["spec/contexto_normalizado inválidos para alineación"]

    # ----------------------------------------------------------
    # PRECHECK determinista: contratos_api vs spec.endpoints
    # ----------------------------------------------------------
    contratos_api = contexto_normalizado.get("contratos_api") if isinstance(contexto_normalizado, dict) else None
    if isinstance(contratos_api, list) and contratos_api:
        spec_endpoints = spec.get("endpoints")
        if not isinstance(spec_endpoints, list):
            print("[DEBUG] Precheck contratos_api vs spec.endpoints: spec.endpoints no es lista; forzando repair.")
            return spec, ["MUST: spec.endpoints debe ser lista para compararse con contratos_api"]

        def _k(method: str, path: str) -> Tuple[str, str]:
            return (str(method or "").upper().strip(), str(path or "").strip())

        spec_by_key: Dict[Tuple[str, str], dict] = {}
        for ep in spec_endpoints:
            if not isinstance(ep, dict):
                continue
            kk = _k(ep.get("method"), ep.get("path"))
            if kk[0] and kk[1]:
                spec_by_key[kk] = ep

        errors_contract: List[str] = []

        for c in contratos_api:
            if not isinstance(c, dict):
                continue
            ck = _k(c.get("method"), c.get("path"))
            if not ck[0] or not ck[1]:
                continue

            ep = spec_by_key.get(ck)
            if not isinstance(ep, dict):
                errors_contract.append(f"MUST: falta endpoint en SPEC para contrato_api {ck[0]} {ck[1]}")
                continue

            c_req = c.get("request") if isinstance(c.get("request"), dict) else {}
            e_req = ep.get("request") if isinstance(ep.get("request"), dict) else {}
            c_type = str((c_req or {}).get("type") or "").strip()
            e_type = str((e_req or {}).get("type") or "").strip()

            if c_type and e_type and c_type != e_type:
                errors_contract.append(
                    f"MUST: mismatch request.type en {ck[0]} {ck[1]} (contrato_api='{c_type}' vs spec='{e_type}')"
                )
            elif c_type and not e_type:
                errors_contract.append(f"MUST: spec.request.type vacío en {ck[0]} {ck[1]} (contrato_api='{c_type}')")

            c_resp = c.get("response") if isinstance(c.get("response"), dict) else {}
            e_resp = ep.get("response") if isinstance(ep.get("response"), dict) else {}
            if isinstance(c_resp, dict) and "json_example" in c_resp:
                if not (isinstance(e_resp, dict) and "json_example" in e_resp):
                    errors_contract.append(f"MUST: falta response.json_example en SPEC para {ck[0]} {ck[1]}")
                else:
                    if c_resp.get("json_example") != e_resp.get("json_example"):
                        errors_contract.append(
                            f"MUST: mismatch response.json_example en {ck[0]} {ck[1]} (SPEC debe seguir contratos_api)"
                        )

        contract_keys = {
            _k(c.get("method"), c.get("path"))
            for c in contratos_api
            if isinstance(c, dict) and c.get("method") and c.get("path")
        }
        extras = sorted([f"{m} {p}" for (m, p) in spec_by_key.keys() if (m, p) not in contract_keys])
        if extras:
            errors_contract.append("MUST: spec contiene endpoints no listados en contratos_api: " + ", ".join(extras))

        if errors_contract:
            print("[DEBUG] Precheck contratos_api vs spec.endpoints: mismatch detectado; forzando repair.")
            return spec, errors_contract

    auditor_compact = os.getenv("AUDITOR_COMPACT", "0").strip() in ("1", "true", "True", "yes", "YES")

    compact_rules = ""
    if auditor_compact:
        compact_rules = """
MODO COMPACTO (ANTI-TRUNCADO) - OBLIGATORIO
- NO devuelvas `patched_spec` completo si su tamaño puede ser grande o incluye listas largas (files/endpoints/contracts/restrictions).
- Si hay inconsistencias, devuelve:
  - ok=false
  - errors: lista accionable de cambios
  - patched_spec: null
- Solo puedes devolver `patched_spec` si es MUY pequeño (p.ej. un cambio menor de 1-2 campos) y estás seguro de no truncarte.
"""

    prompt = f"""
TAREA
Eres un auditor de consistencia. Compara el CONTEXTO_NORMALIZADO (fuente de verdad del usuario) con el SPEC (plan de generación).
Si el SPEC contradice el contexto, corrígelo.

{compact_rules}

CONTEXTO_NORMALIZADO (FUENTE DE VERDAD):
{json.dumps(contexto_normalizado, ensure_ascii=False)}

SPEC A AUDITAR:
{json.dumps(spec, ensure_ascii=False)}

CHECKLIST 
- Funcionalidades explícitas: cualquier funcionalidad descrita en el contexto debe estar representada en el SPEC (como endpoint/contrato/archivo/regla), sin omisiones silenciosas.
- Contratos de entrada/salida: si el contexto describe un tipo de entrada (JSON, multipart, query, none) o ejemplos, el SPEC debe reflejarlo en `endpoints[].request` / `endpoints[].response`.
- Restricciones técnicas: el SPEC no debe exigir ni asumir nada contrario al contexto (p.ej. credenciales embebidas si se pide “sin credenciales en código”).
- Integraciones externas: el SPEC no debe inventar integraciones ajenas; debe incluir las integraciones mencionadas si impactan dependencias/env/contratos.
- Archivos/rutas: el SPEC debe ser coherente internamente:
  - `endpoints[].file` debe existir en `files`
  - `bundle_files` (si existe) debe estar incluido en `files`
  - no debe haber rutas fuera de `app/` para Python

SALIDA (EXCLUSIVAMENTE JSON válido)
{{
  "ok": true,
  "errors": [],
  "patched_spec": null
}}

REGLAS DE SALIDA
- Devuelve SOLO JSON.
- Si detectas problemas:
  - ok=false
  - errors: lista de strings concisos y accionables
  - patched_spec:
    - En modo compacto: null (salvo patch MUY pequeño)
    - Si no compacto: devuelve el SPEC completo corregido si puedes; si no, null
- No uses Markdown.
""".strip()

    last_errors: List[str] = []
    last_raw: str = ""

    for _ in range(max(1, intentos)):
        raw = chat_completion_json(
            prompt=prompt,
            system=None,
            temperature=0.0,
            max_tokens=max_tokens,
            provider_hint=provider_hint,
            fase="documentacion",
        )
        last_raw = raw or ""
        data = _extraer_json_tolerante(raw)
        if not isinstance(data, dict):
            last_errors = ["auditor: salida no parseable"]
            continue

        ok = bool(data.get("ok"))
        errors = data.get("errors") if isinstance(data.get("errors"), list) else []
        patched = data.get("patched_spec")

        if ok:
            return spec, []

        last_errors = [str(e) for e in errors if str(e).strip()] or ["auditor: inconsistencias detectadas"]

        if isinstance(patched, dict):
            return patched, last_errors

        # ok=false + patched_spec=null (modo compacto): devolvemos spec + errores accionables
        return spec, last_errors

    # Persistimos RAW para diagnóstico si todo fue no-parseable
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (debug_dir / f"audit_raw_{ts}.txt").write_text(last_raw or "", encoding="utf-8")
    except Exception:
        pass

    return None, last_errors
