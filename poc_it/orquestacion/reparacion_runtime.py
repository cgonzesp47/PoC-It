from __future__ import annotations

import logging

import os
import re
from typing import Any, Dict, Optional

from poc_it.materializacion.generador_artefactos import generar_proyecto_desde_spec
from poc_it.materializacion.materializador_archivos import materializar_proyecto
from poc_it.modulos.models import ProjectContext
from poc_it.orquestacion.llm_safe_repair import SafeRepairConfig, run_llm_safe_repair
from poc_it.orquestacion.verificador_runtime import runtime_verify_fastapi_project
from poc_it.orquestacion.venv_manager import ensure_project_venv_ready
from poc_it.orquestacion.generacion_tests import generar_tests_unitarios
from poc_it.orquestacion.pytest_llm_repair import repair_tests_until_pytest_passes
from poc_it.orquestacion.test_plan import TEST_PLAN_PATH, build_test_plan, persist_test_plan
from poc_it.orquestacion.wiring_verifier import verify_wiring_against_runtime_contracts
from poc_it.orquestacion.runtime_probe import run_runtime_probe
from poc_it.entrada.demo_progress import demo_progress, is_demo_mode

logger = logging.getLogger(__name__)


def _is_demo_mode() -> bool:
    # compat con lógica antigua en este módulo
    return is_demo_mode()

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
    max_runtime_repairs: int = 3,
) -> None:
    """Repair loop best-effort para asegurar que el proyecto es importable (import-time) sin configuración externa.

    Objetivo pragmático:
    - Verificar importabilidad con `runtime_verify_fastapi_project(project_dir, spec)`.
    - Si falla, intentar reparar con el LLM y materializar un patch.
    - Si agota intentos, NO corta el flujo: persiste un artefacto de error dentro del proyecto
      y continúa (se debe devolver el código generado aunque no sea importable).

    Nota:
    - Si se ha reparado y `regenerar_tests` es True, re-genera tests mínimos.
    """
    runtime_repaired = False
    code_ok = False
    last_missing_module: Optional[str] = None
    llm_attempted_for_missing: bool = False

    if is_demo_mode():
        demo_progress.step(6, 8, "Verificación y reparación automática")
        demo_progress.info("Ejecutando verificación de runtime e intentando autoreparación...")
    else:
        logger.info(
            "[PIPELINE][A] Code correctness loop (max=%s): import-time + wiring(OpenAPI) + request-time probe",
            max_runtime_repairs,
        )

    # Asegurar entorno hermético para runtime verify/probe:
    # - En máquinas limpias, sin esto fallará con ModuleNotFoundError (p.ej. fastapi).
    #
    # IMPORTANTE: si no se puede crear/preparar el venv, lo registramos de forma visible.
    # Si se silencia este fallo, el pipeline acaba fallando más tarde en el OpenAPI probe con
    # un ModuleNotFoundError que parece "misterioso".
    try:
        # Guardrail: normalizar `project_dir` para evitar paths corruptos (observado: duplicación
        # .../output/<name>/output/<name>), que rompe venv/ensurepip y runtime probes.
        project_dir = os.path.normpath(project_dir)

        vr = ensure_project_venv_ready(project_dir=project_dir, estructura=estructura, spec=resultado.get("spec"))
        if not getattr(vr, "ok", True):
            logger.warning(
                "[VENV] No se pudo preparar venv del proyecto (%s): %s", project_dir, getattr(vr, "detail", "")
            )
    except Exception as exc:
        # best-effort: no romper pipeline si no se puede crear venv por política del entorno
        logger.warning("[VENV] Excepción preparando venv del proyecto (%s): %s", project_dir, exc)

    for attempt in range(max_runtime_repairs + 1):
        ok_runtime, detail = runtime_verify_fastapi_project(project_dir, resultado.get("spec"))
        if ok_runtime:
            # Verificación adicional determinista: wiring mínimo vía OpenAPI
            w = verify_wiring_against_runtime_contracts(project_dir=project_dir, estructura=estructura)
            if not w.ok:
                ok_runtime = False
                detail = "RUNTIME_WIRING_VERIFY_FAILED\n" + (w.detail or "")

        if ok_runtime:
            # Verificación request-time (best-effort en PARCIAL):
            # - Siempre ejecutamos el probe para enriquecer runtime_contracts con facts (query/body),
            #   lo que alinea generación/repair de tests.
            # - En modo PARCIAL NO bloqueamos el pipeline si un endpoint devuelve 500, porque puede
            #   depender de integraciones externas o de shapes no reproducibles (form/file) en este probe.
            # Sincronización crítica: si runtime_contracts fue materializado a disco pero `estructura`
            # aún no lo contiene (o está vacío), el probe seleccionaría 0 endpoints y stub_signatures quedaría vacío.
            try:
                rc_key = " .poc_it/runtime_contracts.json".strip()
                rc_in_mem = estructura.get(rc_key) or ""
                if not (isinstance(rc_in_mem, str) and rc_in_mem.strip()):
                    rc_path = os.path.join(project_dir, ".poc_it", "runtime_contracts.json")
                    if os.path.exists(rc_path):
                        with open(rc_path, "r", encoding="utf-8") as f:
                            estructura[rc_key] = f.read()
            except Exception:
                pass
            probe = run_runtime_probe(project_dir=project_dir, estructura=estructura, max_endpoints=3)

            # Si el probe ha generado/actualizado artefactos, materializarlos en el proyecto.
            # IMPORTANTE: runtime_probe puede enriquecer `.poc_it/runtime_contracts.json` en `estructura`
            # (query/body/response hints). Si no lo persistimos a disco aquí, la fase C podría leer
            # un runtime_contracts "viejo" y fallar en reparar 422 (json vs params).
            try:
                patch_probe = {}
                if ".poc_it/runtime_contracts.json" in (estructura or {}):
                    patch_probe[".poc_it/runtime_contracts.json"] = estructura[".poc_it/runtime_contracts.json"]

                if ".poc_it/stub_signatures.json" in (estructura or {}):
                    patch_probe[".poc_it/stub_signatures.json"] = estructura[".poc_it/stub_signatures.json"]

                if patch_probe:
                    materializar_proyecto(
                        nombre_proyecto=nombre_proyecto,
                        estructura=patch_probe,
                        limpiar_directorio=False,
                    )
                    for rel in patch_probe.keys():
                        archivos_creados.append(os.path.join(project_dir, rel.replace("/", os.sep)))
            except Exception:
                logger.exception(
                    "[PIPELINE][A] Error auxiliar persistiendo artefactos de runtime/probe; se continúa sin esos artefactos"
                )

            if not probe.ok:
                gen_mode = ""
                try:
                    rc_raw = estructura.get(" .poc_it/runtime_contracts.json".strip()) or ""
                    import json as _json
                    rc_obj = _json.loads(rc_raw) if isinstance(rc_raw, str) and rc_raw.strip() else {}
                    gen_mode = str((rc_obj or {}).get("generation_mode") or "").upper()
                except Exception:
                    gen_mode = ""

                if gen_mode and gen_mode != "PARCIAL":
                    ok_runtime = False
                    detail = "RUNTIME_REQUEST_PROBE_FAILED\n" + (probe.detail or "")
                else:
                    logger.info(
                        "[PIPELINE][A] Runtime request-probe falló pero se ignora en PARCIAL (best-effort). Detalle: %s",
                        (probe.detail or "")[:400],
                    )

        # ------------------------------------------------------------
        # SUBSECCIÓN (aislable): LLM Safe Repair (diagnose -> patch -> gates -> rollback)
        #
        # Objetivo de esta subsección:
        # - Dejar el código en estado "correcto" ANTES de generar tests.
        # - Cubre:
        #   A) import-time (runtime_verify)
        #   B) wiring (OpenAPI/runtime_contracts)
        #   C) request-time en endpoints (p.ej. NameError logger, exceptions no controladas)
        #
        # Feature flags:
        #   POC_IT_SAFE_LLM_REPAIR=1
        #   POC_IT_SAFE_LLM_REPAIR_MAX=2
        #   POC_IT_SAFE_LLM_REPAIR_REQUIRE_PROBE_OK=1   (si se activa, exige probe.ok incluso en PARCIAL)
        #
        # Nota: NO usa fixers deterministas. Solo LLM + gates + rollback.
        # ------------------------------------------------------------
        # Safe repair es el camino principal (feature-flag solo para desactivar).
        # Objetivo: evitar el repair legacy sin gates/rollback cuando hay fallos import-time típicos.
        safe_disabled = str(os.getenv("POC_IT_SAFE_LLM_REPAIR", "")).strip() in ("0", "false", "False", "no", "NO")
        safe_enabled = not safe_disabled
        safe_max = int(os.getenv("POC_IT_SAFE_LLM_REPAIR_MAX", "2") or "2")
        require_probe_ok = str(os.getenv("POC_IT_SAFE_LLM_REPAIR_REQUIRE_PROBE_OK", "")).strip() in (
            "1",
            "true",
            "True",
            "yes",
            "YES",
        )

        safe = SafeRepairConfig(
            enabled=safe_enabled,
            max_attempts=max(1, safe_max),
        )

        # Gate C: request-time probe, controlable por flag para no romper PARCIAL si el usuario no quiere.
        # Si se activa el safe repair, siempre ejecutamos el probe (ya lo ejecutamos arriba) pero:
        # - Si require_probe_ok=True, entonces un probe no-ok se considera fallo y activa repair.
        if ok_runtime and safe.enabled and require_probe_ok and (probe is not None) and (not probe.ok):
            ok_runtime = False
            detail = "RUNTIME_REQUEST_PROBE_FAILED\n" + (probe.detail or "")

        if safe.enabled and not ok_runtime:
            snapshot = dict(estructura)  # rollback in-memory
            guardrails_warnings = None
            try:
                guardrails_warnings = (resultado.get("guardrails") or {}).get("warnings")  # best-effort
            except Exception:
                guardrails_warnings = None

            sr = run_llm_safe_repair(
                config=safe,
                project_dir=project_dir,
                spec=resultado.get("spec") if isinstance(resultado.get("spec"), dict) else None,
                estructura=estructura,
                runtime_detail=detail,
                guardrails_warnings=guardrails_warnings if isinstance(guardrails_warnings, list) else None,
            )

            if sr.ok and sr.patch:
                patch = sr.patch
                runtime_repaired = True
                materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch)
                estructura.update(patch)
                archivos_creados.extend([os.path.join(project_dir, p.replace("/", os.sep)) for p in patch.keys()])

                # Gate 1: import-time verify again
                ok2, detail2 = runtime_verify_fastapi_project(project_dir, resultado.get("spec"))
                if not ok2:
                    logger.info("[SAFE_LLM_REPAIR] Patch rechazado por runtime_verify. Rollback.")
                    estructura.clear()
                    estructura.update(snapshot)
                    try:
                        materializar_proyecto(
                            nombre_proyecto=nombre_proyecto,
                            estructura=snapshot,
                            limpiar_directorio=False,
                        )
                    except Exception:
                        pass
                    detail = detail2
                    ok_runtime = False
                else:
                    # Gate 2: wiring verify
                    w2 = verify_wiring_against_runtime_contracts(project_dir=project_dir, estructura=estructura)
                    if not w2.ok:
                        logger.info("[SAFE_LLM_REPAIR] Patch rechazado por wiring_verify. Rollback.")
                        estructura.clear()
                        estructura.update(snapshot)
                        try:
                            materializar_proyecto(
                                nombre_proyecto=nombre_proyecto,
                                estructura=snapshot,
                                limpiar_directorio=False,
                            )
                        except Exception:
                            pass
                        detail = "RUNTIME_WIRING_VERIFY_FAILED\n" + (w2.detail or "")
                        ok_runtime = False
                    else:
                        # Gate 3: request-time probe (best-effort salvo flag require_probe_ok)
                        probe2 = run_runtime_probe(project_dir=project_dir, estructura=estructura, max_endpoints=3)
                        if require_probe_ok and not probe2.ok:
                            logger.info("[SAFE_LLM_REPAIR] Patch rechazado por request-probe. Rollback.")
                            estructura.clear()
                            estructura.update(snapshot)
                            try:
                                materializar_proyecto(
                                    nombre_proyecto=nombre_proyecto,
                                    estructura=snapshot,
                                    limpiar_directorio=False,
                                )
                            except Exception:
                                pass
                            detail = "RUNTIME_REQUEST_PROBE_FAILED\n" + (probe2.detail or "")
                            ok_runtime = False
                        else:
                            logger.info("[SAFE_LLM_REPAIR] Patch aceptado. Files=%s", list(patch.keys()))
                            ok_runtime = True
                            # accepted: reintentar loop principal con código ya “más correcto”
                            continue
            else:
                logger.info(
                    "[SAFE_LLM_REPAIR] No se pudo producir patch seguro. Logs=%s",
                    [(l.stage, l.ok) for l in sr.logs],
                )
        # ------------------------------------------------------------
        # Fin subsección LLM Safe Repair
        # ------------------------------------------------------------

        if ok_runtime:
            code_ok = True
            break

        if attempt >= max_runtime_repairs:
            msg = (
                "El proyecto generado no supera verificación runtime (import-time).\n"
                + detail
                + "\n\n"
                + "Sugerencia: evita validar configuración/credenciales en import-time; "
                + "haz lazy init y valida en runtime (en el endpoint que lo necesite)."
            )
            if _is_demo_mode():
                # En demo, no imprimir detalle/traceback crudo (ruido).
                # El detalle completo se persistirá en RUNTIME_VERIFY_ERROR.txt dentro del proyecto generado.
                logger.warning("[PIPELINE] Reparación automática en curso.")
            else:
                logger.error("[RUNTIME_REPAIR] %s", msg)

            # Persistimos un artefacto de error dentro del proyecto para que el usuario lo vea
            # incluso si la ejecución continúa y se devuelven los archivos generados.
            patch = {"RUNTIME_VERIFY_ERROR.txt": msg + "\n"}
            try:
                materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch)
                estructura.update(patch)
                archivos_creados.extend([os.path.join(project_dir, "RUNTIME_VERIFY_ERROR.txt")])
            except Exception as exc:
                logger.info("[RUNTIME_REPAIR] No se pudo materializar RUNTIME_VERIFY_ERROR.txt: %s", exc)

            # IMPORTANTE:
            # Aunque el código NO sea importable (code_ok=False), seguimos con la ejecución global
            # y necesitamos que el pipeline pueda "degradar" a contract-lite más adelante.
            #
            # Para que esa degradación sea posible, intentamos dejar un suite mínimo de tests
            # (smoke_import + openapi) materializado en el proyecto. Esto sirve para:
            # - permitir un pytest mínimo (si el entorno tiene dependencias instaladas)
            # - y, aunque no pase, dejar una base coherente para el usuario.
            try:
                from poc_it.orquestacion.pytest_llm_repair import _degrade_to_contract_lite

                patch_min = _degrade_to_contract_lite(nombre_proyecto=nombre_proyecto, estructura=estructura)
                materializar_proyecto(
                    nombre_proyecto=nombre_proyecto,
                    estructura=patch_min,
                    limpiar_directorio=False,
                )
                estructura.update(patch_min)
                archivos_creados.extend([os.path.join(project_dir, p.replace("/", os.sep)) for p in patch_min.keys()])
            except Exception:
                pass

            # Marcar explícitamente que no se pudo completar runtime_verify (ayuda a la política del orquestador)
            try:
                if isinstance(resultado, dict):
                    resultado["runtime_ok"] = False
            except Exception:
                pass

            return

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

