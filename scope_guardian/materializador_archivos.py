"""
ScopeGuardian - Materializador de Archivos

Responsabilidad:
Materializar físicamente en disco una estructura de archivos
ya validada previamente por los generadores.

Este módulo:
- NO utiliza LLM.
- NO valida sintaxis (eso ya fue validado antes).
- NO mezcla lógica de generación.
- Elimina completamente el proyecto previo si existe (Opción A).
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, List


# ==========================================================
# FUNCIÓN PRINCIPAL
# ==========================================================


def materializar_proyecto(
    nombre_proyecto: str,
    estructura: Dict[str, str],
) -> List[str]:
    """
    Materializa en disco la estructura completa del proyecto.

    Si el directorio ya existe, se elimina completamente antes de crear uno nuevo.

    Args:
        nombre_proyecto: Nombre del proyecto.
        estructura: Diccionario {ruta_relativa: contenido}.

    Returns:
        Lista de rutas de archivos creados.
    """

    if not estructura:
        raise ValueError("No hay estructura para materializar")

    directorio_base = os.path.join("output", nombre_proyecto)

    # Eliminar proyecto anterior si existe
    if os.path.exists(directorio_base):
        shutil.rmtree(directorio_base)

    os.makedirs(directorio_base, exist_ok=True)

    archivos_creados: List[str] = []

    for ruta_relativa, contenido in estructura.items():
        ruta_completa = os.path.join(directorio_base, ruta_relativa)

        # Crear directorios intermedios si es necesario
        directorio_archivo = os.path.dirname(ruta_completa)
        if directorio_archivo:
            os.makedirs(directorio_archivo, exist_ok=True)

        # Escribir archivo
        with open(ruta_completa, "w", encoding="utf-8") as f:
            f.write(contenido)

        archivos_creados.append(ruta_relativa)

    return archivos_creados
