from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

from poc_it.generador_artefactos import generar_proyecto_desde_spec
from poc_it.materializador_archivos import materializar_proyecto
from poc_it.models import ProjectContext
from poc_it.orquestacion.verificador_runtime import runtime_verify_fastapi_project
from poc_it.orquestacion.generacion_tests import generar_tests_unitarios

logger = logging.getLogger(__name__)

_MISSING_MODULE_RE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")
_REQUIREMENTS_PKG_RE = re.compile(r"^([a-zA-Z0-9_.-]+)")


def _extraer_modulo_faltante(runtime_detail: str) -> Optional[str]:
    if not runtime_detail:
        return None
    m = _MISSING_MODULE_RE.search(runtime_detail)
    if not m:
        return None
    return (m.group(1) or "").strip() or None


def _requirements_contiene_paquete(requirements_txt: str, paquete: str) -> bool:
    if not requirements_txt or not paquete:
        return False
    base = paquete.strip().lower()
    for line in requirements_txt.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _REQUIREMENTS_PKG_RE.match(s)
        if not m:
            continue
        if m.group(1).strip().lower() == base:
            return True
    return False


def _append_requirement(requirements_txt: str, paquete: str) -> str:
    # Mantener orden humano: añadir al final, asegurar newline final.
    current = requirements_txt or ""
    if not current.endswith("\n") and current.strip():
        current += "\n"
    return (current + f"{paquete}\n").lstrip("\n")


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
    last_missing_module: Optional[str] = None
    llm_attempted_for_missing: bool = False

    for attempt in range(max_runtime_repairs + 1):
        ok_runtime, detail = runtime_verify_fastapi_project(project_dir, resultado.get("spec"))
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

        missing_mod = _extraer_modulo_faltante(detail)

        # Política pragmática anti-loop:
        # - Primero intentamos 1 reparación con LLM (flujo actual).
        # - Si vuelve a fallar por ModuleNotFoundError con el MISMO módulo, parcheamos requirements.txt
        #   añadiendo el paquete (mismo nombre que el módulo) y reintentamos runtime_verify.
        if missing_mod:
            if last_missing_module == missing_mod and llm_attempted_for_missing:
                pypi_pkg = missing_mod  # pragmático: 1:1 por defecto
                current_reqs = estructura.get("requirements.txt") or ""

                if not _requirements_contiene_paquete(current_reqs, pypi_pkg):
                    logger.info(
                        "[RUNTIME_REPAIR] ModuleNotFoundError persistente tras 1 intento LLM (%s). "
                        "Parchando requirements.txt con '%s'",
                        missing_mod,
                        pypi_pkg,
                    )
                    patch = {"requirements.txt": _append_requirement(current_reqs, pypi_pkg)}

                    runtime_repaired = True
                    materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch)
                    estructura.update(patch)
                    archivos_creados.extend([os.path.join(project_dir, "requirements.txt")])

                    # Mantener spec como “fuente de verdad” (si está disponible)
                    spec_obj = resultado.get("spec")
                    if isinstance(spec_obj, dict):
                        deps = spec_obj.get("dependencies")
                        if not isinstance(deps, list):
                            deps = []
                        deps_norm = [str(d).strip() for d in deps if str(d).strip()]
                        if pypi_pkg not in deps_norm:
                            deps_norm.append(pypi_pkg)
                            spec_obj["dependencies"] = deps_norm
                            resultado["spec"] = spec_obj

                    # reintentar runtime_verify sin tocar código
                    continue

            # primer fallo por este módulo: marcamos y dejamos que el LLM lo intente una vez
            last_missing_module = missing_mod
            llm_attempted_for_missing = True

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

        spec = resultado.get("spec")
        files_iniciales = [{"path": p, "content": c} for p, c in (estructura or {}).items()]

        if not isinstance(spec, dict):
            # fallback conservador (mantener comportamiento previo si no hay spec usable)
            reparacion = generar_proyecto_desde_spec(
                spec={},
                descripcion_global=descripcion_global + "\n\n" + error_context,
                contexto_normalizado=(context.contexto_normalizado.model_dump() if context.contexto_normalizado else None),
                intentos=max(2, max_runtime_repairs),
                files_iniciales=files_iniciales,
            )
        else:
            # Repair incremental: no re-generar el proyecto completo por lotes.
            reparacion = generar_proyecto_desde_spec(
                spec=spec,
                descripcion_global=descripcion_global + "\n\n" + error_context,
                contexto_normalizado=(context.contexto_normalizado.model_dump() if context.contexto_normalizado else None),
                intentos=max(2, max_runtime_repairs),
                files_iniciales=files_iniciales,
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
