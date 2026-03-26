"""
ScopeGuardian - Orquestador Parcial

Responsabilidad:
Coordinar la generación parcial completa de una PoC cuando existen
bloques generables detectados.

Flujo:
1. Evaluar capacidades
2. Generar proyecto base mínimo
3. Expandir bloques generables en artefactos
4. Materializar proyecto completo
5. Generar README_FINAL.md
6. Materializar informe final

Este módulo:
- NO utiliza lógica de generación directa.
- NO contiene prompts.
- SOLO orquesta submódulos especializados.
"""

from __future__ import annotations

from typing import Dict, Any

from scope_guardian.capacidades import (
    EvaluacionCapacidades,
    evaluar_capacidades,
)
from scope_guardian.generador_proyecto_base import generar_proyecto_base
from scope_guardian.generador_artefactos import generar_artefactos_para_bloque
from scope_guardian.materializador_archivos import materializar_proyecto
from scope_guardian.generador_informe_final import generar_informe_final


# ==========================================================
# CLASE PRINCIPAL
# ==========================================================


class OrquestadorParcial:
    """
    Orquesta la generación parcial de una PoC.
    """

    def __init__(self, nombre_proyecto: str, descripcion_global: str, tecnologias: str):
        self.nombre_proyecto = nombre_proyecto
        self.descripcion_global = descripcion_global
        self.tecnologias = tecnologias

    # ------------------------------------------------------
    # MÉTODO PRINCIPAL
    # ------------------------------------------------------

    def ejecutar(self) -> Dict[str, Any]:
        """
        Ejecuta el flujo completo de generación parcial.

        Returns:
            Dict con resumen de ejecución.
        """

        # 1) Evaluar capacidades
        evaluacion: EvaluacionCapacidades = evaluar_capacidades(
            self.descripcion_global
        )

        if not evaluacion.generables:
            raise ValueError(
                "No existen bloques generables. Debe activarse modo asesor."
            )

        try:
            # 2) Generar proyecto base
            estructura_base = generar_proyecto_base(
                nombre_proyecto=self.nombre_proyecto,
                bloques_generables=[b.descripcion for b in evaluacion.generables],
                descripcion_global=self.descripcion_global,
                tecnologias=self.tecnologias,
            )

            # 3) Expandir bloques generables
            estructura_total = estructura_base.copy()

            for bloque in evaluacion.generables:
                try:
                    artefactos = generar_artefactos_para_bloque(
                        bloque=bloque,
                        descripcion_global=self.descripcion_global,
                        estructura_actual=estructura_total,
                    )
                    estructura_total.update(artefactos)
                except Exception:
                    # Si un bloque falla, se continúa con los demás
                    continue

            # 4) Generar informe final
            informe_final = generar_informe_final(
                nombre_proyecto=self.nombre_proyecto,
                evaluacion=evaluacion,
                archivos_creados=list(estructura_total.keys()),
            )

            estructura_total["README_FINAL.md"] = informe_final

            # 5) Materializar proyecto completo
            archivos_creados = materializar_proyecto(
                nombre_proyecto=self.nombre_proyecto,
                estructura=estructura_total,
            )

        except Exception as exc:
            # Si falla incluso el proyecto base, generamos proyecto mínimo resiliente
            fallback_readme = (
                f"# {self.nombre_proyecto}\n\n"
                "## Generación parcial con errores\n\n"
                "El sistema ha detectado errores durante la generación, "
                "pero ha continuado de forma resiliente.\n\n"
                f"Error detectado:\n\n```\n{str(exc)}\n```\n"
            )

            archivos_creados = materializar_proyecto(
                nombre_proyecto=self.nombre_proyecto,
                estructura={"README_FINAL.md": fallback_readme},
            )

            return {
                "nombre_proyecto": self.nombre_proyecto,
                "archivos_creados": archivos_creados,
                "bloques_generados": [],
                "bloques_manuales": [],
                "error": str(exc),
            }

        return {
            "nombre_proyecto": self.nombre_proyecto,
            "archivos_creados": archivos_creados,
            "bloques_generados": [b.descripcion for b in evaluacion.generables],
            "bloques_manuales": [
                b.descripcion for b in evaluacion.manuales
            ],
        }
