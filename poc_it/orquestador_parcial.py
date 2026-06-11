"""
PoC-it – Orquestador Libre (modo generación completa)

Nuevo enfoque:

- Eliminamos generación parcial fragmentada.
- Eliminamos proyecto base rígido.
- Eliminamos expansión por bloques.
- Delegamos completamente en el LLM la generación total.
- Validación sintáctica ya gestionada en generador_artefactos.

Flujo nuevo:

1. Generar proyecto completo vía LLM.
2. Materializar archivos.
3. Retornar resumen.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Tuple

from poc_it.demo_progress import demo_progress, is_demo_mode

from poc_it.clasificador import clasificar_viabilidad
from poc_it.generador_artefactos import generar_proyecto_completo, generar_proyecto_desde_spec
from poc_it.materializador_archivos import materializar_proyecto
from poc_it.models import ContextoNormalizado, ModoGeneracion, PlantillaUsuario, ProjectContext
from poc_it.normalizador_contexto import normalizar_plantilla
from poc_it.orquestacion.generacion_documentacion import generar_documentacion
from poc_it.orquestacion.persistencia_spec import persist_spec_json
from poc_it.orquestacion.postprocesado_alineacion import postprocesar_alineacion_por_pytest
from poc_it.orquestacion.constantes import OUTPUT_DIRNAME, README_ERROR_FILENAME, README_FINAL_FILENAME
from poc_it.orquestacion.generacion_tests import generar_tests_unitarios
from poc_it.orquestacion.reparacion_runtime import ejecutar_reparacion_runtime
from poc_it.orquestacion.run_result import RunResult
from poc_it.poc_facts_extractor import extract_poc_facts_from_structure
from poc_it.runtime_contracts import (
    EndpointRuntimeContract,
    ObservedCall,
    RuntimeContracts,
    persist_runtime_contracts,
)
from poc_it.runtime_facts import (
    EndpointRuntimeFacts,
    RuntimeFacts,
    persist_runtime_facts,
)

# Cachea el contexto normalizado para poder reutilizarlo en estimaciones (sin recalcular inputs).
# Nota: se inicializa en _normalizar_contexto.
ContextoNormalizadoCache = ContextoNormalizado | None

logger = logging.getLogger(__name__)


class OrquestadorParcial:
    """
    Orquestador libre basado en generación completa.
    """

    # ======================================================
    # 🔹 MÉTODOS PRIVADOS DE ESTIMACIÓN (MODULARIZADOS)
    # ======================================================

    def _estimacion_generada(self, modo: str, horas: float, *, spec: Dict[str, Any] | None = None) -> Any:
        from poc_it.estimador_esfuerzo import calcular_estimacion_esfuerzo

        descripcion = f"""
Proyecto: {self.nombre_proyecto}
Modo: {modo}

