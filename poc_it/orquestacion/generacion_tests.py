from __future__ import annotations

import logging
from typing import Any, Dict

from poc_it.generador_tests_unitarios import generar_tests_unitarios_minimos
from poc_it.materializador_archivos import materializar_proyecto

logger = logging.getLogger(__name__)


def generar_tests_unitarios(
    nombre_proyecto: str,
    resultado: Dict[str, Any],
    estructura: Dict[str, str],
    archivos_creados: list[str],
) -> bool:
    """Genera y materializa tests unitarios mínimos.

    Mantiene el comportamiento del orquestador:
    - En errores, no rompe el flujo: solo log informativo.
    - Si genera tests, actualiza `estructura` in-memory y extiende `archivos_creados`.
    """
    generar_tests = True

    try:
        spec_dict = resultado.get("spec") if isinstance(resultado, dict) else None
        tests_result = generar_tests_unitarios_minimos(
            nombre_proyecto=nombre_proyecto,
            spec=spec_dict if isinstance(spec_dict, dict) else None,
            estructura_generada=estructura,
            intentos=1,
        )
        if tests_result.errores:
            logger.info("[TESTS] Aviso: generación de tests con warnings: %s", tests_result.errores)

        if tests_result.estructura_tests:
            archivos_tests = materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura=tests_result.estructura_tests,
                limpiar_directorio=False,
            )
            archivos_creados.extend(archivos_tests)
            estructura.update(tests_result.estructura_tests)
    except Exception as exc:
        logger.info("[TESTS] Error generando/materializando tests: %s", exc)

    return generar_tests
