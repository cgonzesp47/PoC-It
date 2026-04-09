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


class OrquestadorParcial:
    """
    Orquestador libre basado en generación completa.
    """

    def __init__(self, nombre_proyecto: str, descripcion_global: str, tecnologias: str):
        self.nombre_proyecto = nombre_proyecto
        self.descripcion_global = descripcion_global
        self.tecnologias = tecnologias

    def ejecutar(self) -> Dict[str, Any]:
        """
        Ejecuta generación completa delegando arquitectura al LLM.
        """

        try:
            resultado = generar_proyecto_completo(
                descripcion_global=self.descripcion_global
            )

            files = resultado.get("files", [])

            if not files:
                raise ValueError("El modelo no generó archivos válidos.")

            # Convertimos lista JSON a estructura esperada por materializador
            estructura = {
                f["path"]: f["content"]
                for f in files
                if "path" in f and "content" in f
            }

            archivos_creados = materializar_proyecto(
                nombre_proyecto=self.nombre_proyecto,
                estructura=estructura,
            )

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
