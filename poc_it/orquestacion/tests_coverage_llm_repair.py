from __future__ import annotations
"""
Loop de "coverage" para tests generados en modo PARCIAL (guiado por runtime_contracts).

Problema que resuelve
---------------------
En modo PARCIAL podemos tener evidencia dura de endpoints herméticos (runtime_contracts.endpoints),
pero el LLM puede devolver una suite incompleta (p.ej. solo smoke/openapi), o generar un fichero
`test_endpoints_hermetic.py` que no contiene tests para todos los endpoints seleccionados.

Este módulo NO crea tests de forma determinista. En su lugar:
- Valida presencia/cobertura mínima (heurística).
- Si no cumple, pide al LLM un patch SOLO sobre tests/ y pytest.ini para añadir/ajustar los tests faltantes.
- Repite hasta converger o agotar intentos.

La validación es deliberadamente simple para minimizar falsos negativos:
- Requiere `tests/test_endpoints_hermetic.py` si hay endpoints herméticos.
- Requiere que el contenido mencione (path, method) para cada endpoint, como señal de alineación.
"""

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from poc_it.generador.json_utils import extraer_json_tolerante
from poc_it.llm_client import chat_completion_json
from poc_it.materializador_archivos import materializar_proyecto
from poc_it.runtime_contracts import RUNTIME_CONTRACTS_PATH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoverageRepairResult:
    ok: bool
    attempts: int
    detail: str
    patched_files: Dict[str, str]


def _extract_endpoints(runtime_contracts: Optional[dict]) -> List[dict]:
    if not isinstance(runtime_contracts, dict):
        return []
    eps = runtime_contracts.get("endpoints") or []
    return [e for e in eps if isinstance(e, dict)] if isinstance(eps, list) else []


