from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from poc_it.testing.test_generation_service import TestGenerationService
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH

logger = logging.getLogger(__name__)


def _is_parcial(resultado: Dict[str, Any], modo_generacion: str | None = None) -> bool:
    if modo_generacion and str(modo_generacion).upper() == "PARCIAL":
        return True
    spec = resultado.get("spec") if isinstance(resultado, dict) else None
    if isinstance(spec, dict):
        modo = str(spec.get("modo") or spec.get("mode") or "").upper()
        if modo == "PARCIAL":
            return True
    modo2 = str((resultado or {}).get("modo") or (resultado or {}).get("mode") or "").upper()
    return modo2 == "PARCIAL"


def _load_runtime_json_from_structure_or_disk(
    *,
    estructura: Dict[str, str],
    nombre_proyecto: str,
    relative_path: str,
) -> dict | None:
    raw = estructura.get(relative_path) or ""
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            pass

    try:
        project_dir = os.path.join("output", nombre_proyecto)
        abs_path = os.path.join(project_dir, relative_path.replace("/", os.sep))
        if os.path.exists(abs_path):
            with open(abs_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
    except Exception:
        pass

    return None


def generar_tests_unitarios(
    nombre_proyecto: str,
    resultado: Dict[str, Any],
    estructura: Dict[str, str],
    archivos_creados: list[str],
    modo_generacion: str | None = None,
) -> bool:
    """
    Fachada pública de compatibilidad.

    Toda la generación de tests delega exclusivamente en `TestGenerationService`.
    Este módulo conserva únicamente:
    - carga best-effort de artefactos runtime;
    - limpieza de residuales en output/tests;
    - materialización final para mantener la firma histórica.
    """
    try:
        estructura_generada = dict(estructura)

        runtime_contracts = _load_runtime_json_from_structure_or_disk(
            estructura=estructura_generada,
            nombre_proyecto=nombre_proyecto,
            relative_path=RUNTIME_CONTRACTS_PATH,
        )
        runtime_facts = _load_runtime_json_from_structure_or_disk(
            estructura=estructura_generada,
            nombre_proyecto=nombre_proyecto,
            relative_path=".poc_it/runtime_facts.json",
        )

        try:
            tests_dir = os.path.join("output", nombre_proyecto, "tests")
            if os.path.isdir(tests_dir):
                for fn in os.listdir(tests_dir):
                    if fn.endswith(".py"):
                        try:
                            os.remove(os.path.join(tests_dir, fn))
                        except Exception:
                            pass
        except Exception:
            pass

        service = TestGenerationService()
        generation_result = service.generate(
            project_structure=estructura_generada,
            runtime_contracts=runtime_contracts,
            runtime_facts=runtime_facts,
            spec=resultado.get("spec") if isinstance(resultado, dict) else None,
            mode=str(
                modo_generacion
                or (resultado or {}).get("clasificacion")
                or (resultado or {}).get("modo")
                or ""
            ).strip()
            or ("PARCIAL" if _is_parcial(resultado, modo_generacion) else "COMPLETO"),
            project_name=nombre_proyecto,
        )

        if generation_result.warnings:
            logger.info("[TESTS] Generation warnings: %s", generation_result.warnings)

        return service.materialize_result(
            nombre_proyecto=nombre_proyecto,
            result=generation_result,
            estructura_destino=estructura,
            archivos_creados=archivos_creados,
            resultado=resultado,
            runtime_contracts=runtime_contracts,
        )
    except Exception as exc:
        logger.info("[TESTS] Error generando/materializando tests via TestGenerationService: %s", exc)
        return True