Descripción:
{self.descripcion_global}
"""
        return calcular_estimacion_esfuerzo(
            descripcion_proyecto=descripcion,
            modo=modo,
            tiempo_real_scopeguardian_horas=horas,
            spec=spec,
            contexto_normalizado=self._contexto_normalizado.model_dump() if self._contexto_normalizado else None,
        )

    def _estimacion_manual(self, *, spec: Dict[str, Any] | None = None) -> Any:
        from poc_it.estimador_esfuerzo import calcular_estimacion_esfuerzo

        return calcular_estimacion_esfuerzo(
            descripcion_proyecto=self.descripcion_global,
            modo=None,
            tiempo_real_scopeguardian_horas=0.0,
            generable=False,
            spec=spec,
            contexto_normalizado=self._contexto_normalizado.model_dump() if self._contexto_normalizado else None,
        )

    def __init__(
        self,
        plantilla: PlantillaUsuario,
        modo_generacion: str,
        context: ProjectContext,
        t_clasificacion_inicio: float | None = None,
        t_clasificacion_fin: float | None = None,
        t_ejecucion_inicio: float | None = None,
    ):
        self.plantilla = plantilla
        self.nombre_proyecto = plantilla.nombre
        self.descripcion_global = plantilla.problema
        self.tecnologias = plantilla.tecnologias
        self.modo_generacion = modo_generacion
        self.context = context
        self._contexto_normalizado = context.contexto_normalizado
        self.t_clasificacion_inicio = t_clasificacion_inicio
        self.t_clasificacion_fin = t_clasificacion_fin
        # Fuente de verdad del tiempo total PoC-it (sin tiempo de input del usuario).
        # Lo inicializa main.py justo después de collect_user_input().
        self.t_ejecucion_inicio = t_ejecucion_inicio

    def _build_context(self) -> ProjectContext:
        context = ProjectContext(plantilla=self.plantilla)
        return context

    def _normalizar_contexto(self, context: ProjectContext) -> None:
        contexto_dict = normalizar_plantilla(self.plantilla)

        try:
            contexto_normalizado = ContextoNormalizado(**contexto_dict)
            context.contexto_normalizado = contexto_normalizado
            self._contexto_normalizado = contexto_normalizado
            context.registrar_modelo("normalizacion_contexto", "chat_completion_json")

            # [2/8] Demo progress
            if is_demo_mode():
                demo_progress.step(2, 8, "Contexto normalizado generado")
                funcionalidades = ", ".join(contexto_normalizado.funcionalidades_clave or []) or "N/D"
                integ_list = contexto_normalizado.integraciones_externas or []
                integ = ", ".join(integ_list) if integ_list else "No"
                contratos = len(contexto_normalizado.contratos_api or [])
                demo_progress.info(f"Funcionalidades detectadas: {funcionalidades}")
                demo_progress.info(f"Integraciones externas: {integ}")
                demo_progress.info(f"Contratos API identificados: {contratos}")
        except Exception:
            context.contexto_normalizado = None
            self._contexto_normalizado = None
            if is_demo_mode():
                demo_progress.step(2, 8, "Contexto normalizado generado")

    def _clasificar(self, context: ProjectContext) -> tuple[ProjectContext, float, float]:
        import time

        t_clasificacion_inicio = time.perf_counter()
        context = clasificar_viabilidad(context)
        t_clasificacion_fin = time.perf_counter()

        if is_demo_mode():
            modo = (context.clasificacion or "").upper() or "N/D"
            demo_progress.step(3, 8, f"Modo seleccionado: {modo}")
            # “Motivo breve”: usar la señal más cercana (complejidad + integraciones)
            cn = context.contexto_normalizado
            integ = ", ".join((cn.integraciones_externas or [])) if cn else ""
            if integ:
                motivo = f"Requiere integraciones externas: {integ}"
            else:
                motivo = "API backend sin integraciones externas obligatorias"
            demo_progress.info(f"Motivo: {motivo}")
        else:
            # Ruido alto para demo: esto imprime mucho contexto interno.
            # Mantener disponible en DEBUG para desarrollo.
            logger.debug("[DEBUG CONTEXT DESPUÉS DE CLASIFICACIÓN]\n%s", context.model_dump_json(indent=2))

        return context, t_clasificacion_inicio, t_clasificacion_fin

    def _generar_y_materializar(
        self, context: ProjectContext, modo_generacion: str
    ) -> tuple[Dict[str, str], list[str], float, Dict[str, Any]]:
        import time

        estructura: Dict[str, str] = {}
        archivos_creados: list[str] = []
        tiempo_generacion_horas = 0.0
        resultado: Dict[str, Any] = {}

        if modo_generacion.upper() == ModoGeneracion.ASESOR:
            return estructura, archivos_creados, tiempo_generacion_horas, resultado

        t_generacion_inicio = time.perf_counter()

        contexto_norm = context.contexto_normalizado.model_dump() if context.contexto_normalizado else None

        resultado = generar_proyecto_completo(
            descripcion_global=self.descripcion_global,
            contexto_normalizado=contexto_norm,
        )

        # Persistimos spec/diagnóstico del primer intento (si existe) antes de decidir el retry.
        persist_spec_json(self.nombre_proyecto, resultado, archivos_creados)

        files = resultado.get("files", [])
        spec = resultado.get("spec") if isinstance(resultado, dict) else None

        # Retry: si tenemos SPEC válido pero la generación de código no converge (files vacío),
        # reintentamos una vez reutilizando el SPEC como fuente de verdad.
        if (not files) and isinstance(spec, dict) and spec:
            logger.warning(
                "[CODEGEN] Generación no convergió pero hay SPEC disponible. Reintentando una vez desde SPEC..."
            )
            resultado = generar_proyecto_desde_spec(
                spec=spec,
                descripcion_global=self.descripcion_global,
                contexto_normalizado=contexto_norm,
                intentos=1,
            )
            persist_spec_json(self.nombre_proyecto, resultado, archivos_creados)
            files = resultado.get("files", [])

        t_generacion_fin = time.perf_counter()
        tiempo_generacion_horas = (t_generacion_fin - t_generacion_inicio) / 3600

        if not files:
            raise ValueError("El modelo no generó archivos válidos (2 intentos: completo + desde SPEC).")

        estructura = {f["path"]: f["content"] for f in files if "path" in f and "content" in f}

        archivos_creados = materializar_proyecto(
            nombre_proyecto=self.nombre_proyecto,
            estructura=estructura,
        )

        if is_demo_mode():
            demo_progress.info(f"Archivos creados: {len(archivos_creados)}")

        return estructura, archivos_creados, tiempo_generacion_horas, resultado

    def _build_fallback_docs(self, exc: Exception) -> Tuple[str, str]:
        import traceback

        fallback_readme = (
            f"# {self.nombre_proyecto}\n\n"
            "## Estado\n\n"
            "La generación no finalizó correctamente. Revisa `README_ERROR.md` para ver el detalle del error.\n"
        )

        fallback_error = (
            f"# {self.nombre_proyecto} – Error de generación\n\n"
            "## Error durante la generación libre\n\n"
            f"Error detectado:\n\n```\n{str(exc)}\n```\n\n"
            "## Traceback\n\n"
            f"```\n{traceback.format_exc()}\n```\n"
        )

        return fallback_readme, fallback_error

    def ejecutar(self) -> Dict[str, Any]:
        """
        Ejecuta el flujo completo incluyendo clasificación basada en ProjectContext.
        """
        try:
            context = self.context
            modo_generacion = (self.modo_generacion or context.clasificacion or "").upper()

            estructura, archivos_creados, tiempo_generacion_horas, resultado = self._generar_y_materializar(
                context=context,
                modo_generacion=modo_generacion,
            )

            # Fuente de verdad para el resto del pipeline:
            # `resultado` (dict) es lo que se propaga a reparacion_runtime/generacion_tests.
            # Si no persistimos el modo aquí, `generacion_tests._is_parcial()` puede ver modo=None
            # (spec no siempre incluye modo) y disparar warnings legacy/fallback mínimo.
            if isinstance(resultado, dict):
                resultado["clasificacion"] = modo_generacion
                resultado["modo"] = modo_generacion
                spec0 = resultado.get("spec")
                if isinstance(spec0, dict):
                    spec0.setdefault("modo", modo_generacion)
                    resultado["spec"] = spec0

            # Medición real del tiempo de PoC-it (pipeline real, sin input del usuario)
            # Fuente de verdad: contador global de main.py (self.inicio_ejecucion) si está disponible.
            # Debe incluir: repairs + tests + docs + parches finales.
            # Tiempo total real de PoC-it:
            # - Fuente de verdad: contador global iniciado en main.py tras collect_user_input()
            # - Fallback: inicio local del orquestador (best-effort)
            t_pocit_inicio_global = float(self.t_ejecucion_inicio or 0.0)
            t_pocit_inicio_local = 0.0
            t_pocit_fin = 0.0
            if modo_generacion != ModoGeneracion.ASESOR:
                import time

                t_pocit_inicio_local = time.perf_counter()

                # Directorio real del proyecto materializado en disco.
                # Fuente de verdad: `materializar_proyecto()` escribe bajo ./output/<nombre_proyecto>.
                #
                # Bug observado en logs: `project_dir` acababa duplicado (…/output/<name>/output/<name>),
                # lo que rompía la creación del venv (.poc_it/venv) y el runtime probe.
                #
                # Normalizamos y "deduplicamos" de forma determinista:
                project_dir = os.path.normpath(os.path.join(OUTPUT_DIRNAME, self.nombre_proyecto))
                expected_suffix = os.path.normpath(os.path.join(OUTPUT_DIRNAME, self.nombre_proyecto))
                double_suffix = os.path.normpath(os.path.join(expected_suffix, expected_suffix))
                if project_dir.endswith(double_suffix):
                    project_dir = os.path.normpath(project_dir[: -len(double_suffix)] + expected_suffix)

                # Persistir artefacto intermedio con facts deterministas del CÓDIGO real
                # para alinear la generación de tests con el wiring/DI realmente materializado.
                try:
                    facts = extract_poc_facts_from_structure(estructura).to_dict()
                    endpoints: list[EndpointRuntimeFacts] = []
                    contracts_eps: list[EndpointRuntimeContract] = []
                    for ep in facts.get("endpoints", []) or []:
                        calls: list[ObservedCall] = []
                        for c in ep.get("observed_calls", []) or []:
                            calls.append(
                                ObservedCall(
                                    receiver_param=str(c.get("receiver_param")),
                                    method_name=str(c.get("method_name")),
                                    arg_names=list(c.get("arg_names") or []),
                                    awaited=bool(c.get("awaited")),
                                )
                            )

                        # normalizar módulos a posix para estabilidad cross-platform
                        module_path = str(ep.get("module_path") or "").replace("\\", "/")

                        # Inferencias pragmáticas para stubs (modo PARCIAL):
                        # - CRUD-like: si hay varias operaciones sobre el mismo receiver (svc.*), se recomienda stub stateful compartido
                        # - not-found: si el endpoint declara response_model con required_fields, el stub no debe devolver modelos vacíos
                        observed = ep.get("observed_calls", []) or []
                        receiver_names = [str(c.get("receiver_param")) for c in observed if isinstance(c, dict)]
                        receiver_is_service = any(r in ("svc", "service") or r.endswith("Service") for r in receiver_names)
                        statefulness_recommended = "per_client_fixture" if receiver_is_service and len(observed) >= 1 else "stateless"

                        returns_none_on_not_found = None
                        not_found_http_status = None
                        # Heurística: si hay response_model_required_fields, un "not found" NO puede ser {} / modelo vacío
                        if (ep.get("response_model_required_fields") or []) and any(
                            c.get("method_name", "").lower().startswith(("get_", "update_", "delete_")) for c in observed if isinstance(c, dict)
                        ):
                            returns_none_on_not_found = True
                            not_found_http_status = 404

                        ep_fact = EndpointRuntimeFacts(
                            path=str(ep.get("path")),
                            method=str(ep.get("method")),
                            module_path=module_path,
                            func_name=str(ep.get("func_name")),
                            status_code=ep.get("status_code"),
                            depends=list(ep.get("depends") or []),
                            depends_imports=list(ep.get("depends_imports") or []),
                            injected_params=list(ep.get("injected_params") or []),
                            uses_injected=bool(ep.get("uses_injected")),
                            observed_calls=calls,
                            request_body_param=ep.get("request_body_param"),
                            request_model=ep.get("request_model"),
                            request_required_fields=list(ep.get("request_required_fields") or []),
                            request_optional_fields=list(ep.get("request_optional_fields") or []),
                            response_model=ep.get("response_model"),
                            response_model_required_fields=list(ep.get("response_model_required_fields") or []),
                            response_model_optional_fields=list(ep.get("response_model_optional_fields") or []),
                            statefulness_recommended=statefulness_recommended,
                            returns_none_on_not_found=returns_none_on_not_found,
                            not_found_http_status=not_found_http_status,
                        )
                        endpoints.append(ep_fact)

                        contracts_eps.append(
                            EndpointRuntimeContract(
                                path=ep_fact.path,
                                method=ep_fact.method,
                                module_path=ep_fact.module_path,
                                func_name=ep_fact.func_name,
                                status_code=ep_fact.status_code,
                                depends=ep_fact.depends,
                                depends_imports=ep_fact.depends_imports,
                                injected_params=ep_fact.injected_params,
                                uses_injected=ep_fact.uses_injected,
                                observed_calls=calls,
                                request_body_param=ep_fact.request_body_param,
                                request_model=ep_fact.request_model,
                                request_required_fields=ep_fact.request_required_fields,
                                request_optional_fields=ep_fact.request_optional_fields,
                                path_params=list(ep.get("path_params") or []),
                                query_params_required=list(ep.get("query_params_required") or []),
                                query_params_optional=list(ep.get("query_params_optional") or []),
                                header_params_required=list(ep.get("header_params_required") or []),
                                header_params_optional=list(ep.get("header_params_optional") or []),
                                cookie_params_required=list(ep.get("cookie_params_required") or []),
                                cookie_params_optional=list(ep.get("cookie_params_optional") or []),
                                form_params_required=list(ep.get("form_params_required") or []),
                                form_params_optional=list(ep.get("form_params_optional") or []),
                                file_params_required=list(ep.get("file_params_required") or []),
                                file_params_optional=list(ep.get("file_params_optional") or []),
                                response_model=ep_fact.response_model,
                                response_model_required_fields=ep_fact.response_model_required_fields,
                                response_model_optional_fields=ep_fact.response_model_optional_fields,
                                statefulness_recommended=ep_fact.statefulness_recommended,
                                returns_none_on_not_found=ep_fact.returns_none_on_not_found,
                                not_found_http_status=ep_fact.not_found_http_status,
                            )
                        )
                    # Inferir estilo de tests (sync vs async) desde el CÓDIGO real.
                    #
                    # Regla pragmática:
                    # - Por defecto "sync" (TestClient). FastAPI soporta perfectamente endpoints async bajo TestClient.
                    # - Solo usar "async" si el propio suite debe ser async por razones estructurales (muy raras):
                    #   p.ej. si el proyecto expone explícitamente tests async o si detectamos dependencias a pytest-anyio/asyncio en config.
                    #
                    # Nota: Este flag existe para evitar que el LLM mezcle fixtures async + TestClient (error frecuente).
                    tests_style = "sync"
                    runtime_facts = RuntimeFacts(
                        endpoints=endpoints,
                        env_vars_explicit=list(facts.get("env_vars_explicit") or []),
                        imports=list(facts.get("imports") or []),
                        tests_style=tests_style,
                    )
                    estructura = persist_runtime_facts(project_structure=estructura, runtime_facts=runtime_facts)

                    # Nota: `hermetic` controla cómo se interpreta y repara pytest:
                    # - hermetic=True  -> se asume que NO hay integraciones reales accesibles; preferimos fixers/tests/overrides.
                    # - hermetic=False -> se asume que el runtime es "real" y permitimos que pytest revele problemas de wiring/código real.
                    #
                    # Política del proyecto:
                    # - PARCIAL => hermetic=True (tests aislados, sin I/O real).
                    # - COMPLETA => hermetic=False (no hay integraciones externas; tests y código deberían ejecutarse "de verdad").
                    hermetic_flag = str(modo_generacion).upper() == "PARCIAL"
                    runtime_contracts = RuntimeContracts(
                        endpoints=contracts_eps,
                        env_vars_explicit=list(facts.get("env_vars_explicit") or []),
                        imports=list(facts.get("imports") or []),
                        tests_style=tests_style,
                        hermetic=hermetic_flag,
                        generation_mode=modo_generacion,
                        allowed_dependency_overrides=None,
                    )
                    estructura = persist_runtime_contracts(project_structure=estructura, runtime_contracts=runtime_contracts)

                    archivos_creados = materializar_proyecto(
                        nombre_proyecto=self.nombre_proyecto,
                        estructura={
                            ".poc_it/runtime_facts.json": estructura[".poc_it/runtime_facts.json"],
                            ".poc_it/runtime_contracts.json": estructura[".poc_it/runtime_contracts.json"],
                        },
                        limpiar_directorio=False,
                    )
                except Exception:
                    logger.exception("No se pudo persistir runtime_facts; se continúa sin artefacto intermedio.")

                # NUEVO FLUJO (separación estricta de responsabilidades)
                # Fase A: code correctness loop (solo código, import-time + wiring mínimo)
                # Fase B: generación de tests (LLM)
                # Fase C: pytest loop para alinear tests y, si aplica, código
                #
                # Política:
                # - PARCIAL: mantenemos el flujo hermético A/B/C via ejecutar_reparacion_runtime().
                # - COMPLETA: además habilitamos el postprocesado por pytest (A/C pragmáticos):
                #   * corrige código si hay fallos reales (wiring, imports, etc.)
                #   * corrige asserts/tests cuando el fallo es de expectativas
                ejecutar_reparacion_runtime(
                    nombre_proyecto=self.nombre_proyecto,
                    descripcion_global=self.descripcion_global,
                    project_dir=project_dir,
                    context=context,
                    resultado=resultado,
                    estructura=estructura,
                    archivos_creados=archivos_creados,
                    regenerar_tests=True,
                )

                # Estado final: determinista y estructurado.
                #
                # Política requerida:
                # - Tanto en COMPLETO como en PARCIAL: si el loop de reparación de tests no converge,
                #   degradar a "contract-lite" (suite mínima) y re-ejecutar pytest.
                # - Si esa suite mínima pasa => continuar (docs/estimación/publicación) SIN generar README_ERROR fatal.
                # - Si incluso contract-lite falla => no publicar.
                run_result = RunResult.error(mode=str(modo_generacion).upper(), reason="pytest_failed")

                # 1) Leer resultado del loop de repair si existe (lo setea ejecutar_reparacion_runtime)
                degraded = False
                degrade_type = None
                try:
                    pr = (resultado or {}).get("pytest_repair") if isinstance(resultado, dict) else None
                    if isinstance(pr, dict):
                        degraded = bool(pr.get("degraded"))
                        degrade_type = pr.get("degrade_type")
                except Exception:
                    degraded = False
                    degrade_type = None

                # 2) Evaluar pytest actual
                try:
                    from poc_it.orquestacion.pytest_llm_repair import (
                        _extract_counts_from_junit_xml,
                        _read_pytest_junit_xml,
                    )

                    xml = _read_pytest_junit_xml(project_dir)
                    counts = _extract_counts_from_junit_xml(xml)
                    pytest_ok = bool(counts and (counts[0] + counts[1] == 0))
                except Exception:
                    pytest_ok = False

                # 3) Si no pasó y no degradó todavía, degradar aquí (contract-lite) como último recurso.
                if not pytest_ok and not degraded:
                    try:
                        # Degradación a contract-lite: suite mínima (smoke_import + openapi).
                        # IMPORTANTE: materializar a disco el patch mínimo, porque `_degrade_to_contract_lite`
                        # actualiza `estructura` pero no siempre garantiza escritura final si el pipeline se corta.
                        from poc_it.orquestacion.pytest_llm_repair import _degrade_to_contract_lite, _run_pytest

                        patch_min = _degrade_to_contract_lite(
                            nombre_proyecto=self.nombre_proyecto,
                            estructura=estructura,
                        )
                        try:
                            materializar_proyecto(
                                nombre_proyecto=self.nombre_proyecto,
                                estructura=patch_min,
                                limpiar_directorio=False,
                            )
                        except Exception:
                            pass

                        ok2, out2, _ = _run_pytest(project_dir)
                        degraded = True
                        degrade_type = "contract-lite"

                        # refrescar estado pytest tras degradación (suite mínima)
                        # Nota: el report XML puede no existir si pytest corrió sin junitxml fallback;
                        # en ese caso, ok2 es la fuente de verdad.
                        pytest_ok = bool(ok2)

                        # reflejar en `resultado` para el resto del pipeline
                        try:
                            if isinstance(resultado, dict):
                                resultado["pytest_repair"] = {
                                    "ok": bool(pytest_ok),
                                    "attempts": int((pr or {}).get("attempts") or 0) if isinstance(pr, dict) else 0,
                                    "degraded": True,
                                    "degrade_type": "contract-lite",
                                    "artifacts": dict((pr or {}).get("artifacts") or {}) if isinstance(pr, dict) else {},
                                }
                        except Exception:
                            pass
                    except Exception:
                        # best-effort: si falla degradación, seguimos con pytest_ok=False
                        pass

                # 4) Política: considerar OK si pytest pasa, incluyendo el caso OK_DEGRADED (contract-lite).
                if pytest_ok:
                    run_result = RunResult.ok(
                        mode=str(modo_generacion).upper(),
                        degraded=degraded,
                        degrade_type=str(degrade_type) if degrade_type else None,
                    )

                if isinstance(resultado, dict):
                    resultado["pytest_ok"] = bool(run_result.pytest_ok)
                    resultado["estado_final"] = run_result.status
                    resultado["publishable"] = bool(run_result.publishable)
                    resultado["run_reasons"] = list(run_result.reasons)
                    resultado["degraded"] = bool(run_result.degraded) if run_result.degraded is not None else None
                    resultado["degrade_type"] = run_result.degrade_type

                if str(modo_generacion).upper() == "COMPLETO":
                    postprocesar_alineacion_por_pytest(
                        nombre_proyecto=self.nombre_proyecto,
                        project_dir=project_dir,
                        resultado=resultado,
                        estructura=estructura,
                        archivos_creados=archivos_creados,
                    )

            modo_upper = modo_generacion

            estimacion_generada = None
            estimacion_manual = None

            if modo_upper == ModoGeneracion.ASESOR:
                # En ASESOR no se genera PoC, pero SÍ queremos reflejar el tiempo real medido por PoC-it.
                #
                # Importante: aunque no haya generación de código, el pipeline de análisis y docs puede tardar.
                # Este tiempo medido debe reflejarse en README_ANALISIS (fila "PoC-it").
                import time

                # Si por algún motivo no se inicializó antes, lo iniciamos aquí.
                if not t_pocit_inicio:
                    t_pocit_inicio = time.perf_counter()
                t_pocit_fin = time.perf_counter()

                tiempo_real_pocit_horas = 0.0
                try:
                    if t_pocit_fin >= t_pocit_inicio:
                        tiempo_real_pocit_horas = (t_pocit_fin - t_pocit_inicio) / 3600
                except Exception:
                    tiempo_real_pocit_horas = 0.0

                # Guardrail anti-0: si el contador no quedó bien, forzamos 1s mínimo
                if tiempo_real_pocit_horas <= 0.0:
                    tiempo_real_pocit_horas = 1.0 / 3600.0  # 1 segundo

                estimacion_manual = self._estimacion_manual(
                    spec=resultado.get("spec") if isinstance(resultado, dict) else None
                )
                # Parcheamos el tiempo medido en la estimación manual (la que consume README_ANALISIS en ASESOR)
                try:
                    estimacion_manual.horas_scopeguardian = float(tiempo_real_pocit_horas)
                except Exception:
                    pass
            else:
                spec = resultado.get("spec") if isinstance(resultado, dict) else None

                # Estimación se construye tras generar documentación (para incluirla en el tiempo medido).
                # Inicializamos con 0.0 y se recalculará al final.
                tiempo_real_pocit_horas = 0.0

                estimacion_generada = self._estimacion_generada(
                    modo_generacion,
                    tiempo_real_pocit_horas,
                    spec=spec,
                )
                # Mantener también una estimación manual para README_ANALISIS (sin recalcular en docs).
                estimacion_manual = self._estimacion_manual(spec=spec)

            # Persistencia de artefactos "fuente de verdad" (solo una vez) dentro de la PoC.
            #
            # Objetivo:
            # - facilitar diagnósticos post-mortem sin depender de output/_debug
            # - alimentar reparaciones posteriores (manuales o automáticas) con el SPEC usado realmente
            # - mantenerlo fuera del código publicado (en `.poc_it/`)
            try:
                patch_truth = {}
                if self._contexto_normalizado:
                    patch_truth[".poc_it/contexto_normalizado.json"] = self._contexto_normalizado.model_dump_json(
                        indent=2
                    )
                if isinstance(resultado, dict) and isinstance(resultado.get("spec"), dict):
                    import json as _json

                    patch_truth[".poc_it/spec.json"] = _json.dumps(
                        resultado.get("spec"), ensure_ascii=False, indent=2
                    )
                if patch_truth:
                    materializar_proyecto(
                        nombre_proyecto=self.nombre_proyecto,
                        estructura=patch_truth,
                        limpiar_directorio=False,
                    )
            except Exception:
                pass

            # ------------------------------------------------------------
            # Documentación + estimación final (determinista)
            # ------------------------------------------------------------
            # 1) Capturar tiempo actual para métricas de docs (PERFORMANCE).
            #    Importante: antes estaba a 0.0, lo que producía "0 min 0 s" en la tabla.
            if modo_generacion != ModoGeneracion.ASESOR:
                import time

                t_pocit_fin = time.perf_counter()

            # 2) Generación docs con la mejor estimación disponible (puede ser provisional).
            generar_documentacion(
                nombre_proyecto=self.nombre_proyecto,
                descripcion_global=self.descripcion_global,
                tecnologias=self.tecnologias,
                context=context,
                modo_generacion=modo_generacion,
                estructura=estructura,
                resultado=resultado,
                estimacion_generada=estimacion_generada,
                estimacion_manual=estimacion_manual,
                t_clasificacion_inicio=self.t_clasificacion_inicio,
                t_clasificacion_fin=self.t_clasificacion_fin,
                # Para métricas internas de docs (PERFORMANCE), reutilizamos el rango real del pipeline PoC-it.
                t_generacion_inicio=(float(t_pocit_inicio_global or 0.0) or float(t_pocit_inicio_local or 0.0)),
                t_generacion_fin=t_pocit_fin,
            )

            # 3) Cerrar tiempo real PoC-it DESPUÉS de docs (tiempo total real).
            if modo_generacion != ModoGeneracion.ASESOR:
                import time

                t_pocit_fin = time.perf_counter()

                inicio_medicion = float(t_pocit_inicio_global or 0.0) or float(t_pocit_inicio_local or 0.0)

                spec = resultado.get("spec") if isinstance(resultado, dict) else None
                tiempo_real_pocit_horas = 0.0
                try:
                    if inicio_medicion and t_pocit_fin and t_pocit_fin >= inicio_medicion:
                        tiempo_real_pocit_horas = (t_pocit_fin - inicio_medicion) / 3600.0
                except Exception:
                    tiempo_real_pocit_horas = 0.0
                if tiempo_real_pocit_horas <= 0.0:
                    tiempo_real_pocit_horas = 1.0 / 3600.0  # nunca 0

                segundos = max(int(round(tiempo_real_pocit_horas * 3600)), 1)

                logger.info("[ESTIMACION] inicio_global=%s", str(t_pocit_inicio_global) if t_pocit_inicio_global else "None")
                logger.info("[ESTIMACION] fin=%s", t_pocit_fin)
                logger.info("[ESTIMACION] segundos=%s", segundos)
                logger.info("[ESTIMACION] horas=%s", tiempo_real_pocit_horas)

                estimacion_generada = self._estimacion_generada(
                    modo_generacion,
                    tiempo_real_pocit_horas,
                    spec=spec,
                )

                # 4) Parche determinista del bloque de estimación (sin LLM) con el tiempo FINAL.
                from poc_it.orquestacion.patch_estimacion_readme import parchear_bloque_estimacion

                parchear_bloque_estimacion(
                    nombre_proyecto=self.nombre_proyecto,
                    estimacion_generada=estimacion_generada,
                )
                logger.info("[ESTIMACION] archivos_parcheados=README.md,README_ANALISIS.md")

        except Exception as exc:
            logger.exception("[ORQUESTADOR] Error no recuperable durante generación libre")
            fallback_readme, fallback_error = self._build_fallback_docs(exc)

            archivos_creados = materializar_proyecto(
                nombre_proyecto=self.nombre_proyecto,
                estructura={README_FINAL_FILENAME: fallback_readme, README_ERROR_FILENAME: fallback_error},
                limpiar_directorio=True,
            )

            return {
                "nombre_proyecto": self.nombre_proyecto,
                "archivos_creados": archivos_creados,
                "error": str(exc),
            }

        # Devolvemos flags que main.py usa para decidir publicación.
        out = {"nombre_proyecto": self.nombre_proyecto, "archivos_creados": archivos_creados}

        try:
            if isinstance(resultado, dict):
                if "estado_final" in resultado:
                    out["estado_final"] = resultado.get("estado_final")
                if "pytest_ok" in resultado:
                    out["pytest_ok"] = resultado.get("pytest_ok")
                if "publishable" in resultado:
                    out["publishable"] = resultado.get("publishable")
        except Exception:
            pass

        return out
