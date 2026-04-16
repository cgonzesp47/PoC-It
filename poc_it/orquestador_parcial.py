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

from typing import Dict, Any

from poc_it.generador_artefactos import generar_proyecto_completo
from poc_it.materializador_archivos import materializar_proyecto
from poc_it.models import ProjectContext, PlantillaUsuario
from poc_it.clasificador import clasificar_viabilidad


class OrquestadorParcial:
    """
    Orquestador libre basado en generación completa.
    """

    # ======================================================
    # 🔹 MÉTODOS PRIVADOS DE ESTIMACIÓN (MODULARIZADOS)
    # ======================================================

    def _estimacion_generada(self, modo: str, horas: float):
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

    def _estimacion_completa(self, horas: float):
        from poc_it.estimador_esfuerzo import calcular_estimacion_llm

        return calcular_estimacion_llm(
            descripcion_proyecto=self.descripcion_global,
            modo="COMPLETA_SOLICITADA",
            tiempo_real_scopeguardian_horas=horas,
        )

    def _estimacion_manual(self):
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

    def ejecutar(self) -> Dict[str, Any]:
        """
        Ejecuta el flujo completo incluyendo clasificación basada en ProjectContext.
        """

        try:
            # ==========================================
            # 1) Construcción del contexto inicial
            # ==========================================
            # Usamos la plantilla REAL proporcionada por el usuario
            context = ProjectContext(plantilla=self.plantilla)

            # ==========================================
            # 1.1) Fase de Normalización Formal de Contexto
            # ==========================================
            from poc_it.normalizador_contexto import normalizar_plantilla
            from poc_it.models import ContextoNormalizado

            # Normalizamos usando la plantilla completa real
            contexto_dict = normalizar_plantilla(self.plantilla)

            try:
                contexto_normalizado = ContextoNormalizado(**contexto_dict)
                context.contexto_normalizado = contexto_normalizado
                context.registrar_modelo("normalizacion_contexto", "chat_completion_json")
            except Exception:
                # En caso extremo de estructura inesperada
                context.contexto_normalizado = None

            # ==========================================
            # 2) Clasificación con nuevo agente
            # ==========================================
            import time
            t_clasificacion_inicio = time.perf_counter()
            context = clasificar_viabilidad(context)
            t_clasificacion_fin = time.perf_counter()

            # Mostrar estado actual del contexto
            print("\n[DEBUG CONTEXT DESPUÉS DE CLASIFICACIÓN]")
            print(context.model_dump_json(indent=2))
            print("")

            modo_generacion = context.clasificacion or self.modo_generacion

            # (Clasificador oficial mantenido. Sin comparativa adicional.)

            # ==========================================
            # 3) Generación (omitida si modo ASESOR)
            # ==========================================
            import time

            estructura = {}
            archivos_creados = []
            tiempo_generacion_horas = 0.0

            if modo_generacion.upper() != "ASESOR":
                t_generacion_inicio = time.perf_counter()

                resultado = generar_proyecto_completo(
                    descripcion_global=self.descripcion_global,
                    contexto_normalizado=(
                        context.contexto_normalizado.model_dump()
                        if context.contexto_normalizado
                        else None
                    ),
                )

                t_generacion_fin = time.perf_counter()
                tiempo_generacion_horas = (t_generacion_fin - t_generacion_inicio) / 3600

                files = resultado.get("files", [])

                if not files:
                    raise ValueError("El modelo no generó archivos válidos.")

                estructura = {
                    f["path"]: f["content"]
                    for f in files
                    if "path" in f and "content" in f
                }

                archivos_creados = materializar_proyecto(
                    nombre_proyecto=self.nombre_proyecto,
                    estructura=estructura,
                )

            # ======================================================
            # GENERACIÓN DE README PROFESIONAL (POST-PROCESADO)
            # ======================================================

            from poc_it.generador_informes import (
                generar_readme_final,
                generar_readme_manual,
            )

            # Detectar endpoints simples a partir de paths
            endpoints_detectados = [
                path for path in estructura.keys()
                if path.endswith(".py")
            ]

            # ======================================================
            # GENERACIÓN DE DOCUMENTACIÓN EN PARALELO
            # ======================================================
            from concurrent.futures import ThreadPoolExecutor

            from poc_it.generador_informes import (
                generar_readme_final,
                generar_readme_manual,
                generar_readme_asesor,
            )
            from poc_it.opciones import generar_opciones

            # Usar primero contexto normalizado si existe
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

            t_documentacion_inicio = time.perf_counter()

            # ======================================================
            # CÁLCULO DE ESTIMACIONES MEDIANTE MÉTODOS MODULARIZADOS
            # ======================================================

            modo_upper = modo_generacion.upper()

            # Inicialización segura (evita errores en ejecución paralela)
            estimacion_generada = None
            estimacion_completa = None
            estimacion_manual = None

            if modo_upper == "PARCIAL":
                estimacion_generada = self._estimacion_generada(
                    modo_generacion,
                    tiempo_generacion_horas,
                )
                estimacion_completa = self._estimacion_completa(
                    tiempo_generacion_horas,
                )
                estimacion_manual = self._estimacion_manual()

            elif modo_upper == "COMPLETO":
                estimacion_generada = self._estimacion_generada(
                    modo_generacion,
                    tiempo_generacion_horas,
                )
                estimacion_completa = self._estimacion_completa(
                    tiempo_generacion_horas,
                )
                estimacion_manual = None  # explícito

            elif modo_upper == "ASESOR":
                estimacion_manual = self._estimacion_manual()
                estimacion_generada = None
                estimacion_completa = None

            # Blindaje adicional: asegurar que las estimaciones sean objetos válidos
            # Nunca sustituimos por string porque generar_bloque_markdown espera atributos
            if estimacion_generada is None:
                estimacion_generada = self._estimacion_manual()

            if estimacion_completa is None:
                estimacion_completa = self._estimacion_manual()

            if estimacion_manual is None:
                estimacion_manual = self._estimacion_manual()

            with ThreadPoolExecutor(max_workers=3) as executor:
                future_final = executor.submit(
                    generar_readme_final,
                    self.nombre_proyecto,
                    self.descripcion_global,
                    "Arquitectura generada dinámicamente por LLM",
                    endpoints_detectados,
                    modo_generacion.upper(),
                    self.tecnologias,
                    estimacion_generada,
                    estimacion_completa,
                )

                future_analisis = executor.submit(
                    generar_readme_asesor,
                    self.nombre_proyecto,
                    context.plantilla.problema,
                    usuarios_reales,
                    funcionalidades_reales,
                    limites_reales,
                    tecnologias_reales,
                    arquitectura_real,
                    opciones_estrategicas,
                    estimacion_manual,
                )

                future_manual = None
                if modo_generacion.upper() == "PARCIAL":
                    future_manual = executor.submit(
                        generar_readme_manual,
                        self.nombre_proyecto,
                        "Arquitectura generada dinámicamente por LLM",
                        self.tecnologias,
                        endpoints_detectados,
                    )

                readme_final = future_final.result()
                readme_analisis = future_analisis.result()
                readme_manual = future_manual.result() if future_manual else None

            archivos_readme_final = materializar_proyecto(
                nombre_proyecto=self.nombre_proyecto,
                estructura={"README.md": readme_final},
            )
            archivos_creados.extend(archivos_readme_final)

            archivos_readme_analisis = materializar_proyecto(
                nombre_proyecto=self.nombre_proyecto,
                estructura={"README_ANALISIS.md": readme_analisis},
            )
            archivos_creados.extend(archivos_readme_analisis)

            if readme_manual:
                archivos_readme_manual = materializar_proyecto(
                    nombre_proyecto=self.nombre_proyecto,
                    estructura={"README_MANUAL.md": readme_manual},
                )
                archivos_creados.extend(archivos_readme_manual)

            t_documentacion_fin = time.perf_counter()

            print("\n[PERFORMANCE]")
            print(f"- Clasificación: {t_clasificacion_fin - t_clasificacion_inicio:.2f}s")
            print(f"- Generación libre: {t_generacion_fin - t_generacion_inicio:.2f}s")
            print(f"- Documentación: {t_documentacion_fin - t_documentacion_inicio:.2f}s\n")

        except Exception as exc:
            fallback_readme = (
                f"# {self.nombre_proyecto}\n\n"
                "## Error durante la generación libre\n\n"
                f"Error detectado:\n\n```\n{str(exc)}\n```\n"
            )

            archivos_creados = materializar_proyecto(
                nombre_proyecto=self.nombre_proyecto,
                estructura={"README.md": fallback_readme},
            )

            return {
                "nombre_proyecto": self.nombre_proyecto,
                "archivos_creados": archivos_creados,
                "error": str(exc),
            }

        return {
            "nombre_proyecto": self.nombre_proyecto,
            "archivos_creados": archivos_creados,
        }
