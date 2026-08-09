from __future__ import annotations
"""
Compat: construir SPEC dict desde RequestIR.

Motivación:
- El flujo previo usaba spec_builder.build_spec_from_ir(IRSpecPlan).
- Tras consolidar RequestIR como única fuente de verdad previa al SPEC, exponemos
  una API estable para el resto del sistema.

Este módulo NO usa LLM.
"""

from typing import Any, Dict

from poc_it.generador.request_ir import RequestIR
from poc_it.generador.spec_builder import build_spec_from_request_ir


def build_spec(ir: RequestIR) -> Dict[str, Any]:
    return build_spec_from_request_ir(ir)
