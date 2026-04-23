"""
PoC-it – Materializador Libre de Proyecto Completo

Nuevo enfoque:

- Ya no trabajamos con inyección por marcadores.
- Ya no trabajamos con proyecto base.
- El LLM genera estructura completa.
- Este módulo simplemente materializa archivos en disco.

Mantiene:
- Validación sintáctica opcional.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, List


# ==========================================================
# UTILIDAD INTERNA
# ==========================================================


def _asegurar_directorio(ruta: str) -> None:
    directorio = os.path.dirname(ruta)
    if directorio and not os.path.exists(directorio):
        os.makedirs(directorio, exist_ok=True)


# ==========================================================
# FUNCIÓN PRINCIPAL
# ==========================================================


def materializar_proyecto(
    nombre_proyecto: str,
    estructura: Dict[str, str],
    limpiar_directorio: bool = True,
) -> List[str]:
    """
    Materializa en disco el proyecto generado por el LLM.

    - Crea carpeta dentro de ./output/
    - Crea subcarpetas automáticamente
    - Escribe archivos completos
    """

    base_path = os.path.join("output", nombre_proyecto)

    # Si el proyecto ya existía (reintentos / regeneraciones), limpiamos para evitar
    # artefactos residuales (p.ej. README_ERROR.md antiguo) que desincronicen el estado.
    if limpiar_directorio and os.path.isdir(base_path):
        shutil.rmtree(base_path)

    os.makedirs(base_path, exist_ok=True)

    archivos_creados: List[str] = []

    for ruta_relativa, contenido in estructura.items():
        ruta_completa = os.path.join(base_path, ruta_relativa)

        _asegurar_directorio(ruta_completa)

        with open(ruta_completa, "w", encoding="utf-8") as f:
            f.write(contenido)

        archivos_creados.append(ruta_completa)

    return archivos_creados
