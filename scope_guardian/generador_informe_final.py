"""
ScopeGuardian - Generador de Informe Final

Responsabilidad:
Generar un README_FINAL.md determinista que documente:

- Qué se ha generado automáticamente
- Qué archivos se han creado
- Qué partes requieren intervención manual
- Cómo ejecutar el proyecto

Este módulo:
- NO utiliza LLM.
- NO materializa archivos.
- NO mezcla lógica de generación.
- Es completamente determinista.
"""

from __future__ import annotations

from typing import List
from scope_guardian.capacidades import EvaluacionCapacidades


# ==========================================================
# FUNCIÓN PRINCIPAL
# ==========================================================


def generar_informe_final(
    nombre_proyecto: str,
    evaluacion: EvaluacionCapacidades,
    archivos_creados: List[str],
) -> str:
    """
    Genera el contenido Markdown del README_FINAL.md.

    Args:
        nombre_proyecto: Nombre del proyecto.
        evaluacion: Resultado de evaluación de capacidades.
        archivos_creados: Lista de archivos materializados.

    Returns:
        Contenido en formato Markdown.
    """

    if not archivos_creados:
        raise ValueError("No hay archivos creados para documentar")

    lineas: List[str] = []

    # ------------------------------------------------------
    # Título
    # ------------------------------------------------------
    lineas.append(f"# {nombre_proyecto}")
    lineas.append("")
    lineas.append("## Generación automática completada")
    lineas.append("")
    lineas.append(
        "Se ha generado automáticamente la estructura base del proyecto "
        "y los siguientes bloques:"
    )
    lineas.append("")

    # ------------------------------------------------------
    # Bloques generados automáticamente
    # ------------------------------------------------------
    if evaluacion.generables:
        for bloque in evaluacion.generables:
            lineas.append(f"- {bloque.descripcion}")
    else:
        lineas.append("- (No se han detectado bloques generables)")

    lineas.append("")
    lineas.append("### Archivos creados")
    lineas.append("")

    for archivo in sorted(archivos_creados):
        lineas.append(f"- {archivo}")

    lineas.append("")
    lineas.append("---")
    lineas.append("")

    # ------------------------------------------------------
    # Bloques manuales
    # ------------------------------------------------------
    if evaluacion.manuales:
        lineas.append("## ⚠️ Intervención manual requerida")
        lineas.append("")
        lineas.append(
            "Los siguientes elementos requieren configuración o acciones externas:"
        )
        lineas.append("")

        for i, bloque in enumerate(evaluacion.manuales, start=1):
            lineas.append(f"### {i}. {bloque.descripcion}")
            lineas.append("")
            lineas.append(f"**Motivo:** {bloque.motivo}")
            lineas.append("")
    else:
        lineas.append("## No se requieren pasos manuales adicionales")
        lineas.append("")
        lineas.append(
            "Todos los bloques necesarios han sido generados automáticamente."
        )
        lineas.append("")

    lineas.append("---")
    lineas.append("")

    # ------------------------------------------------------
    # Instrucciones de ejecución
    # ------------------------------------------------------
    lineas.append("## Cómo ejecutar el proyecto")
    lineas.append("")
    lineas.append("```bash")
    lineas.append(f"cd output/{nombre_proyecto}")
    lineas.append("uvicorn main:app --reload")
    lineas.append("```")
    lineas.append("")

    return "\n".join(lineas)
