"""
ScopeGuardian - Oráculo de Viabilidad (Fase A - Versión Profesional y Controlada)

Objetivos de esta versión:
- Mantener código limpio y fácil de leer.
- Evitar JSON.
- Evitar parsing complejo.
- Forzar una única decisión booleana.
- Generar consejo técnico útil aunque esté fuera de alcance.
- Evitar respuestas en bucle del modelo.
- Cortar la salida de forma determinista.

Estrategia:
1. Forzamos al modelo a comenzar con: DECISION: TRUE o FALSE
2. Le obligamos a terminar con el marcador: END_OF_REPORT
3. Limitamos num_predict.
4. Cortamos la respuesta en backend usando ese marcador.
5. El informe incluye una sección técnica útil para el usuario.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import ollama
from pydantic import BaseModel, Field


# ==========================================================
# 1. Contrato de Entrada
# ==========================================================


class UserTemplate(BaseModel):
    nombre: str = Field(...)
    problema: str = Field(...)
    usuarios: str = Field(...)
    funcionalidades: str = Field(...)
    limites: str = Field(...)
    tecnologias: str = Field(...)


# ==========================================================
# 2. Resultado Interno
# ==========================================================


@dataclass
class FeasibilityDecision:
    is_feasible: bool
    report: str


# ==========================================================
# 3. Configuración del Modelo
# ==========================================================


MODEL_NAME: str = "qwen7b:latest"

SYSTEM_PROMPT: str = """
Eres ScopeGuardian.

CAPACIDAD PERMITIDA:
- APIs REST simples
- Python
- FastAPI
- CRUD básico
- Arquitectura monolítica

PROHIBIDO:
- Java
- Spring Boot
- Microservicios
- Frontend
- IA avanzada
- Seguridad compleja

INSTRUCCIONES OBLIGATORIAS:

1. La respuesta DEBE comenzar con exactamente una de estas dos líneas:
   DECISION: TRUE
   o
   DECISION: FALSE

2. Después debes incluir las siguientes secciones:

   ## Motivo
   Explicación breve.

   ## Arquitectura Sugerida
   - Entidades principales
   - Endpoints REST necesarios
   - Posibles DTOs / Schemas
   - Clases o componentes clave
   - Consideraciones técnicas importantes

3. No hagas preguntas.
4. No ofrezcas ayuda adicional.
5. No repitas el mensaje.
6. Termina SIEMPRE con la línea exacta:
   END_OF_REPORT
"""


# ==========================================================
# 4. Generación del Informe
# ==========================================================


def _generate_viability_report(user_data: UserTemplate) -> str:
    prompt = f"""{SYSTEM_PROMPT}

Evalúa la siguiente solicitud:

Nombre: {user_data.nombre}
Problema: {user_data.problema}
Usuarios: {user_data.usuarios}
Funcionalidades: {user_data.funcionalidades}
Límites: {user_data.limites}
Tecnologías: {user_data.tecnologias}
"""

    response = ollama.chat(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        options={
            "temperature": 0.1,
            "num_predict": 600,
            "stop": ["END_OF_REPORT"]
        },
    )

    raw_text = response.get("message", {}).get("content", "").strip()

    # Cortar cualquier texto posterior inesperado
    if "END_OF_REPORT" in raw_text:
        raw_text = raw_text.split("END_OF_REPORT")[0].strip()

    # Fallback de seguridad para evitar bucles largos
    max_length = 3000
    if len(raw_text) > max_length:
        raw_text = raw_text[:max_length]

    return raw_text


# ==========================================================
# 5. Extracción de Decisión
# ==========================================================


def _extract_decision(report: str) -> Optional[bool]:
    match = re.search(r"\b(TRUE|FALSE)\b", report.upper())
    if not match:
        return None
    return match.group(1) == "TRUE"


# ==========================================================
# 6. Oráculo Principal
# ==========================================================


async def judge_feasibility(user_data: UserTemplate) -> FeasibilityDecision:
    report = _generate_viability_report(user_data)
    decision = _extract_decision(report)

    if decision is None:
        raise RuntimeError(
            "No se pudo determinar la decisión TRUE/FALSE en la respuesta del modelo."
        )

    return FeasibilityDecision(
        is_feasible=decision,
        report=report,
    )


# ==========================================================
# 7. Flujo Principal
# ==========================================================


async def run_scope_guardian(user_data: UserTemplate) -> None:
    decision = await judge_feasibility(user_data)

    print("\n==============================")
    print("INFORME DE VIABILIDAD")
    print("==============================\n")
    print(decision.report)
    print("\n==============================\n")

    if not decision.is_feasible:
        print("Resultado: Solicitud fuera de alcance.")
    else:
        print("Resultado: Solicitud dentro del alcance.")
