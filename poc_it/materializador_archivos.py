"""
ScopeGuardian - Inyección Segura de Bloques Dinámicos

Responsabilidad:
- Insertar bloques generados por el LLM dentro de los marcadores.
- Nunca reescribir archivos completos.
- Validar sintaxis tras la inyección.
"""

from __future__ import annotations

import ast
from typing import Dict, List, Any


# ==========================================================
# UTILIDAD INTERNA
# ==========================================================


def _reemplazar_bloque(
    contenido: str,
    start_marker: str,
    end_marker: str,
    nuevo_bloque: str,
) -> str:
    if start_marker not in contenido or end_marker not in contenido:
        return contenido

    before = contenido.split(start_marker)[0] + start_marker
    after = contenido.split(end_marker)[1]

    return f"{before}\n{nuevo_bloque.strip()}\n{end_marker}{after}"


def _validar_sintaxis(estructura: Dict[str, str]) -> None:
    for ruta, contenido in estructura.items():
        if ruta.endswith(".py"):
            ast.parse(contenido)


# ==========================================================
# FUNCIÓN PRINCIPAL
# ==========================================================


def inyectar_bloques_dinamicos(
    estructura_base: Dict[str, str],
    bloques: Dict[str, Any],
) -> Dict[str, str]:
    """
    Inserta bloques LLM dentro de los marcadores.
    """

    estructura = estructura_base.copy()

    # =============================
    # API.py
    # =============================

    estructura["app/api.py"] = _reemplazar_bloque(
        estructura["app/api.py"],
        "# === LLM_IMPORTS_START ===",
        "# === LLM_IMPORTS_END ===",
        str(bloques.get("imports", "") or ""),
    )

    estructura["app/api.py"] = _reemplazar_bloque(
        estructura["app/api.py"],
        "# === LLM_ENDPOINTS_START ===",
        "# === LLM_ENDPOINTS_END ===",
        str(bloques.get("endpoints", "") or ""),
    )

    # =============================
    # SCHEMAS
    # =============================

    estructura["app/schemas.py"] = _reemplazar_bloque(
        estructura["app/schemas.py"],
        "# === LLM_SCHEMAS_START ===",
        "# === LLM_SCHEMAS_END ===",
        str(bloques.get("schemas", "") or ""),
    )

    # =============================
    # SERVICES
    # =============================

    estructura["app/services.py"] = _reemplazar_bloque(
        estructura["app/services.py"],
        "# === LLM_SERVICE_LOGIC_START ===",
        "# === LLM_SERVICE_LOGIC_END ===",
        str(bloques.get("services_logic", "") or ""),
    )

    # =============================
    # REQUIREMENTS
    # =============================

    raw_requirements = bloques.get("requirements", [])
    req_extra: List[str] = (
        raw_requirements
        if isinstance(raw_requirements, list)
        else []
    )

    if req_extra:
        req_block = "\n".join(str(r) for r in req_extra)
        estructura["requirements.txt"] = _reemplazar_bloque(
            estructura["requirements.txt"],
            "# === LLM_REQUIREMENTS_START ===",
            "# === LLM_REQUIREMENTS_END ===",
            req_block,
        )

    # Validar sintaxis tras inyección
    _validar_sintaxis(estructura)

    return estructura