REGLAS PRAGMÁTICAS DE COMPATIBILIDAD (CÓDIGO COMPILABLE)
- Pydantic v2 OBLIGATORIO:
  - PROHIBIDO `orm_mode = True` y PROHIBIDO `from_orm(...)`.
  - Para ORM/atributos:
    - `from pydantic import BaseModel, ConfigDict`
    - `model_config = ConfigDict(from_attributes=True)`
    - `Schema.model_validate(obj)` (no `from_orm`)
  - Para settings:
    - `from pydantic_settings import BaseSettings` (no `from pydantic import BaseSettings`)
- Cache/async:
  - Está PROHIBIDO aplicar `@lru_cache` sobre `async def` (produce errores tipo “cannot reuse already awaited coroutine”).
  - Si necesitas cachear un recurso async (engine/cliente), usa variable global inicializada lazy (None -> create),
    o una función sync cacheada que devuelva un objeto ya construido sin await.
- La reparación debe mantener la intención original del usuario (SPEC) y priorizar compilabilidad.

- Si existe `app/config/settings.py`, el patrón de settings debe ser consistente:
  - Debe existir `class Settings(BaseSettings)`.
  - Debe existir `def get_settings() -> Settings` (cacheada con lru_cache) y ser el ÚNICO punto de creación.
  - Prohibido `settings = Settings()` en import-time si hay campos requeridos.
  - Si algún módulo hace `from app.config.settings import get_settings`, entonces get_settings DEBE existir.

