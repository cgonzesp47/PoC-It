from __future__ import annotations

import logging
import os
from typing import Any, Dict

from poc_it.generador_artefactos import generar_proyecto_completo
from poc_it.materializador_archivos import materializar_proyecto
from poc_it.models import ProjectContext
from poc_it.orquestacion.runtime_verifier import runtime_verify_fastapi_project
from poc_it.orquestacion.tests_generation import generar_tests_unitarios

logger = logging.getLogger(__name__)


def ejecutar_reparacion_runtime(
    *,
    nombre_proyecto: str,
    descripcion_global: str,
    project_dir: str,
    context: ProjectContext,
    resultado: Dict[str, Any],
    estructura: Dict[str, str],
    archivos_creados: list[str],
    regenerar_tests: bool,
    max_runtime_repairs: int = 5,
) -> None:
    """Repair loop para asegurar que el proyecto es importable (import-time) sin configuración externa.

    Mantiene el comportamiento del orquestador:
    - Verifica importabilidad con `runtime_verify_fastapi_project(project_dir)`.
    - Si falla, genera un patch con el LLM y materializa.
    - Si agota intentos, lanza ValueError con detalle para cortar el flujo.
    - Si se ha reparado y `regenerar_tests` es True, re-genera tests mínimos.
    """
    runtime_repaired = False

    for attempt in range(max_runtime_repairs + 1):
        ok_runtime, detail = runtime_verify_fastapi_project(project_dir)
        if ok_runtime:
            break

        if attempt >= max_runtime_repairs:
            raise ValueError(
                "El proyecto generado no supera verificación runtime.\n"
                + detail
                + "\n\n"
                + "Sugerencia: evita validar configuración/credenciales en import-time; "
                + "haz lazy init y valida en runtime (en el endpoint que lo necesite)."
            )

        error_context = f"""
FALLO EN VERIFICACIÓN RUNTIME (import app.main)
El proyecto debe ser importable sin configuración externa.

Error:
{detail}

REGLAS DE REPARACIÓN (MÍNIMAS, CANÓNICAS)
- Corrige SOLO los archivos del proyecto implicados en el traceback.
- No cambies la arquitectura ni introduzcas nuevas dependencias innecesarias: corrige wiring/errores.
- Evita instanciar servicios/configuración en import-time. Haz lazy init dentro de endpoints/funciones.
- Mantén el patrón FastAPI con routers.
- Regla general: si importas `from X import Y`, entonces Y DEBE existir en X (no inventar símbolos).
- Si existe `app/config/settings.py`, el patrón de settings debe ser consistente:
  - Debe existir `class Settings(BaseSettings)`.
  - Debe existir `def get_settings() -> Settings` (cacheada con lru_cache) y ser el ÚNICO punto de creación.
  - Prohibido `settings = Settings()` en import-time si hay campos requeridos.
  - Si algún módulo hace `from app.config.settings import get_settings`, entonces get_settings DEBE existir.

SALIDA
- Devuelve JSON con la lista completa de archivos corregidos (solo los modificados) con formato:
  {{ "files": [{{"path":"...", "content":"..."}}] }}
"""

        reparacion = generar_proyecto_completo(
            descripcion_global=descripcion_global + "\n\n" + error_context,
            contexto_normalizado=(context.contexto_normalizado.model_dump() if context.contexto_normalizado else None),
            intentos=2,
        )

        repaired_files = reparacion.get("files", [])
        if not repaired_files:
            continue

        patch = {f["path"]: f["content"] for f in repaired_files if "path" in f and "content" in f}
        if patch:
            runtime_repaired = True
            materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch)
            estructura.update(patch)
            archivos_creados.extend([os.path.join(project_dir, p.replace("/", os.sep)) for p in patch.keys()])

    if runtime_repaired and regenerar_tests:
        try:
            generar_tests_unitarios(nombre_proyecto, resultado, estructura, archivos_creados)
        except Exception as exc:
            logger.info("[TESTS] Error regenerando tests tras reparación runtime: %s", exc)
