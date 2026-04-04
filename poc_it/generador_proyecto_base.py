"""
ScopeGuardian - Generador de Proyecto Base (Refactorizado con Plantillas)

Nueva arquitectura:
- Estructura 100% determinista.
- El LLM SOLO puede rellenar zonas delimitadas.
- Nunca se reescriben archivos completos.
"""

from __future__ import annotations

import ast
from typing import Dict, List


# ==========================================================
# VALIDACIONES BÁSICAS
# ==========================================================


def _validar_sintaxis_python(estructura: Dict[str, str]) -> None:
    for ruta, contenido in estructura.items():
        if ruta.endswith(".py"):
            ast.parse(contenido)


# ==========================================================
# FUNCIÓN PRINCIPAL
# ==========================================================


def generar_proyecto_base(
    nombre_proyecto: str,
    bloques_generables: List[str],
    descripcion_global: str,
    tecnologias: str,
) -> Dict[str, str]:
    """
    Generador base con arquitectura de plantillas.
    El LLM rellenará únicamente las zonas marcadas.
    """

    estructura: Dict[str, str] = {}

    # =====================================================
    # main.py (100% determinista)
    # =====================================================

    estructura["main.py"] = """
from fastapi import FastAPI
import app.api as api_module
from fastapi import APIRouter

app = FastAPI()

router_instance = None
for attr_name in dir(api_module):
    attr = getattr(api_module, attr_name)
    if isinstance(attr, APIRouter):
        router_instance = attr
        break

if router_instance is None:
    raise RuntimeError("No se encontró ningún APIRouter en app.api")

app.include_router(router_instance)
""".strip()

    # =====================================================
    # app/__init__.py
    # =====================================================

    estructura["app/__init__.py"] = ""

    # =====================================================
    # app/api.py (plantilla con marcadores)
    # =====================================================

    estructura["app/api.py"] = """
from fastapi import APIRouter, HTTPException

# === LLM_IMPORTS_START ===
# LLM_IMPORTS_END ===

router = APIRouter()

# === LLM_ENDPOINTS_START ===
# LLM_ENDPOINTS_END ===
""".strip()

    # =====================================================
    # app/schemas.py (plantilla dinámica)
    # =====================================================

    estructura["app/schemas.py"] = """
from pydantic import BaseModel

# === LLM_SCHEMAS_START ===
# LLM_SCHEMAS_END ===
""".strip()

    # =====================================================
    # app/services.py (estructura fija + zona dinámica)
    # =====================================================

    estructura["app/services.py"] = """
# Almacenamiento en memoria base
storage = {}

def _ensure_entity(entity_name: str):
    if entity_name not in storage:
        storage[entity_name] = {}

# === LLM_SERVICE_LOGIC_START ===
# LLM_SERVICE_LOGIC_END ===
""".strip()

    # =====================================================
    # requirements.txt (base + extensión dinámica)
    # =====================================================

    estructura["requirements.txt"] = """
fastapi
uvicorn[standard]
pydantic

# === LLM_REQUIREMENTS_START ===
# LLM_REQUIREMENTS_END ===
""".strip()

    # =====================================================
    # VALIDACIÓN FINAL
    # =====================================================

    _validar_sintaxis_python(estructura)

    return estructura