SALIDA
- Devuelve JSON con la lista completa de archivos corregidos (solo los modificados) con formato:
  {{ "files": [{{"path":"...", "content":"..."}}] }}
"""

        # Si safe repair está habilitado, evitamos el repair legacy (sin gates) salvo que esté explícitamente desactivado.
        # Motivo: el repair legacy suele empeorar la convergencia y no tiene rollback.
        if safe.enabled:
            continue

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

    # Fase B: generación de tests (LLM) sobre código ya validado (import + wiring).
    # Nota: aquí NO se repara código. Solo se generan tests.
    if regenerar_tests and code_ok:
        if is_demo_mode():
            demo_progress.step(7, 8, "Generación de tests y ejecución local")
            demo_progress.info("Generando tests unitarios y ejecutando pytest...")
        else:
            logger.info("[PIPELINE][B] Generación de tests herméticos (LLM)")
        try:
            # FIX: Propagar modo de generación real (sin hardcodear).
            #
            # Fuente de verdad:
            # - `resultado["clasificacion"]` (string) cuando viene del clasificador.
            # - `spec["modo"]` / `spec["mode"]` si existe.
            # - `resultado["modo"]` / `resultado["mode"]` como fallback.
            #
            # Si no se propaga, generacion_tests.py puede inferir mal _is_parcial() (p.ej. modo_generacion=None)
            # y disparar validaciones legacy / fallbacks incorrectos.
            modo = None
            try:
                modo = str((resultado or {}).get("clasificacion") or "").strip() or None
            except Exception:
                modo = None
            if not modo:
                try:
                    spec = (resultado or {}).get("spec") if isinstance(resultado, dict) else None
                    if isinstance(spec, dict):
                        modo = str(spec.get("modo") or spec.get("mode") or "").strip() or None
                except Exception:
                    modo = None
            if not modo:
                try:
                    modo = str((resultado or {}).get("modo") or (resultado or {}).get("mode") or "").strip() or None
                except Exception:
                    modo = None

            generar_tests_unitarios(
                nombre_proyecto,
                resultado,
                estructura,
                archivos_creados,
                modo_generacion=modo,
            )
        except Exception as exc:
            logger.info("[TESTS] Error generando tests: %s", exc)

        # Fase C: loop de pytest SOLO sobre tests (prohibido tocar app/**).
        #
        # Política nueva (contract-first):
        # - El renderer determinista ya genera una suite "conservadora".
        # - El repair loop debe centrarse en:
        #   (1) arreglar harness (conftest) si está roto
        #   (2) si aún así hay 5xx recurrentes por integraciones externas, DEGRADAR endpoints
        #       en `.poc_it/test_plan.json` a OPENAPI_CONTRACT y re-renderizar tests.
        #
        if not is_demo_mode():
            logger.info("[PIPELINE][C] Pytest loop (solo tests/ + pytest.ini)")
        try:
            import json as _json
            import os as _os

            rc = None
            rf = None
            try:
                rc_raw = estructura.get(" .poc_it/runtime_contracts.json".strip()) or ""
                rc = _json.loads(rc_raw) if isinstance(rc_raw, str) and rc_raw.strip() else None
            except Exception:
                rc = None

            try:
                rf_raw = estructura.get(" .poc_it/runtime_facts.json".strip()) or ""
                rf = _json.loads(rf_raw) if isinstance(rf_raw, str) and rf_raw.strip() else None
            except Exception:
                rf = None

            max_repairs = int(_os.getenv("TESTS_PYTEST_REPAIR_MAX", "3"))
            rep = repair_tests_until_pytest_passes(
                nombre_proyecto=nombre_proyecto,
                project_dir=project_dir,
                estructura=estructura,
                max_repairs=max_repairs,
                runtime_contracts=rc,
                runtime_facts=rf,
            )

            if is_demo_mode():
                # Resumen compacto para demo: solo OK/FAIL + conteo desde junit xml.
                try:
                    demo_progress.info(f"Pytest: {'OK' if rep.ok else 'FAIL'}")
                except Exception:
                    pass
                try:
                    from poc_it.orquestacion.pytest_llm_repair import (
                        _extract_counts_from_junit_xml,
                        _read_pytest_junit_xml,
                    )

                    xml = _read_pytest_junit_xml(project_dir)
                    counts = _extract_counts_from_junit_xml(xml)
                    if counts:
                        errors, failures, passed, skipped, total = counts
                        demo_progress.info(
                            f"Resumen tests: total={total} pasados={passed} fallidos={failures} errores={errors} omitidos={skipped}"
                        )
                except Exception:
                    pass

            # Propagar resultado estructurado de pytest a `resultado` (fuente de verdad para publicación)
            try:
                if isinstance(resultado, dict):
                    resultado["pytest_repair"] = {
                        "ok": bool(rep.ok),
                        "attempts": int(rep.attempts),
                        "degraded": bool(getattr(rep, "degraded", False)),
                        "degrade_type": getattr(rep, "degrade_type", None),
                        "artifacts": dict(getattr(rep, "artifacts", {}) or {}),
                    }
            except Exception:
                pass

            if not rep.ok:
                logger.info("[TESTS] Pytest repair loop agotado; pytest sigue fallando.")

                # Intento determinista adicional: degradar plan y re-renderizar tests.
                # Esto evita que el sistema quede en estado "pytest rojo" por un endpoint no hermetizable.
                try:
                    # reconstruir plan a partir del runtime_contracts actual
                    modo = None
                    try:
                        modo = str((resultado or {}).get("clasificacion") or (resultado or {}).get("modo") or "").strip() or None
                    except Exception:
                        modo = None
                    if not modo and isinstance((resultado or {}).get("spec"), dict):
                        modo = str((resultado.get("spec") or {}).get("modo") or (resultado.get("spec") or {}).get("mode") or "").strip() or None

                    plan2 = build_test_plan(
                        structure=estructura,
                        spec=(resultado.get("spec") if isinstance(resultado.get("spec"), dict) else None),
                        mode=str(modo or "PARCIAL"),
                    )

                    # Si el plan2 no cambia nada, no insistimos.
                    # Aun así lo persistimos para que el usuario vea la decisión.
                    estructura2 = persist_test_plan(structure=estructura, plan=plan2)
                    patch_plan = {TEST_PLAN_PATH: estructura2.get(TEST_PLAN_PATH, "")}

                    materializar_proyecto(
                        nombre_proyecto=nombre_proyecto,
                        estructura=patch_plan,
                        limpiar_directorio=False,
                    )
                    estructura.update(patch_plan)

                    # Re-render tests desde plan2 (via generacion_tests ya usado en fase B).
                    # Nota: este "re-render" es local al proyecto y no usa LLM.
                    try:
                        from poc_it.orquestacion.contract_test_renderer import render_tests_from_test_plan

                        tests_patch = render_tests_from_test_plan(
                            structure=estructura2,
                            runtime_contracts=rc if isinstance(rc, dict) else None,
                            runtime_facts=rf if isinstance(rf, dict) else None,
                        )
                        if tests_patch:
                            materializar_proyecto(
                                nombre_proyecto=nombre_proyecto,
                                estructura=tests_patch,
                                limpiar_directorio=False,
                            )
                            estructura.update(tests_patch)

                            rep3 = repair_tests_until_pytest_passes(
                                nombre_proyecto=nombre_proyecto,
                                project_dir=project_dir,
                                estructura=estructura,
                                max_repairs=max_repairs,
                                runtime_contracts=rc,
                                runtime_facts=rf,
                            )
                            if rep3.ok:
                                rep = rep3
                    except Exception:
                        pass
                except Exception:
                    pass

                # ------------------------------------------------------------
                # Escalado pragmático (LLM-driven): si pytest no converge,
                # intentar 1 safe-repair adicional sobre CÓDIGO usando pytest output
                # como runtime_detail, con rollback + gates.
                #
                # No usa fixers deterministas. Solo LLM + allowlist + gates.
                #
                # Feature flag:
                #   POC_IT_SAFE_LLM_REPAIR_FROM_PYTEST=1
                # ------------------------------------------------------------
                safe_from_pytest = str(os.getenv("POC_IT_SAFE_LLM_REPAIR_FROM_PYTEST", "")).strip() in (
                    "1",
                    "true",
                    "True",
                    "yes",
                    "YES",
                )
                if safe_enabled and safe_from_pytest and (rep.last_output or "").strip():
                    snapshot = dict(estructura)

                    # Best-effort: ampliar allowlist con endpoints conocidos (runtime_contracts)
                    # para que el safe repair no dependa solo del traceback de pytest (a veces solo muestra tests/*).
                    extra_allow = []
                    try:
                        if isinstance(rc, dict):
                            for ep in (rc.get("endpoints") or []):
                                if isinstance(ep, dict):
                                    mp = str(ep.get("module_path") or "").strip()
                                    if mp:
                                        extra_allow.append(mp.replace("\\", "/"))
                    except Exception:
                        extra_allow = []

                    guardrails_warnings = None
                    try:
                        guardrails_warnings = (resultado.get("guardrails") or {}).get("warnings")
                    except Exception:
                        guardrails_warnings = None

                    sr = run_llm_safe_repair(
                        config=safe,
                        project_dir=project_dir,
                        spec=resultado.get("spec") if isinstance(resultado.get("spec"), dict) else None,
                        estructura=estructura,
                        runtime_detail=rep.last_output,
                        guardrails_warnings=guardrails_warnings if isinstance(guardrails_warnings, list) else None,
                        extra_allow_paths=extra_allow if extra_allow else None,
                    )

                    if sr.ok and sr.patch:
                        patch = sr.patch
                        materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch)
                        estructura.update(patch)
                        archivos_creados.extend([os.path.join(project_dir, p.replace("/", os.sep)) for p in patch.keys()])

                        # Gates (mismos que subsección Safe Repair de A)
                        ok2, _detail2 = runtime_verify_fastapi_project(project_dir, resultado.get("spec"))
                        if not ok2:
                            logger.info("[SAFE_FROM_PYTEST] Patch rechazado por runtime_verify. Rollback.")
                            estructura.clear()
                            estructura.update(snapshot)
                            try:
                                materializar_proyecto(
                                    nombre_proyecto=nombre_proyecto,
                                    estructura=snapshot,
                                    limpiar_directorio=False,
                                )
                            except Exception:
                                pass
                        else:
                            w2 = verify_wiring_against_runtime_contracts(project_dir=project_dir, estructura=estructura)
                            if not w2.ok:
                                logger.info("[SAFE_FROM_PYTEST] Patch rechazado por wiring_verify. Rollback.")
                                estructura.clear()
                                estructura.update(snapshot)
                                try:
                                    materializar_proyecto(
                                        nombre_proyecto=nombre_proyecto,
                                        estructura=snapshot,
                                        limpiar_directorio=False,
                                    )
                                except Exception:
                                    pass
                            else:
                                # request-time probe best-effort (PARCIAL); enriquece artefactos pero no bloquea.
                                try:
                                    _ = run_runtime_probe(project_dir=project_dir, estructura=estructura, max_endpoints=3)
                                except Exception:
                                    pass

                                # Reintentar pytest loop una única vez extra
                                rep2 = repair_tests_until_pytest_passes(
                                    nombre_proyecto=nombre_proyecto,
                                    project_dir=project_dir,
                                    estructura=estructura,
                                    max_repairs=max_repairs,
                                    runtime_contracts=rc,
                                    runtime_facts=rf,
                                )
                                if rep2.ok:
                                    logger.info(
                                        "[SAFE_FROM_PYTEST] Pytest pasó tras reparación de código guiada por pytest."
                                    )
                                else:
                                    logger.info(
                                        "[SAFE_FROM_PYTEST] Pytest sigue fallando tras reparación de código guiada por pytest."
                                    )
                    else:
                        logger.info("[SAFE_FROM_PYTEST] No se pudo producir patch seguro guiado por pytest.")
        except Exception as exc:
            logger.info("[TESTS] Aviso: pytest repair loop no ejecutable: %s", exc)
    elif regenerar_tests and not code_ok:
        logger.info("[PIPELINE] Skip tests: el código no es importable/wireable tras reparaciones (code_ok=False).")
