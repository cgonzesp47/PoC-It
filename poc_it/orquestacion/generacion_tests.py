from __future__ import annotations

import logging
from typing import Any, Dict, List

from poc_it.generador_tests_unitarios import generar_tests_unitarios_minimos
from poc_it.materializador_archivos import materializar_proyecto

logger = logging.getLogger(__name__)


def _normalizar_reqs(lines: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for ln in lines:
        s = (ln or "").strip()
        if not s or s.startswith("#"):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _asegurar_requirements_dev(estructura: Dict[str, str], resultado: Dict[str, Any]) -> None:
    """
    Asegura un requirements-dev.txt coherente cuando se han generado tests.

    Política:
    - runtime deps en requirements.txt (si existe).
    - dev/test deps en requirements-dev.txt (se crea si faltaba).
    - Si spec trae dev_dependencies, se usan como base.
    - Backward compatible: si no hay dev_dependencies, usamos mínimos: pytest, pytest-mock, httpx.
    """
    spec = resultado.get("spec") if isinstance(resultado, dict) else None
    dev_deps = []
    if isinstance(spec, dict):
        dd = spec.get("dev_dependencies")
        if isinstance(dd, list):
            dev_deps = [str(x) for x in dd if str(x).strip()]

    if not dev_deps:
        dev_deps = ["pytest", "pytest-mock", "httpx"]

    dev_deps = _normalizar_reqs(dev_deps)

    # Escribimos requirements-dev.txt solo si no existe o está vacío.
    # (Si existe con contenido, lo respetamos para evitar “pisar” decisiones del modelo/usuario).
    existing = (estructura.get("requirements-dev.txt") or "").strip()
    if not existing:
        estructura["requirements-dev.txt"] = "\n".join(dev_deps) + "\n"


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
            # 1) Materializar tests
            archivos_tests = materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura=tests_result.estructura_tests,
                limpiar_directorio=False,
            )
            archivos_creados.extend(archivos_tests)
            estructura.update(tests_result.estructura_tests)

            # 2) Asegurar deps de dev/tests (evita bug: tests usan pytest-mock pero no está en requirements)
            _asegurar_requirements_dev(estructura, resultado)

            # 3) Materializar requirements-dev.txt si lo hemos creado
            if "requirements-dev.txt" in estructura:
                archivos_req = materializar_proyecto(
                    nombre_proyecto=nombre_proyecto,
                    estructura={"requirements-dev.txt": estructura["requirements-dev.txt"]},
                    limpiar_directorio=False,
                )
                archivos_creados.extend(archivos_req)
    except Exception as exc:
        logger.info("[TESTS] Error generando/materializando tests: %s", exc)

    return generar_tests
