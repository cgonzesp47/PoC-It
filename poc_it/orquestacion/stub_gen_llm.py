from __future__ import annotations

from typing import Any, Dict, List


def suggest_dependency_behaviors(*, dependency_context: Dict[str, Any]) -> Dict[str, List[dict]]:
    """
    Sustituto estructurado del antiguo generador libre de stubs vía LLM.

    El LLM ya no emite código Python ni modifica archivos de tests.
    Puede, como mucho, sugerir comportamientos estructurados de dependencias
    para enriquecer el plan antes del render determinista.
    """
    dependencies = dependency_context.get("dependencies") if isinstance(dependency_context, dict) else None
    if not isinstance(dependencies, list):
        dependencies = []

    return {
        "behaviors": [
            {
                "dependency": str(dep.get("fqn") or dep.get("name") or "").strip(),
                "suggested_calls": list(dep.get("observed_calls") or []),
            }
            for dep in dependencies
            if isinstance(dep, dict) and str(dep.get("fqn") or dep.get("name") or "").strip()
        ]
    }
