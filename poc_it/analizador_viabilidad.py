"""
ScopeGuardian - Analizador de Viabilidad (Arquitectura Multi‑Prompt)

Objetivo:
Separar responsabilidades cognitivas del modelo en llamadas pequeñas y controladas.

Flujo:

1) Clasificación (A/B) → ¿Puede generarse automáticamente?
2) Diseño técnico estructurado → Entidades + Endpoints + Componentes
3) Generación de opciones alineadas con la arquitectura generada

Cada fase usa un prompt independiente y minimalista.
Diseñado específicamente para LLM local 7B.
"""

from __future__ import annotations

from poc_it.models import (
    PlantillaUsuario,
    ResultadoViabilidad,
    ProjectContext,
    ModoGeneracion,
)
from poc_it.clasificador import clasificar_viabilidad
from poc_it.arquitectura import generar_arquitectura
from poc_it.opciones import generar_opciones
from poc_it.fases import detectar_fase, FaseProyecto


# ==========================================================
# 1. Modelo de Entrada
# ==========================================================


# Los modelos han sido extraídos a scope_guardian.models


# ==========================================================
# 3. FASE 1 — CLASIFICACIÓN (Ultra minimalista)
# ==========================================================


# Este módulo ahora actúa únicamente como orquestador ligero.


async def analizar_viabilidad(datos: PlantillaUsuario) -> ResultadoViabilidad:
    """
    Adaptador temporal para mantener compatibilidad con el flujo actual
    mientras el sistema migra progresivamente a ProjectContext.
    """

    # ==========================================================
    # 1) Construcción de ProjectContext a partir de PlantillaUsuario
    # ==========================================================
    context = ProjectContext(plantilla=datos)

    context = clasificar_viabilidad(context)

    # Convertimos la clasificación almacenada en el contexto
    # al Enum ModoGeneracion esperado por el flujo antiguo.
    try:
        modo = ModoGeneracion(context.clasificacion.upper())
    except Exception:
        modo = ModoGeneracion.ASESOR

    # ==========================================================
    # 2) Flujo original conservado
    # ==========================================================

    fase = detectar_fase(datos)

    if fase == FaseProyecto.FASE_0:
        arquitectura = ""
        opciones = generar_opciones(
            arquitectura="",
            limites=datos.limites,
            tecnologias=datos.tecnologias,
            fase=fase,
        )
    else:
        arquitectura = generar_arquitectura(datos)
        opciones = generar_opciones(
            arquitectura=arquitectura,
            limites=datos.limites,
            tecnologias=datos.tecnologias,
            fase=fase,
        )

    return ResultadoViabilidad(
        modo=modo,
        arquitectura=arquitectura,
        opciones=opciones,
    )
