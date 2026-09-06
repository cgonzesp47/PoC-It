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
from poc_it.runtime.poc_runtime_environment import prepare_poc_runtime_environment
from poc_it.testing.test_generation_service import ensure_generic_requirements_dev
from poc_it.orquestacion.generacion_tests import generar_tests_unitarios
from poc_it.orquestacion.pytest_llm_repair import repair_tests_until_pytest_passes
from poc_it.orquestacion.test_plan import TEST_PLAN_PATH, build_test_plan, persist_test_plan
from poc_it.orquestacion.wiring_verifier import verify_wiring_against_runtime_contracts
from poc_it.orquestacion.runtime_probe import run_runtime_probe
from poc_it.entrada.demo_progress import demo_progress, is_demo_mode
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH

logger = logging.getLogger(__name__)


def _resolve_dotted_import_to_repo_path(dotted: str, estructura: Dict[str, str]) -> Optional[str]:
    """
    Convierte un import punteado tipo "app.integrations.google_drive_api.build_client" en el
    path real del fichero dentro de `estructura` (p.ej. "app/integrations/google_drive_api.py"),
    recortando progresivamente el último segmento (normalmente el símbolo importado, no parte
    del path) hasta encontrar una coincidencia real. Evita adivinar un path que no exista en el
    proyecto generado.
    """
    parts = [p for p in dotted.strip().split(".") if p]
    while parts:
        candidate = "/".join(parts) + ".py"
        if candidate in estructura:
            return candidate
        parts = parts[:-1]
    return None


