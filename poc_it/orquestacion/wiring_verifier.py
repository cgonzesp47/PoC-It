from __future__ import annotations
"""
Verificación determinista de wiring FastAPI (pre-tests).

Objetivo
--------
Complementar `runtime_verify_fastapi_project` (import-time) con una comprobación de
"wiring mínimo" antes de generar/ejecutar tests:

- El proyecto debe exponer OpenAPI (`/openapi.json`) sin disparar integraciones externas.
- Los paths/methods detectados en `.poc_it/runtime_contracts.json` deberían existir en el OpenAPI,
  salvo que el proyecto esté roto o el endpoint no se haya registrado (típico: falta include_router).

Este verificador NO modifica tests. Devuelve un diagnóstico para que la fase de reparación de código
(llm/fixers) actúe sobre `app/**` si es necesario.
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WiringVerifyResult:
    ok: bool
    detail: str
    missing_paths: List[str]


def _run_openapi_probe(project_dir: str) -> Tuple[bool, str, Optional[dict]]:
    """
    Intenta importar app.main y extraer /openapi.json usando TestClient en un proceso separado
    para aislar side-effects. Captura stdout+stderr.
    """
    code = r"""
import json
import sys
from fastapi.testclient import TestClient

try:
    from app.main import app
except Exception as e:
    print("IMPORT_ERROR:", repr(e))
    raise

c = TestClient(app)
r = c.get("/openapi.json")
print("STATUS:", r.status_code)
try:
    data = r.json()
except Exception as e:
    print("JSON_ERROR:", repr(e))
    data = None
print("OPENAPI_JSON:", json.dumps(data) if data is not None else "null")
"""
    p = subprocess.run(
        ["python", "-c", code],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    if p.returncode != 0:
        return False, out, None

    # parsear salida
    openapi_json = None
    for line in (p.stdout or "").splitlines():
        if line.startswith("OPENAPI_JSON:"):
            payload = line.split("OPENAPI_JSON:", 1)[1].strip()
            try:
                openapi_json = json.loads(payload)
            except Exception:
                openapi_json = None
            break

    return True, out, openapi_json if isinstance(openapi_json, dict) else None


def verify_wiring_against_runtime_contracts(
    *,
    project_dir: str,
    estructura: Dict[str, str],
) -> WiringVerifyResult:
    """
    Verifica que los paths de runtime_contracts existen en OpenAPI.
    - Si no hay runtime_contracts, devuelve ok=True (no podemos afirmar nada).
    - Si no se puede obtener OpenAPI, devuelve ok=False.
    """
    rc_raw = estructura.get(RUNTIME_CONTRACTS_PATH) or ""
    rc = None
    if isinstance(rc_raw, str) and rc_raw.strip():
        try:
            rc = json.loads(rc_raw)
        except Exception:
            rc = None

    endpoints = []
    if isinstance(rc, dict):
        endpoints = rc.get("endpoints") or []
    if not isinstance(endpoints, list) or not endpoints:
        return WiringVerifyResult(ok=True, detail="No runtime_contracts endpoints; skipping wiring verify.", missing_paths=[])

    ok_probe, out, openapi = _run_openapi_probe(project_dir)
    if not ok_probe or not isinstance(openapi, dict):
        return WiringVerifyResult(ok=False, detail="OpenAPI probe failed.\n" + out, missing_paths=[])

    paths_obj = openapi.get("paths") or {}
    if not isinstance(paths_obj, dict):
        return WiringVerifyResult(ok=False, detail="OpenAPI inválido (no contiene paths dict).", missing_paths=[])

    missing: List[str] = []
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        pth = str(ep.get("path") or "").strip()
        if not pth:
            continue
        if pth not in paths_obj:
            missing.append(pth)

    if missing:
        return WiringVerifyResult(
            ok=False,
            detail=(
                "Wiring mismatch: paths presentes en runtime_contracts pero ausentes en OpenAPI.\n"
                f"Missing: {sorted(set(missing))}\n"
                "Causa típica: routers no incluidos en app.main (falta include_router).\n"
            ),
            missing_paths=sorted(set(missing)),
        )

    return WiringVerifyResult(ok=True, detail="Wiring OK (runtime_contracts paths present in OpenAPI).", missing_paths=[])
