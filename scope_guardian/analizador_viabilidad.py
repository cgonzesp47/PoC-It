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

from scope_guardian.models import PlantillaUsuario, ResultadoViabilidad
from scope_guardian.clasificador import clasificar_viabilidad
from scope_guardian.arquitectura import generar_arquitectura
from scope_guardian.opciones import generar_opciones
from scope_guardian.fases import detectar_fase, FaseProyecto


# ==========================================================
# 1. Modelo de Entrada
# ==========================================================


# Los modelos han sido extraídos a scope_guardian.models


# ==========================================================
# 3. FASE 1 — CLASIFICACIÓN (Ultra minimalista)
# ==========================================================


# Este módulo ahora actúa únicamente como orquestador ligero.


async def analizar_viabilidad(datos: PlantillaUsuario) -> ResultadoViabilidad:
    decision = clasificar_viabilidad(datos)

    # Detectar fase (actualmente forzada a FASE_0)
    fase = detectar_fase(datos)

    # 🔹 NUEVO FLUJO:
    # En FASE_0 no generamos arquitectura primero.
    # Generamos directamente opciones estratégicas de arranque.
    if fase == FaseProyecto.FASE_0:
        arquitectura = ""  # No inducimos sesgo arquitectónico
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
        puede_generarse_automaticamente=decision,
        arquitectura=arquitectura,
        opciones=opciones,
    )
