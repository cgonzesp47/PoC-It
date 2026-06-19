from __future__ import annotations
"""
Filtrado determinista de endpoints testables (herméticos) en modo PARCIAL.

Motivación
----------
No podemos delegar todo el control al prompt del LLM. Antes de generar tests que
ejecuten handlers, debemos decidir de forma determinista si el endpoint es
testable de forma hermética en CI.

Fuente de verdad
----------------
- `.poc_it/runtime_contracts.json` (persistido por el orquestador)
  - endpoints[*].depends_imports
  - allowed_dependency_overrides (si existe; puede ser None)
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


@dataclass(frozen=True)
class EndpointBucket:
    hermetic_callable: List[dict]
    openapi_only: List[dict]
    skipped: List[dict]


def _norm_list(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for x in v:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def bucket_endpoints_for_tests(runtime_contracts: Optional[dict]) -> EndpointBucket:
    """
    Clasifica endpoints en buckets:
    - hermetic_callable: podemos ejecutarlos en tests (todas sus depends_imports son overrideables).
    - openapi_only: no podemos garantizar hermeticidad -> solo validar presencia en OpenAPI.
    - skipped: endpoints malformados (sin path/method).
    """
    if not isinstance(runtime_contracts, dict):
        return EndpointBucket(hermetic_callable=[], openapi_only=[], skipped=[])

    eps = runtime_contracts.get("endpoints") or []
    if not isinstance(eps, list):
        return EndpointBucket(hermetic_callable=[], openapi_only=[], skipped=[])

    allow = runtime_contracts.get("allowed_dependency_overrides")
    allow_set: Optional[Set[str]] = None
    if isinstance(allow, list) and allow:
        allow_set = set(_norm_list(allow))

    hermetic: List[dict] = []
    openapi_only: List[dict] = []
    skipped: List[dict] = []

    for ep in eps:
        if not isinstance(ep, dict):
            continue
        path = str(ep.get("path") or "").strip()
        method = str(ep.get("method") or "").strip().upper()
        if not path or not method:
            skipped.append(ep)
            continue

        deps_imports = _norm_list(ep.get("depends_imports"))
        if not deps_imports:
            # Sin depends_imports no hay forma genérica (y segura) de aislar integraciones;
            # podría ser hermético si no toca IO, pero no hay evidencia dura -> OpenAPI only.
            openapi_only.append(ep)
            continue

        if allow_set is not None:
            # Modo estricto: solo si todas están permitidas
            if all(d in allow_set for d in deps_imports):
                hermetic.append(ep)
            else:
                openapi_only.append(ep)
        else:
            # Si no hay allowlist, asumimos que cualquier depends_imports es overrideable (best-effort),
            # pero sigue siendo una decisión determinista para permitir tests de endpoint.
            hermetic.append(ep)

    return EndpointBucket(
        hermetic_callable=hermetic,
        openapi_only=openapi_only,
        skipped=skipped,
    )
