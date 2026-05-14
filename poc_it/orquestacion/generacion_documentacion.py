from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from poc_it.generador_informes import generar_readme_asesor, generar_readme_final, generar_readme_manual
from poc_it.materializador_archivos import materializar_proyecto
from poc_it.models import ModoGeneracion, ProjectContext
from poc_it.opciones import generar_opciones
from poc_it.orquestacion.constantes import (
    ARQUITECTURA_LLM_DEFAULT,
    OUTPUT_DIRNAME,
    README_ANALISIS_FILENAME,
    README_FINAL_FILENAME,
    README_MANUAL_FILENAME,
)
from poc_it.orquestacion.verificador_runtime import runtime_verify_fastapi_project

logger = logging.getLogger(__name__)


def generar_documentacion(
    *,
    nombre_proyecto: str,
    descripcion_global: str,
    tecnologias: Optional[str],
    context: ProjectContext,
    modo_generacion: str,
    estructura: Dict[str, str],
    resultado: Dict[str, Any],
    estimacion_generada: Any,
    estimacion_manual: Any,
    t_clasificacion_inicio: float,
    t_clasificacion_fin: float,
    t_generacion_inicio: float,
    t_generacion_fin: float,
) -> None:
    """Genera y materializa la documentación (readmes) del proyecto.

    Extraído desde `OrquestadorParcial._generar_documentacion` para reducir acoplamiento y
    mantener el orquestador como coordinador. Mantiene el comportamiento existente:
    - Si modo != ASESOR, verifica que el proyecto sea importable antes de generar docs.
    - Genera README.md (final), README_ANALISIS.md (asesor) y opcionalmente README_MANUAL.md (si PARCIAL).
    - Materializa los ficheros en `output/<nombre_proyecto>` sin limpiar directorio.
    - Registra métricas de performance.

    Nota: `modo_generacion` se mantiene como str por compatibilidad con el flujo actual
    (que usa `context.clasificacion`), pero se compara con `ModoGeneracion.*`.
    """
    generar_docs = True
    if modo_generacion.upper() != ModoGeneracion.ASESOR:
        project_dir = os.path.join(OUTPUT_DIRNAME, nombre_proyecto)
        ok_runtime, detail = runtime_verify_fastapi_project(project_dir)
        if not ok_runtime:
            generar_docs = False
            logger.info("[DOCS] Saltando generación de documentación: el proyecto no es importable aún.")
            logger.info(detail)

    if not generar_docs:
        return

    endpoints_detectados = [path for path in estructura.keys() if path.endswith(".py")]

    if context.contexto_normalizado:
        arquitectura_real = context.contexto_normalizado.objetivo_tecnico
        limites_reales = ", ".join(context.contexto_normalizado.restricciones_tecnicas)
        tecnologias_reales = ", ".join(context.contexto_normalizado.integraciones_externas)
        funcionalidades_reales = ", ".join(context.contexto_normalizado.funcionalidades_clave)
        usuarios_reales = ", ".join(context.contexto_normalizado.actores_principales)
    else:
        arquitectura_real = context.plantilla.problema
        limites_reales = context.plantilla.limites or ""
        tecnologias_reales = context.plantilla.tecnologias or ""
        funcionalidades_reales = context.plantilla.funcionalidades or ""
        usuarios_reales = context.plantilla.usuarios or ""

    opciones_estrategicas = generar_opciones(
        arquitectura=arquitectura_real,
        limites=limites_reales,
        tecnologias=tecnologias_reales,
    )

    import time

    t_documentacion_inicio = time.perf_counter()

    spec_dict: Optional[Dict[str, Any]] = None
    try:
        raw_spec = resultado.get("spec") if isinstance(resultado, dict) else None
        if isinstance(raw_spec, dict) and raw_spec:
            spec_dict = raw_spec
    except Exception:
        spec_dict = None

    if not spec_dict:
        spec_dict = context.contexto_normalizado.model_dump() if context.contexto_normalizado else None

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_final = executor.submit(
            generar_readme_final,
            nombre_proyecto,
            descripcion_global,
            ARQUITECTURA_LLM_DEFAULT,
            endpoints_detectados,
            modo_generacion.upper(),
            tecnologias,
            estimacion_generada,
            spec=spec_dict,
        )

        future_analisis = executor.submit(
            generar_readme_asesor,
            nombre_proyecto,
            context.plantilla.problema,
            usuarios_reales,
            funcionalidades_reales,
            limites_reales,
            tecnologias_reales,
            arquitectura_real,
            opciones_estrategicas,
            estimacion_manual,
            spec_dict,
        )

        future_manual = None
        if modo_generacion.upper() == ModoGeneracion.PARCIAL:
            future_manual = executor.submit(
                generar_readme_manual,
                nombre_proyecto,
                ARQUITECTURA_LLM_DEFAULT,
                tecnologias,
                endpoints_detectados,
                estructura,
                spec_dict,
            )

        readme_final = future_final.result()
        readme_analisis = future_analisis.result()
        readme_manual = future_manual.result() if future_manual else None

    materializar_proyecto(
        nombre_proyecto=nombre_proyecto,
        estructura={README_FINAL_FILENAME: readme_final},
        limpiar_directorio=False,
    )

    materializar_proyecto(
        nombre_proyecto=nombre_proyecto,
        estructura={README_ANALISIS_FILENAME: readme_analisis},
        limpiar_directorio=False,
    )

    if readme_manual:
        materializar_proyecto(
            nombre_proyecto=nombre_proyecto,
            estructura={README_MANUAL_FILENAME: readme_manual},
            limpiar_directorio=False,
        )

    t_documentacion_fin = time.perf_counter()

    logger.info("\n[PERFORMANCE]")
    logger.info("- Clasificación: %.2fs", t_clasificacion_fin - t_clasificacion_inicio)
    logger.info("- Generación libre: %.2fs", t_generacion_fin - t_generacion_inicio)
    logger.info("- Documentación: %.2fs\n", t_documentacion_fin - t_documentacion_inicio)
