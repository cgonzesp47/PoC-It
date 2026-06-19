from __future__ import annotations
"""
Construcción de contexto compacto para reparación/generación de tests.

Idea
----
En vez de una allowlist explícita de stdlib (que parece interminable), usamos un enfoque práctico:

- Permitido siempre: imports de `app.*`
- Permitido: paquetes presentes en requirements.txt / requirements-dev.txt
- Permitido "por defecto": stdlib (no se enumera)
- Restringido: imports externos NO presentes en requirements*.
  - Si el LLM necesita uno para un test hermético, debe:
      1) añadirlo a requirements-dev.txt
      2) y justificarlo en el patch (en comentarios o en el propio prompt se le exige)

Este módulo prepara el artefacto normativo y compacto:
  `.poc_it/test_repair_context.json`

Se alimenta de:
- `.poc_it/runtime_contracts.json` (request shape, allowlist overrides, endpoints)
- `.poc_it/stub_signatures.json` (firma observada de stubs / chains)
- requirements.txt / requirements-dev.txt (allowlist imports externos)
"""

import json
import re
from typing import Dict, List, Optional, Set

from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH

TEST_REPAIR_CONTEXT_PATH = ".poc_it/test_repair_context.json"

_REQ_LINE_RE = re.compile(r"^\\s*([A-Za-z0-9_.\\-]+)")


def _parse_requirements(content: str) -> Set[str]:
    pkgs: Set[str] = set()
    for ln in (content or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or ln.startswith("-"):
            continue
        m = _REQ_LINE_RE.match(ln)
        if not m:
            continue
        name = m.group(1).strip()
        if name:
            pkgs.add(name.replace("_", "-").lower())
    return pkgs


def _safe_json_loads(s: str) -> Optional[dict]:
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def build_test_repair_context(
    *,
    estructura: Dict[str, str],
    runtime_contracts: Optional[dict] = None,
    runtime_facts: Optional[dict] = None,
) -> dict:
    # runtime_contracts desde estructura si no viene inyectado
    rc: Optional[dict] = None
    if isinstance(runtime_contracts, dict):
        rc = runtime_contracts
    else:
        raw = estructura.get(RUNTIME_CONTRACTS_PATH) if isinstance(estructura, dict) else None
        if isinstance(raw, str) and raw.strip():
            rc = _safe_json_loads(raw)

    stubs: Optional[dict] = None
    stub_raw = estructura.get(".poc_it/stub_signatures.json") if isinstance(estructura, dict) else None
    if isinstance(stub_raw, str) and stub_raw.strip():
        stubs = _safe_json_loads(stub_raw)

    req = _parse_requirements(estructura.get("requirements.txt", "") if isinstance(estructura, dict) else "")
    req_dev = _parse_requirements(estructura.get("requirements-dev.txt", "") if isinstance(estructura, dict) else "")
    allow_import_packages = sorted(req | req_dev)

    allow_overrides: List[str] = []
    endpoints: List[dict] = []
    if isinstance(rc, dict):
        al = rc.get("allowed_dependency_overrides")
        if isinstance(al, list):
            allow_overrides = [str(x) for x in al if str(x).strip()]
        eps = rc.get("endpoints")
        if isinstance(eps, list):
            endpoints = [e for e in eps if isinstance(e, dict)]

    # Compact endpoints -> solo lo necesario para request-shape + asserts
    endpoints_compact: List[dict] = []
    for ep in endpoints:
        endpoints_compact.append(
            {
                "method": str(ep.get("method") or "").upper(),
                "path": str(ep.get("path") or ""),
                "depends_imports": list(ep.get("depends_imports") or []),
                "query_params_required": list(ep.get("query_params_required") or []),
                "query_params_optional": list(ep.get("query_params_optional") or []),
                "request_body_param": ep.get("request_body_param"),
                "request_required_fields": list(ep.get("request_required_fields") or []),
                "response_media_type": ep.get("response_media_type"),
                "response_json_shape": ep.get("response_json_shape"),
                "response_json_required_keys": list(ep.get("response_json_required_keys") or []),
            }
        )

    ctx = {
        "version": 1,
        "allowed_dependency_overrides": allow_overrides,
        "endpoints": endpoints_compact,
        "stub_signatures": stubs or {},
        "allow_import_packages": allow_import_packages,
        "import_policy": {
            "stdlib": "implicit_allow",
            "app_prefix": "allow",
            "third_party_requires_requirements": True,
            "how_to_add": "Add to requirements-dev.txt only if strictly needed for hermetic tests; avoid SDKs/drivers of external integrations",
        },
        "notes": {
            "hermetic_tests": True,
            "partial_mode": True,
            "source_of_truth": {
                "runtime_contracts": RUNTIME_CONTRACTS_PATH,
                "stub_signatures": ".poc_it/stub_signatures.json",
                "requirements": ["requirements.txt", "requirements-dev.txt"],
            },
        },
    }

    if isinstance(runtime_facts, dict):
        ctx["runtime_facts"] = runtime_facts

    return ctx