def _current_tests_from_structure(estructura: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p, c in (estructura or {}).items():
        if not isinstance(p, str):
            continue
        if p == "pytest.ini" or p.startswith("tests/"):
            out[p] = c if isinstance(c, str) else ""
    return out


def _validate_min_coverage(runtime_contracts: Optional[dict], current_tests: Dict[str, str]) -> Tuple[bool, str]:
    eps = _extract_endpoints(runtime_contracts)
    if not eps:
        return True, "No endpoints; coverage OK."

    hermetic_path = "tests/test_endpoints_hermetic.py"
    if hermetic_path not in current_tests or not (current_tests.get(hermetic_path) or "").strip():
        return False, f"Missing required {hermetic_path} for {len(eps)} endpoint(s)."

    content = current_tests.get(hermetic_path) or ""
    missing: List[str] = []
    for ep in eps:
        path = str(ep.get("path") or "").strip()
        method = str(ep.get("method") or "").upper().strip()
        if not path or not method:
            continue
        # Heurística mínima: el test debe mencionar path y method en el archivo
        if path not in content or method not in content:
            missing.append(f"{method} {path}")

    if missing:
        return False, "Missing endpoint coverage markers in test_endpoints_hermetic.py: " + ", ".join(missing)

    return True, "Coverage OK."


def _normalize_patch(files: object) -> Dict[str, str]:
    if not isinstance(files, list):
        return {}
    out: Dict[str, str] = {}
    for it in files:
        if not isinstance(it, dict):
            continue
        path = str(it.get("path") or "").replace("\\", "/").strip()
        content = it.get("content")
        if not path or not isinstance(content, str):
            continue
        if path != "pytest.ini" and not path.startswith("tests/"):
            continue
        out[path] = content
    return out


def _build_prompt_repair_missing_coverage(
    *,
    runtime_contracts: dict,
    current_tests: Dict[str, str],
    validation_error: str,
) -> str:
    files = [{"path": p, "content": c} for p, c in sorted(current_tests.items())]
    eps = _extract_endpoints(runtime_contracts)
    return f"""
TAREA
Faltan tests por endpoint en modo PARCIAL. Debes corregir/crear SOLO tests (tests/*.py) y/o pytest.ini
para que haya al menos 1 test ejecutable por cada endpoint listados en RUNTIME_CONTRACTS.endpoints.

CONTEXTO
- Estamos en modo PARCIAL: las integraciones externas NO están disponibles.
- Los tests deben ser 100% HERMÉTICOS: nada de red, credenciales, SDKs cloud, DB real.
- Solo puedes ejecutar endpoints si puedes aislar dependencias vía FastAPI dependency_overrides.
- Fuente de verdad para overrides: RUNTIME_CONTRACTS.endpoints[*].depends_imports y allowed_dependency_overrides.

VALIDATION ERROR
{validation_error}

ENDPOINTS A CUBRIR (RUNTIME_CONTRACTS.endpoints)
{json.dumps(eps, ensure_ascii=False)}

RUNTIME_CONTRACTS (completo)
{json.dumps(runtime_contracts, ensure_ascii=False)}

TESTS ACTUALES (JSON; archivos completos)
{json.dumps(files, ensure_ascii=False)}

REGLAS (OBLIGATORIAS)
- Devuelve SOLO archivos bajo tests/ y/o pytest.ini.
- Debes asegurar que existe `tests/test_endpoints_hermetic.py` y que contiene tests para TODOS los endpoints.
- Cada test debe mencionar explícitamente METHOD y PATH (como comentario o nombre) para facilitar validación.
- Usa `fastapi.testclient.TestClient` si tests_style es sync (ver runtime_contracts.tests_style si existe).
- Overrides:
  - Importa el callable real y úsalo como key: `app.dependency_overrides[callable] = override_fn`
  - override_fn debe devolver un stub con métodos async si el handler los await-ea.
- No importes SDKs externos (googleapiclient/google.oauth2/etc.) en tests.

SALIDA (JSON)
{{"files": [{{"path": "tests/test_endpoints_hermetic.py", "content": "..."}}, {{"path": "pytest.ini", "content": "..."}}]}}
""".strip()


def repair_tests_coverage_until_ok(
    *,
    nombre_proyecto: str,
    estructura: Dict[str, str],
    max_repairs: int = 2,
) -> CoverageRepairResult:
    # runtime_contracts desde estructura
    rc_raw = estructura.get(RUNTIME_CONTRACTS_PATH) or ""
    runtime_contracts = None
    if isinstance(rc_raw, str) and rc_raw.strip():
        try:
            runtime_contracts = json.loads(rc_raw)
        except Exception:
            runtime_contracts = None

    if not isinstance(runtime_contracts, dict):
        return CoverageRepairResult(ok=True, attempts=0, detail="No runtime_contracts; skip coverage loop.", patched_files={})

    current_tests = _current_tests_from_structure(estructura)
    patched_total: Dict[str, str] = {}
    last_detail = ""

    for attempt in range(max_repairs + 1):
        ok, msg = _validate_min_coverage(runtime_contracts, current_tests)
        last_detail = msg
        if ok:
            return CoverageRepairResult(ok=True, attempts=attempt, detail=msg, patched_files=patched_total)

        if attempt >= max_repairs:
            break

        prompt = _build_prompt_repair_missing_coverage(
            runtime_contracts=runtime_contracts,
            current_tests=current_tests,
            validation_error=msg,
        )
        raw = chat_completion_json(
            prompt=prompt,
            system=None,
            temperature=0.1,
            max_tokens=1800,
            provider_hint="code-gen",
            fase="generacion_codigo",
        )
        data = extraer_json_tolerante(raw) or {}
        patch = _normalize_patch(data.get("files"))
        if not patch:
            return CoverageRepairResult(ok=False, attempts=attempt + 1, detail=msg + " | LLM no devolvió patch", patched_files=patched_total)

        materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch, limpiar_directorio=False)
        estructura.update(patch)
        current_tests.update(patch)
        patched_total.update(patch)

    return CoverageRepairResult(ok=False, attempts=max_repairs, detail=last_detail, patched_files=patched_total)
