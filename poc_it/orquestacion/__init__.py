"""Paquete de orquestación.

Objetivo:
- Extraer helpers del orquestador para mejorar cohesión y testabilidad.
- Mantener estable la API pública del orquestador.

Este paquete expone utilidades internas. No pretende ser una API pública estable,
pero sí ofrece imports "cortos" dentro del repo.
"""

from poc_it.orquestacion.generacion_documentacion import generar_documentacion
from poc_it.orquestacion.generacion_tests import generar_tests_unitarios
from poc_it.orquestacion.persistencia_spec import persist_spec_json
from poc_it.orquestacion.postprocesado_alineacion import postprocesar_alineacion_por_pytest
from poc_it.orquestacion.reparacion_runtime import ejecutar_reparacion_runtime
from poc_it.orquestacion.verificador_runtime import runtime_verify_fastapi_project

__all__ = [
    "ejecutar_reparacion_runtime",
    "generar_documentacion",
    "generar_tests_unitarios",
    "persist_spec_json",
    "postprocesar_alineacion_por_pytest",
    "runtime_verify_fastapi_project",
]
