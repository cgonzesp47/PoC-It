from __future__ import annotations

from typing import Any, Dict, List


def suggest_override_repairs(*, plan: Any, runtime_contracts: Dict[str, Any], failure_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sustituto estructurado del antiguo reparador libre de overrides/conftest.

    Restricciones de la arquitectura nueva:
    - el LLM no modifica directamente archivos de tests;
    - no reescribe `conftest.py`;
    - cualquier reparación debe expresarse como propuesta estructurada
      para regenerar desde un plan o contratos corregidos.
    """
    missing = failure_context.get("missing_overrides") if isinstance(failure_context, dict) else None
    if not isinstance(missing, list):
        missing = []

    current = runtime_contracts.get("allowed_dependency_overrides") if isinstance(runtime_contracts, dict) else None
    if not isinstance(current, list):
        current = []

    suggested: List[str] = []
    for item in missing:
        value = str(item).strip()
        if value and value not in current and value not in suggested:
            suggested.append(value)

    return {
        "action": "UPDATE_DEPENDENCY_BINDINGS",
        "edit_files_directly": False,
        "target": "runtime_contracts.allowed_dependency_overrides",
        "suggested_overrides": suggested,
        "plan_available": plan is not None,
        "reason": "Detected missing dependency override candidates from pytest failure context; return structured suggestions instead of editing conftest.py directly.",
    }
