"""
PoC-it – Post-procesador de alineación (modo LLM-only)

Objetivo
--------
Aplicar un micro-parche posterior a la generación, usando SOLO el LLM (code-gen),
para alinear el proyecto materializado con su intención y corregir errores de runtime/tests/docs.

Principios de seguridad
-----------------------
- Parche mínimo: solo modificar archivos explícitamente listados.
- No reescrituras masivas: pedir cambios quirúrgicos.
- Validación posterior se hace fuera (en el orquestador): import-time + pytest si existe.

Entrada/salida
--------------
Opera sobre `estructura: Dict[path, content]` (paths POSIX dentro del output, p.ej. app/main.py)
y devuelve un patch {path: content} con solo los archivos modificados.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from poc_it.llm_client import chat_completion_json
from poc_it.poc_facts_extractor import extract_poc_facts_from_structure


# ----------------------------
# Modelos
# ----------------------------

@dataclass
class AlignmentIssue:
    """
    Issue detectada por un validador previo (runtime_verify, pytest, o heurística externa).
    En modo LLM-only no hacemos detección interna: el orquestador la pasa.
    """

    code: str
    severity: str  # "info" | "warn" | "error"
    file: str
    message: str
    hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "file": self.file,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass
class PostprocessResult:
    patched_files: Dict[str, str]
    llm_raw: str


# ----------------------------
# Utilidades
# ----------------------------

def _pick_files_to_patch(issues: List[AlignmentIssue], max_files: int) -> List[str]:
    """
    Selecciona la whitelist de ficheros que el LLM puede modificar.

    Regla base:
      - usar `issue.file` (normalizado a POSIX)

    Mejora clave para robustez con pytest:
      - si el issue viene de pytest con `file="tests"` pero el traceback apunta a ficheros reales
        del proyecto (p.ej. `app/services/drive_service.py:12`), añadimos también esos paths.
      - así el modelo puede parchear el fichero que realmente falla, sin abrir demasiado el scope.
    """
    files: List[str] = []

    def _add(path: str) -> None:
        p = (path or "").replace("\\", "/").strip()
        if not p:
            return
        if p not in files:
            files.append(p)

    # 1) Paths explícitos por issue.file
    for iss in issues:
        _add(iss.file)
        if len(files) >= max_files:
            return files[:max_files]

    # 2) Heurística: extraer paths desde hint/traceback (pytest)
    # Patrones típicos:
    # - "app\\services\\drive_service.py:12: NameError"
    # - "app/services/drive_service.py:12: NameError"
    # - 'File "....\\app\\services\\drive_service.py", line 12'
    import re

    candidates: List[str] = []
    for iss in issues:
        hint = (iss.hint or "").replace("\\", "/")
        if not hint:
            continue

        # a) paths tipo app/...py:line
        for m in re.finditer(r"\b(app/[^\s:]+\.py):\d+\b", hint):
            candidates.append(m.group(1))

        # b) paths dentro de 'File "..."' -> recortamos a partir de /app/
        for m in re.finditer(r'File "([^"]+\.py)"', hint):
            full = m.group(1).replace("\\", "/")
            idx = full.rfind("/app/")
            if idx != -1:
                candidates.append(full[idx + 1 :])  # quita el leading '/'

    # Orden estable + dedupe conservando orden
    for p in candidates:
        _add(p)
        if len(files) >= max_files:
            break

    return files[:max_files]


def _build_files_context(estructura: Dict[str, str], files: List[str], max_chars: int = 12000) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for f in files:
        c = estructura.get(f, "")
        if isinstance(c, str) and c:
            out.append({"path": f, "content": c[:max_chars]})
    return out


# ----------------------------
# API principal
# ----------------------------

def postprocesar_alineacion_llm(
    *,
    estructura: dict[str, str],
    spec: dict | None,
    issues: list[AlignmentIssue],
    runtime_contracts: dict | None = None,
    max_files: int = 6,
    repair_goal: str = "tests",
    strategy_hint: str = "GENERAL",
) -> PostprocessResult:
    """
    Ejecuta post-procesado SOLO via LLM.

    Parámetros:
    - estructura: ficheros del proyecto ya generados (path->content)
    - spec: spec dict si existe (fuente de intención)
    - issues: lista de problemas detectados por validadores (tracebacks, fallos pytest, checks contractuales)
    - max_files: máximo de ficheros que puede tocar el modelo (control de blast radius)

    Devuelve:
    - patched_files: dict con SOLO los ficheros modificados
    - llm_raw: respuesta raw del modelo (debug/auditoría)
    """
    facts = extract_poc_facts_from_structure(estructura).to_dict()

    rg = (repair_goal or "auto").strip().lower()

    # En modo TEST_REPAIR forzamos allowlist estricta para evitar que el LLM “escape” a app/**
    # cuando el traceback menciona ficheros de app (p.ej. Settings/DATABASE_URL missing).
    # Esto es clave para convergencia: si estamos reparando tests, solo se puede tocar tests/**.
    if rg == "tests":
        files_to_patch = []
        for p in estructura.keys():
            pp = (p or "").replace("\\", "/")
            if pp.startswith("tests/") or pp == "conftest.py":
                files_to_patch.append(pp)
        # Fallback conservador: si no hay tests, al menos tocar el propio issue.file
        if not files_to_patch:
            files_to_patch = _pick_files_to_patch(issues, max_files=max_files)
        else:
            files_to_patch = files_to_patch[: max(1, max_files)]
    else:
        files_to_patch = _pick_files_to_patch(issues, max_files=max_files)

    files_ctx = _build_files_context(estructura, files_to_patch)

    goal_rules = ""
    if rg == "code":
        goal_rules = """
