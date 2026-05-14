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

from poc_it.clasificador import clasificar_viabilidad
from poc_it.generador_artefactos import generar_proyecto_completo
from poc_it.materializador_archivos import materializar_proyecto
from poc_it.models import ContextoNormalizado, ModoGeneracion, PlantillaUsuario, ProjectContext
from poc_it.normalizador_contexto import normalizar_plantilla
from poc_it.orquestacion.generacion_documentacion import generar_documentacion
from poc_it.orquestacion.persistencia_spec import persist_spec_json
from poc_it.orquestacion.postprocesado_alineacion import postprocesar_alineacion_por_pytest
from poc_it.orquestacion.constantes import OUTPUT_DIRNAME, README_ERROR_FILENAME, README_FINAL_FILENAME
from poc_it.orquestacion.generacion_tests import generar_tests_unitarios
from poc_it.orquestacion.reparacion_runtime import ejecutar_reparacion_runtime

logger = logging.getLogger(__name__)


class OrquestadorParcial:
    """
    Orquestador libre basado en generación completa.
    """

    # ======================================================
    # 🔹 MÉTODOS PRIVADOS DE ESTIMACIÓN (MODULARIZADOS)
    # ======================================================

    def _estimacion_generada(self, modo: str, horas: float) -> Any:
        from poc_it.estimador_esfuerzo import calcular_estimacion_llm

        descripcion = f"""
Proyecto: {self.nombre_proyecto}
Modo: {modo}

Descripción:
{self.descripcion_global}
"""
        return calcular_estimacion_llm(
            descripcion_proyecto=descripcion,
            modo=modo,
            tiempo_real_scopeguardian_horas=horas,
        )

    def _estimacion_completa(self, horas: float) -> Any:
        from poc_it.estimador_esfuerzo import calcular_estimacion_llm

        return calcular_estimacion_llm(
            descripcion_proyecto=self.descripcion_global,
            modo="COMPLETA_SOLICITADA",
            tiempo_real_scopeguardian_horas=horas,
        )

    def _estimacion_manual(self) -> Any:
        from poc_it.estimador_esfuerzo import calcular_estimacion_llm

        return calcular_estimacion_llm(
            descripcion_proyecto=self.descripcion_global,
            modo=None,
            tiempo_real_scopeguardian_horas=0.0,
            generable=False,
        )

    def __init__(self, plantilla: PlantillaUsuario, modo_generacion: str):
        self.plantilla = plantilla
        self.nombre_proyecto = plantilla.nombre
        self.descripcion_global = plantilla.problema
        self.tecnologias = plantilla.tecnologias
        self.modo_generacion = modo_generacion

    def _build_context(self) -> ProjectContext:
        context = ProjectContext(plantilla=self.plantilla)
        return context

    def _normalizar_contexto(self, context: ProjectContext) -> None:
        contexto_dict = normalizar_plantilla(self.plantilla)

        try:
            contexto_normalizado = ContextoNormalizado(**contexto_dict)
            context.contexto_normalizado = contexto_normalizado
            context.registrar_modelo("normalizacion_contexto", "chat_completion_json")
        except Exception:
            context.contexto_normalizado = None

    def _clasificar(self, context: ProjectContext) -> tuple[ProjectContext, float, float]:
        import time

        t_clasificacion_inicio = time.perf_counter()
        context = clasificar_viabilidad(context)
        t_clasificacion_fin = time.perf_counter()

        logger.info("[DEBUG CONTEXT DESPUÉS DE CLASIFICACIÓN]\n%s", context.model_dump_json(indent=2))
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

        resultado = generar_proyecto_completo(
            descripcion_global=self.descripcion_global,
            contexto_normalizado=(
                context.contexto_normalizado.model_dump() if context.contexto_normalizado else None
            ),
        )

        persist_spec_json(self.nombre_proyecto, resultado, archivos_creados)

        t_generacion_fin = time.perf_counter()
        tiempo_generacion_horas = (t_generacion_fin - t_generacion_inicio) / 3600

        files = resultado.get("files", [])
        if not files:
            raise ValueError("El modelo no generó archivos válidos.")

        estructura = {f["path"]: f["content"] for f in files if "path" in f and "content" in f}

        archivos_creados = materializar_proyecto(
            nombre_proyecto=self.nombre_proyecto,
            estructura=estructura,
        )

        return estructura, archivos_creados, tiempo_generacion_horas, resultado

    def _build_fallback_docs(self, exc: Exception) -> Tuple[str, str]:
        fallback_readme = (
            f"# {self.nombre_proyecto}\n\n"
            "## Estado\n\n"
            "La generación no finalizó correctamente. Revisa `README_ERROR.md` para ver el detalle del error.\n"
        )

        fallback_error = (
            f"# {self.nombre_proyecto} – Error de generación\n\n"
            "## Error durante la generación libre\n\n"
            f"Error detectado:\n\n```\n{str(exc)}\n```\n"
        )

        return fallback_readme, fallback_error

    def ejecutar(self) -> Dict[str, Any]:
        """
        Ejecuta el flujo completo incluyendo clasificación basada en ProjectContext.
        """
        try:
            context = self._build_context()
            self._normalizar_contexto(context)

            context, t_clasificacion_inicio, t_clasificacion_fin = self._clasificar(context)

            modo_generacion = (context.clasificacion or self.modo_generacion).upper()

            estructura, archivos_creados, tiempo_generacion_horas, resultado = self._generar_y_materializar(
                context=context,
                modo_generacion=modo_generacion,
            )

            t_generacion_inicio = 0.0
            t_generacion_fin = 0.0
            if modo_generacion != ModoGeneracion.ASESOR:
                import time

                # Mantener comportamiento: el tiempo real se medía solo si se generaba.
                # (Aquí solo preservamos el contrato, no re-medimos; se usa para PERFORMANCE log)
                t_generacion_inicio = time.perf_counter()
                t_generacion_fin = t_generacion_inicio

                project_dir = os.path.join(OUTPUT_DIRNAME, self.nombre_proyecto)

                generar_tests = generar_tests_unitarios(self.nombre_proyecto, resultado, estructura, archivos_creados)
                postprocesar_alineacion_por_pytest(
                    nombre_proyecto=self.nombre_proyecto,
                    project_dir=project_dir,
                    resultado=resultado,
                    estructura=estructura,
                    archivos_creados=archivos_creados,
                )
                ejecutar_reparacion_runtime(
                    nombre_proyecto=self.nombre_proyecto,
                    descripcion_global=self.descripcion_global,
                    project_dir=project_dir,
                    context=context,
                    resultado=resultado,
                    estructura=estructura,
                    archivos_creados=archivos_creados,
                    regenerar_tests=generar_tests,
                )

            modo_upper = modo_generacion

            estimacion_generada = None
            estimacion_completa = None
            estimacion_manual = None

            if modo_upper == ModoGeneracion.PARCIAL:
                estimacion_generada = self._estimacion_generada(modo_generacion, tiempo_generacion_horas)
                estimacion_completa = self._estimacion_completa(tiempo_generacion_horas)
                estimacion_manual = self._estimacion_manual()
            elif modo_upper == ModoGeneracion.COMPLETO:
                estimacion_generada = self._estimacion_generada(modo_generacion, tiempo_generacion_horas)
                estimacion_completa = self._estimacion_completa(tiempo_generacion_horas)
                estimacion_manual = None
            elif modo_upper == ModoGeneracion.ASESOR:
                estimacion_manual = self._estimacion_manual()
                estimacion_generada = None
                estimacion_completa = None

            if estimacion_generada is None:
                estimacion_generada = self._estimacion_manual()
            if estimacion_completa is None:
                estimacion_completa = self._estimacion_manual()
            if estimacion_manual is None:
                estimacion_manual = self._estimacion_manual()

            # Generación docs
            generar_documentacion(
                nombre_proyecto=self.nombre_proyecto,
                descripcion_global=self.descripcion_global,
                tecnologias=self.tecnologias,
                context=context,
                modo_generacion=modo_generacion,
                estructura=estructura,
                resultado=resultado,
                estimacion_generada=estimacion_generada,
                estimacion_completa=estimacion_completa,
                estimacion_manual=estimacion_manual,
                t_clasificacion_inicio=t_clasificacion_inicio,
                t_clasificacion_fin=t_clasificacion_fin,
                t_generacion_inicio=t_generacion_inicio,
                t_generacion_fin=t_generacion_fin,
            )

        except Exception as exc:
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

        return {"nombre_proyecto": self.nombre_proyecto, "archivos_creados": archivos_creados}
