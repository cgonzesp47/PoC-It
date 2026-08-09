from __future__ import annotations

from typing import Any, Dict


def repair_tests_for_coverage(*, plan: Any, coverage_report: Dict[str, Any], files: Dict[str, str]) -> Dict[str, Any]:
    """
    Compatibilidad transitoria para la antigua fase de reparación de cobertura.

    La cobertura ya no se corrige reescribiendo tests con LLM. Se verifica
    contra el plan validado y el reporte estructurado de cobertura.

    Resultado:
    - no modifica archivos;
    - devuelve diagnóstico estructurado para que una capa superior decida
      si debe regenerar desde un plan corregido.
    """
    return {
        "action": "regenerate_from_plan_if_needed",
        "modified_files": [],
        "files": dict(files or {}),
        "coverage_report": dict(coverage_report or {}),
        "plan_available": plan is not None,
    }