def _extra_allow_paths_from_runtime_contracts(rc: Optional[dict], estructura: Dict[str, str]) -> list[str]:
    """
    Deriva paths adicionales para el allowlist de Safe LLM Repair a partir de
    `runtime_contracts`: no solo el módulo del propio endpoint (`module_path`), sino también los
    módulos de los que depende vía Depends() (`depends_imports`, p.ej.
    "app.integrations.google_drive_api.build_client").

    Sin esto, cuando el bug real vive en un módulo de integración inyectado (no en el endpoint
    que lo consume), ese módulo nunca entra en el allowlist y Safe LLM Repair queda
    estructuralmente incapaz de tocar el fichero correcto sin importar cuántos intentos haga
    -- el diagnóstico puede señalarlo correctamente y aun así el patch step lo descarta porque
    "files_to_change debe ser SUBCONJUNTO de allowlist".
    """
    out: list[str] = []
    if not isinstance(rc, dict):
        return out
    for ep in rc.get("endpoints") or []:
        if not isinstance(ep, dict):
            continue
        mp = str(ep.get("module_path") or "").strip()
        if mp:
            out.append(mp.replace("\\", "/"))
        for dotted in ep.get("depends_imports") or []:
            resolved = _resolve_dotted_import_to_repo_path(str(dotted), estructura)
            if resolved:
                out.append(resolved)
    seen: set = set()
    deduped: list[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def _resolve_require_probe_ok() -> bool:
    """
    Resuelve si el probe request-time (Fase A) debe exigirse (probe.ok=False -> dispara
    reparación), incluso en modo PARCIAL.

    Por defecto: exigido (True). Antes esto era opt-in explícito ("1"/"true") y por defecto se
    ignoraba en PARCIAL, lo que dejaba pasar a la fase de tests código que ya reventaba con una
    petición real sintética. Para volver al comportamiento anterior (best-effort, no bloquea):
    exporta POC_IT_SAFE_LLM_REPAIR_REQUIRE_PROBE_OK=0.
    """
    return str(os.getenv("POC_IT_SAFE_LLM_REPAIR_REQUIRE_PROBE_OK", "1")).strip() not in (
        "0",
        "false",
        "False",
        "no",
        "NO",
    )


def _persist_openapi_into_runtime_contracts(
    *, estructura: Dict[str, str], openapi: Optional[dict]
) -> bool:
    """Guarda el OpenAPI REAL (capturado en vivo de la app vía TestClient durante wiring
    verify) dentro de `.poc_it/runtime_contracts.json`.

    Por qué: es la única fuente fiable de status codes / response shapes reales. Sin esto, el
    planificador de tests (poc_it.testing.planning.test_plan_builder) nunca sabe qué status
    "feliz" esperar por endpoint (los decoradores de FastAPI rara vez declaran `status_code=`
    explícito) y ningún caso puede promocionar más allá de OPENAPI_CONTRACT, incluso para
    endpoints triviales sin ninguna dependencia externa.

    Devuelve True si `estructura[RUNTIME_CONTRACTS_PATH]` cambió (para que el caller decida si
    materializar a disco).
    """
    if not isinstance(openapi, dict) or not openapi:
        return False

    import json as _json

    raw = estructura.get(RUNTIME_CONTRACTS_PATH) or ""
    try:
        rc = _json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except Exception:
        rc = {}
    if not isinstance(rc, dict):
        rc = {}

    if rc.get("openapi") == openapi:
        return False

    rc["openapi"] = openapi
    estructura[RUNTIME_CONTRACTS_PATH] = _json.dumps(rc, ensure_ascii=False, indent=2)
    return True


def _is_demo_mode() -> bool:
    # compat con lógica antigua en este módulo
    return is_demo_mode()

_MISSING_MODULE_RE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")


def _extraer_modulo_faltante(runtime_detail: str) -> Optional[str]:
    if not runtime_detail:
        return None
    m = _MISSING_MODULE_RE.search(runtime_detail)
    if not m:
        return None
    return (m.group(1) or "").strip() or None


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
    # Historial de intentos de Safe LLM Repair a través de TODAS las rondas exteriores de este
    # mismo `ejecutar_reparacion_runtime` (import-time -> wiring -> probe). Cada ronda exterior
    # llamaba a `run_llm_safe_repair` desde cero, sin ninguna pista de qué parche ya se había
    # aplicado de verdad y por qué la re-verificación externa (import real/wiring/petición real)
    # lo había rechazado — así que el LLM podía repetir el mismo intento fallido, o oscilar entre
    # 2 intentos igual de erróneos, ronda tras ronda.
    safe_repair_history: list[Dict[str, Any]] = []

    if is_demo_mode():
        demo_progress.step(6, 8, "Verificación y reparación automática")
        demo_progress.info("Ejecutando verificación de runtime e intentando autoreparación...")
    else:
        logger.info(
            "[PIPELINE][A] Code correctness loop (max=%s): import-time + wiring(OpenAPI) + request-time probe",
            max_runtime_repairs,
        )

    # Asegurar entorno hermético (venv aislado con SOLO las deps de requirements.txt) para
    # runtime verify/probe/pytest. Esto es lo que separa el entorno de PoC-it del entorno de
    # ejecución de la PoC generada:
    #
    #   entorno de PoC-it  !=  entorno de la PoC generada
    #
    # `runtime_environment_status` (ready/venv_create_failed/venv_validation_failed/
    # dependency_install_failed) es un eje INDEPENDIENTE de
    # `codegen_status`. Un fallo de `pip install` (red/PyPI) nunca debe convertirse
    # automáticamente en `codegen_status=invalid`: solo significa que no pudimos ejecutar
    # runtime/tests para verificar un codegen que, en principio, ya es válido.
    #
    # Guardrail: normalizar `project_dir` para evitar paths corruptos (observado: duplicación
    # .../output/<name>/output/<name>), que rompe venv/ensurepip y runtime probes.
    project_dir = os.path.normpath(project_dir)

    # Bootstrap temprano de `requirements-dev.txt` (solo la base genérica de test harness:
    # pytest/httpx/httpx2/...), ANTES de preparar el venv.
    #
    # Por qué: los propios probes internos de PoC-it (runtime_verify_fastapi_project,
    # verify_wiring_against_runtime_contracts, run_runtime_probe) usan
    # `fastapi.testclient.TestClient`, que requiere un cliente HTTP (httpx/httpx2) instalado en
    # el venv. Esos probes se ejecutan en el loop de abajo (Fase A), ANTES de que Fase B genere
    # la suite de tests real y materialice `requirements-dev.txt`. Sin este bootstrap, la
    # primera ejecución de cualquier probe fallaría con un error de tooling
    # ("TestClient requires httpx2") ajeno al código generado.
    #
    # No pisa un `requirements-dev.txt` ya existente (materializado en un intento previo).
    try:
        from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH

        rc_for_bootstrap = None
        rc_raw = estructura.get(RUNTIME_CONTRACTS_PATH) or ""
        if isinstance(rc_raw, str) and rc_raw.strip():
            import json as _json

            rc_for_bootstrap = _json.loads(rc_raw)

        if ensure_generic_requirements_dev(estructura, resultado, runtime_contracts=rc_for_bootstrap):
            materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura={"requirements-dev.txt": estructura["requirements-dev.txt"]},
                limpiar_directorio=False,
            )
            archivos_creados.append(os.path.join(project_dir, "requirements-dev.txt"))
    except Exception:
        logger.exception(
            "[RUNTIME_ENV] No se pudo preparar requirements-dev.txt (bootstrap genérico); "
            "los probes basados en TestClient pueden fallar por falta de httpx/httpx2."
        )

    runtime_env = prepare_poc_runtime_environment(project_dir)
    python_executable = str(runtime_env.python_executable)

    if isinstance(resultado, dict):
        resultado["runtime_environment_status"] = runtime_env.runtime_environment_status
        resultado["runtime_environment_debug"] = {
            "venv_dir": str(runtime_env.venv_dir),
            "python_executable": python_executable,
            "requirements_hash": runtime_env.requirements_hash,
            "install_attempted": runtime_env.install_attempted,
            "install_exit_code": runtime_env.install_exit_code,
            "detail": runtime_env.detail,
        }

    if runtime_env.runtime_environment_status != "ready":
        logger.error(
            "[RUNTIME_ENV] No se pudo preparar el entorno aislado del proyecto (%s): %s\n"
            "stdout:\n%s\nstderr:\n%s",
            project_dir,
            runtime_env.detail,
            runtime_env.install_stdout_summary,
            runtime_env.install_stderr_summary,
        )
        # Persistimos un artefacto legible dentro del proyecto (el usuario puede no ver logs).
        try:
            msg = (
                "No se pudo preparar el entorno de ejecución aislado (.poc_it/venv) para esta PoC.\n"
                f"Detalle: {runtime_env.detail}\n\n"
                "IMPORTANTE: esto NO significa que el codegen (requirements.txt/código) sea inválido.\n"
                "Puede deberse a falta de red, PyPI no accesible, o una versión de paquete inexistente.\n"
                "Revisa requirements.txt y vuelve a intentarlo; el codegen se conserva tal cual.\n\n"
                f"install_exit_code={runtime_env.install_exit_code}\n"
                f"stdout:\n{runtime_env.install_stdout_summary}\n\n"
                f"stderr:\n{runtime_env.install_stderr_summary}\n"
            )
            patch = {"RUNTIME_ENVIRONMENT_SETUP_ERROR.txt": msg}
            materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch, limpiar_directorio=False)
            estructura.update(patch)
            archivos_creados.append(os.path.join(project_dir, "RUNTIME_ENVIRONMENT_SETUP_ERROR.txt"))
        except Exception:
            pass

        if isinstance(resultado, dict):
            resultado["runtime_ok"] = False
            resultado["runtime_tests_status"] = "skipped_environment_setup_failed"
        return

    for attempt in range(max_runtime_repairs + 1):
        ok_runtime, detail = runtime_verify_fastapi_project(
            project_dir, resultado.get("spec"), python_executable=python_executable
        )
        if ok_runtime:
            # Verificación adicional determinista: wiring mínimo vía OpenAPI
            w = verify_wiring_against_runtime_contracts(project_dir=project_dir, estructura=estructura, python_executable=python_executable)
            if not w.ok:
                ok_runtime = False
                detail = "RUNTIME_WIRING_VERIFY_FAILED\n" + (w.detail or "")
            elif _persist_openapi_into_runtime_contracts(estructura=estructura, openapi=w.openapi):
                materializar_proyecto(
                    nombre_proyecto=nombre_proyecto,
                    estructura={RUNTIME_CONTRACTS_PATH: estructura[RUNTIME_CONTRACTS_PATH]},
                    limpiar_directorio=False,
                )

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
            probe = run_runtime_probe(project_dir=project_dir, estructura=estructura, python_executable=python_executable)

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
                    # En PARCIAL no cortamos aquí mismo: el gate de abajo (require_probe_ok,
                    # activado por defecto) decide si esto debe disparar reparación o solo
                    # quedar registrado. Ver POC_IT_SAFE_LLM_REPAIR_REQUIRE_PROBE_OK.
                    logger.info(
                        "[PIPELINE][A] Runtime request-probe falló en PARCIAL. Detalle: %s",
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
        #   POC_IT_SAFE_LLM_REPAIR_REQUIRE_PROBE_OK  (ver _resolve_require_probe_ok)
        #
        # Nota: NO usa fixers deterministas. Solo LLM + gates + rollback.
        # ------------------------------------------------------------
        # Safe repair es el camino principal (feature-flag solo para desactivar).
        # Objetivo: evitar el repair legacy sin gates/rollback cuando hay fallos import-time típicos.
        safe_disabled = str(os.getenv("POC_IT_SAFE_LLM_REPAIR", "")).strip() in ("0", "false", "False", "no", "NO")
        safe_enabled = not safe_disabled
        safe_max = int(os.getenv("POC_IT_SAFE_LLM_REPAIR_MAX", "2") or "2")
        require_probe_ok = _resolve_require_probe_ok()

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

            # Best-effort: ampliar allowlist con los module_path de los endpoints conocidos y
            # los módulos de los que dependen vía Depends() (runtime_contracts). IMPORTANTE
            # para el caso RUNTIME_REQUEST_PROBE_FAILED: el `detail` ahí es el cuerpo JSON de
            # la respuesta 500 (lo que el propio endpoint capturó y serializó con `str(e)`), NO
            # un traceback de Python con "File ..., line ...", así que `_build_allowlist` no
            # puede extraer ningún path del traceback y el safe repair se rendía sin ni
            # siquiera intentar un diagnóstico ("allowlist vacío"). Incluir también
            # `depends_imports` es necesario porque el bug real a menudo vive en el módulo de
            # integración inyectado (p.ej. app/integrations/*.py), no en el propio endpoint.
            extra_allow: list[str] = []
            try:
                rc_raw_extra = estructura.get(RUNTIME_CONTRACTS_PATH) or ""
                if isinstance(rc_raw_extra, str) and rc_raw_extra.strip():
                    import json as _json

                    rc_extra = _json.loads(rc_raw_extra)
                    extra_allow = _extra_allow_paths_from_runtime_contracts(
                        rc_extra if isinstance(rc_extra, dict) else None, estructura
                    )
            except Exception:
                extra_allow = []

            sr = run_llm_safe_repair(
                config=safe,
                project_dir=project_dir,
                spec=resultado.get("spec") if isinstance(resultado.get("spec"), dict) else None,
                estructura=estructura,
                runtime_detail=detail,
                guardrails_warnings=guardrails_warnings if isinstance(guardrails_warnings, list) else None,
                extra_allow_paths=extra_allow if extra_allow else None,
                previous_attempts=safe_repair_history if safe_repair_history else None,
                outer_attempt=attempt,
            )

            if sr.ok and sr.patch:
                patch = sr.patch
                runtime_repaired = True
                materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch, limpiar_directorio=False)
                estructura.update(patch)
                archivos_creados.extend([os.path.join(project_dir, p.replace("/", os.sep)) for p in patch.keys()])

                # Gate 1: import-time verify again
                ok2, detail2 = runtime_verify_fastapi_project(project_dir, resultado.get("spec"), python_executable=python_executable)
                if not ok2:
                    logger.info("[SAFE_LLM_REPAIR] Patch rechazado por runtime_verify. Rollback.")
                    safe_repair_history.append({"patch": patch, "reason": "RUNTIME_VERIFY_FAILED\n" + (detail2 or "")})
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
                    w2 = verify_wiring_against_runtime_contracts(project_dir=project_dir, estructura=estructura, python_executable=python_executable)
                    if not w2.ok:
                        logger.info("[SAFE_LLM_REPAIR] Patch rechazado por wiring_verify. Rollback.")
                        safe_repair_history.append(
                            {"patch": patch, "reason": "RUNTIME_WIRING_VERIFY_FAILED\n" + (w2.detail or "")}
                        )
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
                        if _persist_openapi_into_runtime_contracts(estructura=estructura, openapi=w2.openapi):
                            materializar_proyecto(
                                nombre_proyecto=nombre_proyecto,
                                estructura={RUNTIME_CONTRACTS_PATH: estructura[RUNTIME_CONTRACTS_PATH]},
                                limpiar_directorio=False,
                            )
                        # Gate 3: request-time probe (best-effort salvo flag require_probe_ok)
                        probe2 = run_runtime_probe(project_dir=project_dir, estructura=estructura, python_executable=python_executable)
                        if require_probe_ok and not probe2.ok:
                            logger.info("[SAFE_LLM_REPAIR] Patch rechazado por request-probe. Rollback.")
                            safe_repair_history.append(
                                {"patch": patch, "reason": "RUNTIME_REQUEST_PROBE_FAILED\n" + (probe2.detail or "")}
                            )
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
                materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch, limpiar_directorio=False)
                estructura.update(patch)
                archivos_creados.extend([os.path.join(project_dir, "RUNTIME_VERIFY_ERROR.txt")])
            except Exception as exc:
                logger.info("[RUNTIME_REPAIR] No se pudo materializar RUNTIME_VERIFY_ERROR.txt: %s", exc)

            # Marcar explícitamente que no se pudo completar runtime_verify (ayuda a la política del orquestador)
            try:
                if isinstance(resultado, dict):
                    resultado["runtime_ok"] = False
            except Exception:
                pass

            # IMPORTANTE: si el caller NO quiere que se generen tests (`regenerar_tests=False`),
            # aquí es donde termina todo, así que dejamos un suite mínimo (smoke_import +
            # openapi) para que el proyecto tenga algo coherente que mostrar.
            #
            # Si SÍ quiere tests (`regenerar_tests=True`, el caso real de producción), NO
            # degradamos aquí de forma preventiva: dejamos que la Fase B genere el test PROFUNDO
            # de verdad. Antes, este `return` cortaba la función entera y la Fase B nunca se
            # llegaba a ejecutar cuando `code_ok=False` — el mismo bug roto que el probe ya había
            # detectado se colaba sin que ningún test profundo llegara a mostrarlo fallando.
            if not regenerar_tests:
                try:
                    from poc_it.orquestacion.pytest_llm_repair import _degrade_to_contract_lite

                    patch_min = _degrade_to_contract_lite(nombre_proyecto=nombre_proyecto, estructura=estructura)
                    materializar_proyecto(
                        nombre_proyecto=nombre_proyecto,
                        estructura=patch_min,
                        limpiar_directorio=False,
                    )
                    estructura.update(patch_min)
                    archivos_creados.extend(
                        [os.path.join(project_dir, p.replace("/", os.sep)) for p in patch_min.keys()]
                    )
                except Exception:
                    pass
                return

        missing_mod = _extraer_modulo_faltante(detail)

        # Diagnóstico (sin adivinar el paquete instalable).
        #
        # IMPORTANTE: aquí NO se hace `pypi_pkg = missing_mod` ni se muta
        # `estructura["requirements.txt"]` / `resultado["spec"]["dependencies"]` a partir de un
        # ModuleNotFoundError de runtime. El nombre de un módulo Python no tiene por qué coincidir
        # con el nombre del paquete instalable en PyPI:
        #
        #     google            != google-api-python-client
        #     yaml              != PyYAML
        #     PIL               != Pillow
        #     pydantic_settings != pydantic-settings
        #
        # Adivinar produce requirements.txt incorrectos. La única fuente de verdad para paquetes
        # instalables es `TechnologySignal.packages` (SPEC), no el traceback de runtime.
        #
        # Si requirements.txt está realmente incompleto, eso es un defecto de generación de
        # dependencias que el gate `DEPENDENCY_IMPORT_NOT_DECLARED`
        # (poc_it.generador.file_contracts_validation) debería haber atrapado ANTES de
        # materializar el proyecto, no algo que se repare adivinando aquí.
        if missing_mod:
            if last_missing_module == missing_mod and llm_attempted_for_missing:
                logger.warning(
                    "[RUNTIME_REPAIR] ModuleNotFoundError persistente tras intento LLM: '%s'. "
                    "No se adivina el paquete PyPI correspondiente ni se muta requirements.txt/"
                    "spec.dependencies automáticamente.",
                    missing_mod,
                )
            else:
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
            materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch, limpiar_directorio=False)
            estructura.update(patch)
            archivos_creados.extend([os.path.join(project_dir, p.replace("/", os.sep)) for p in patch.keys()])

    # Fase B: generación de tests (LLM).
    # Nota: aquí NO se repara código. Solo se generan tests.
    #
    # IMPORTANTE: se ejecuta SIEMPRE que `regenerar_tests`, incluso si `code_ok=False` (el
    # código no superó import-time/wiring/probe tras agotar las reparaciones automáticas).
    #
    # Por qué: antes, si `code_ok` quedaba en False, esta fase se saltaba por completo
    # ("Skip tests: el código no es importable/wireable..."), así que un bug real detectado por
    # el probe pero que la reparación automática no consiguió arreglar terminaba en el MISMO
    # resultado que el problema original que este pipeline intenta evitar: código roto + solo
    # tests superficiales (contract-lite) — pero encima gastando varias rondas de reparación por
    # el camino. Dejar que el test profundo se genere igualmente permite que, si el bug persiste,
    # falle de forma visible y accionable (un assert con el motivo real) en vez de desaparecer
    # silenciosamente. El coste (una generación de test que podría degradar) ya se pagaba antes
    # de tener el probe; ahora, además, tenemos el detalle exacto del fallo en
    # `output/_debug/safe_repair_*.json` para diagnosticarlo.
    if regenerar_tests:
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

        # Re-preparar el entorno aislado DESPUÉS de generar tests: `generar_tests_unitarios`
        # puede materializar `requirements-dev.txt` (deps de test harness) por primera vez, o
        # modificarlo. Si no repetimos `prepare_poc_runtime_environment` aquí, pytest se
        # ejecutaría con un venv que nunca llegó a instalar esas dependencias.
        #
        # El fingerprint (`.poc_it/runtime_requirements.sha256`) decide si hace falta reinstalar:
        # si requirements.txt + requirements-dev.txt no cambiaron, esta llamada es un no-op.
        runtime_env = prepare_poc_runtime_environment(project_dir)
        python_executable = str(runtime_env.python_executable)
        if isinstance(resultado, dict):
            resultado["runtime_environment_status"] = runtime_env.runtime_environment_status
            resultado["runtime_environment_debug"] = runtime_env.to_debug_dict()

        if runtime_env.runtime_environment_status != "ready":
            logger.error(
                "[RUNTIME_ENV] No se pudo preparar el entorno tras generar tests/requirements-dev.txt (%s): %s",
                project_dir,
                runtime_env.detail,
            )
            try:
                msg = (
                    "No se pudo preparar el entorno de ejecución aislado (.poc_it/venv) tras "
                    "generar tests/requirements-dev.txt.\n"
                    f"Detalle: {runtime_env.detail}\n\n"
                    "IMPORTANTE: esto NO significa que el codegen sea inválido.\n\n"
                    f"install_exit_code={runtime_env.install_exit_code}\n"
                    f"stdout:\n{runtime_env.install_stdout_summary}\n\n"
                    f"stderr:\n{runtime_env.install_stderr_summary}\n"
                )
                patch = {"RUNTIME_ENVIRONMENT_SETUP_ERROR.txt": msg}
                materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch, limpiar_directorio=False)
                estructura.update(patch)
                archivos_creados.append(os.path.join(project_dir, "RUNTIME_ENVIRONMENT_SETUP_ERROR.txt"))
            except Exception:
                pass

            if isinstance(resultado, dict):
                resultado["runtime_tests_status"] = "skipped_environment_setup_failed"
            return

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
                python_executable=python_executable,
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
                                python_executable=python_executable,
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

                    # Best-effort: ampliar allowlist con endpoints conocidos y sus módulos de
                    # dependencia (Depends()) vía runtime_contracts, para que el safe repair no
                    # dependa solo del traceback de pytest (a veces solo muestra tests/*) ni se
                    # quede sin poder tocar el módulo de integración donde vive el bug real.
                    extra_allow = []
                    try:
                        extra_allow = _extra_allow_paths_from_runtime_contracts(
                            rc if isinstance(rc, dict) else None, estructura
                        )
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
                        previous_attempts=safe_repair_history if safe_repair_history else None,
                        # 100+: marca los artefactos de depuración como del camino "disparado por
                        # pytest" (no iterado), distinto del bucle principal (outer_attempt 0..N).
                        outer_attempt=100,
                    )

                    if sr.ok and sr.patch:
                        patch = sr.patch
                        materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch, limpiar_directorio=False)
                        estructura.update(patch)
                        archivos_creados.extend([os.path.join(project_dir, p.replace("/", os.sep)) for p in patch.keys()])

                        # Gates (mismos que subsección Safe Repair de A)
                        ok2, _detail2 = runtime_verify_fastapi_project(project_dir, resultado.get("spec"), python_executable=python_executable)
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
                            w2 = verify_wiring_against_runtime_contracts(project_dir=project_dir, estructura=estructura, python_executable=python_executable)
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
                                if _persist_openapi_into_runtime_contracts(estructura=estructura, openapi=w2.openapi):
                                    materializar_proyecto(
                                        nombre_proyecto=nombre_proyecto,
                                        estructura={RUNTIME_CONTRACTS_PATH: estructura[RUNTIME_CONTRACTS_PATH]},
                                        limpiar_directorio=False,
                                    )
                                # request-time probe best-effort (PARCIAL); enriquece artefactos pero no bloquea.
                                try:
                                    _ = run_runtime_probe(project_dir=project_dir, estructura=estructura, python_executable=python_executable)
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
                                    python_executable=python_executable,
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
