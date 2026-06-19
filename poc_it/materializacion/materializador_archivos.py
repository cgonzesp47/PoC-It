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


def _normalizar_paths(estructura: Dict[str, str]) -> Dict[str, str]:
    """
    Normaliza separadores y elimina paths vacíos.
    """
    out: Dict[str, str] = {}
    for k, v in (estructura or {}).items():
        if not k:
            continue
        kk = str(k).replace("\\", "/").lstrip("/")
        out[kk] = v if v is not None else ""
    return out


def _autocompletar_init_py(estructura: Dict[str, str]) -> Dict[str, str]:
    """
    Garantiza que todo directorio bajo `app/` que contenga algún `.py` tenga su `__init__.py`.

    Motivación:
    - Evitar loops de regeneración por errores mecánicos del modelo.
    - Hacer la materialización más robusta sin depender de LLM.

    Nota:
    - `__init__.py` puede ser vacío legítimamente.
    """
    estructura = _normalizar_paths(estructura)
    py_files = [p for p in estructura.keys() if p.startswith("app/") and p.endswith(".py")]

    required_inits = set()
    for p in py_files:
        parts = p.split("/")[:-1]  # directorios
        # para cada directorio app/x/y, añadir app/x/y/__init__.py y app/x/__init__.py...
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d:
                required_inits.add(f"{d}/__init__.py")

    # Asegurar también el root package `app/__init__.py` si hay código en app/
    if any(p.startswith("app/") for p in py_files):
        required_inits.add("app/__init__.py")

    for init_path in sorted(required_inits):
        if init_path not in estructura:
            estructura[init_path] = ""

    return estructura


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

    estructura = _autocompletar_init_py(estructura)
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