OBJETIVO DE REPARACIÓN: CODE_REPAIR
- Prioriza arreglar el código de app/ para eliminar excepciones (NameError/AttributeError/imports).
- NO cambies tests salvo que el error esté en tests y sea estrictamente necesario.
- NO inventes integraciones externas: si falta DB/credenciales, maneja el caso de forma controlada (errores claros) y mantén import-time estable.
"""
    elif rg == "tests":
        goal_rules = """
OBJETIVO DE REPARACIÓN: TEST_REPAIR
- Prioriza arreglar SOLO tests (tests/**, conftest.py) para que sean coherentes con el código existente.
- NO modifiques app/** salvo que sea imprescindible para permitir import-time (y solo cambios triviales de wiring: imports/logger).
- Los tests deben ser herméticos: sin red, sin DB real. Usa dependency overrides/mocks coherentes con las firmas reales (AsyncSession vs dict).
- NO cambies la semántica de la app: adapta asserts/payloads/status codes a lo que el código realmente implementa y a la SPEC.
"""
    else:
        goal_rules = """
OBJETIVO DE REPARACIÓN: AUTO
- Si el traceback apunta a app/** con excepciones, arregla código.
- Si lo que falla son asserts/status codes/contract mismatch, arregla tests.
"""

    prompt = f"""
TAREA
Eres un post-procesador de alineación de un proyecto FastAPI generado por IA.
Debes corregir SOLO los errores reportados, con el CAMBIO MÍNIMO posible.

ESTRATEGIA (HINT; APLICAR SI ES COMPATIBLE CON LOS ISSUES)
- strategy_hint = {strategy_hint}
- BOOTSTRAP_HERMETIC:
  - Haz los tests herméticos: sin DB/red/credenciales reales.
  - Prefiere introducir/ajustar tests/conftest.py (dependency_overrides, monkeypatch.setenv).
- STATEFUL_DOUBLES:
  - Si los tests realizan POST->GET/PUT/DELETE, usa stubs stateful (misma instancia en fixture).
  - Evita doubles incompatibles con Depends (p.ej. dict como db/session).
- CONTRACT_ALIGNMENT:
  - Alinea asserts/status codes/body shape con lo que realmente devuelve el código.
  - Relaja asserts demasiado estrictos en modo PARCIAL.
- CLEANUP_FINAL:
  - Minimiza cambios; evita reescrituras grandes.
  - Si hay estancamiento, prefiere degradar tests a smoke/openapi en vez de inventar wiring.

{goal_rules}

FUENTES DE VERDAD
1) SPEC (intención del usuario): úsalo para NO romper requisitos.
2) FACTS (extraído del código): úsalo para NO inventar símbolos.

SPEC (JSON; puede ser null)
{json.dumps(spec, ensure_ascii=False)}

RUNTIME_CONTRACTS (JSON; puede ser null)
- Si es no-null, úsalo como fuente de verdad para:
  - métodos/paths reales existentes (evitar 404/405 falsos)
  - required/optional fields reales por endpoint (evitar expectativas 422 incorrectas)
  - not_found_http_status y returns_none_on_not_found (ej: PUT {{}} puede ser 404 si resource no existe)
  - dependency_overrides permitidos (allowed_dependency_overrides) y estilo de tests (sync/async)
{json.dumps(runtime_contracts, ensure_ascii=False) if runtime_contracts is not None else "null"}

FACTS (JSON)
{json.dumps(facts, ensure_ascii=False)}

ISSUES (JSON)
{json.dumps([i.to_dict() for i in issues], ensure_ascii=False)}

ARCHIVOS DISPONIBLES PARA MODIFICAR (NO TOCAR OTROS)
{json.dumps([f["path"] for f in files_ctx], ensure_ascii=False)}

CONTENIDO DE ARCHIVOS (solo estos)
{json.dumps(files_ctx, ensure_ascii=False)}

REGLAS ESTRICTAS
- No modifiques archivos fuera de la lista.
- No cambies la arquitectura ni añadas dependencias sin necesidad.
- No reescribas archivos completos si no es imprescindible; cambios quirúrgicos.
- Corrige CUALQUIER error residual que cause:
  - errores de import-time,
  - errores de ejecución típicos (NameError por imports faltantes, símbolos inexistentes),
  - fallos de tests (pytest),
  - incoherencias contrato API vs tests vs modelos (422/200),
  - incoherencias documentación vs código.
- Prohibido “inventar” símbolos. Si propones usar `X`, debe existir en FACTS o debes crear `X` en el mismo patch.
- Para integraciones externas (Drive, DB, etc.):
  - Nunca debe romper el import-time.
  - Preferir lazy init (dentro de funciones) y manejar ausencia de credenciales con error controlado.
- Si un endpoint tiene un request model con campos requeridos, los tests deben enviar esos campos, o el endpoint debe definir defaults coherentes con el SPEC.
- Si hay un fallo que no se puede resolver sin credenciales reales, el código debe seguir siendo importable y los tests deben mockear la dependencia correctamente.

MODO DE TRABAJO (OBLIGATORIO)
1) Lee ISSUES y localiza el archivo y línea aproximada (traceback/hint).
2) Usa FACTS para validar nombres reales de módulos/símbolos/paths.
3) Aplica el cambio mínimo que haga pasar la verificación:
   - import-time
   - y/o ejecución de la función afectada
   - y/o tests
4) Devuelve SOLO los ficheros que realmente cambias.

SALIDA
Devuelve SOLO JSON con el formato:
{{
  "files": [
    {{"path": "app/services/drive_service.py", "content": "..." }}
  ]
}}
"""

    llm_raw = chat_completion_json(
        prompt=prompt,
        system=None,
        temperature=0.1,
        max_tokens=2500,
        provider_hint="code-gen",
        fase="generacion_codigo",
    )

    data = json.loads(llm_raw)
    patched: Dict[str, str] = {}
    for f in data.get("files", []) or []:
        if not isinstance(f, dict):
            continue
        p = str(f.get("path") or "").replace("\\", "/")
        c = f.get("content")
        if p in files_to_patch and isinstance(c, str) and c.strip():
            patched[p] = c

    return PostprocessResult(patched_files=patched, llm_raw=llm_raw)


def issues_from_runtime_verify(detail: str) -> List[AlignmentIssue]:
    """
    Helper opcional: convertir el detail de _runtime_verify_fastapi_project en una issue genérica.
    (Seguimos siendo LLM-only: no inferimos fix, solo empaquetamos el error.)
    """
    return [
        AlignmentIssue(
            code="RUNTIME_IMPORT_FAILURE",
            severity="error",
            file="__unknown__",
            message="El proyecto no es importable (import-time). Ver detalle.",
            hint=detail,
        )
    ]
