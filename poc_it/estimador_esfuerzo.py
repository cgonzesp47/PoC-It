"""
ScopeGuardian - Estimador de Esfuerzo basado en LLM

Nueva estrategia:
- El LLM infiere el esfuerzo humano (Junior / Senior).
- Se solicita salida estructurada en JSON.
- Se mantiene control limitando rango y ahorro máximo.
- Se usa tiempo real medido para ScopeGuardian.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from poc_it.llm_client import chat_completion_json


# ==========================================================
# MODELO DE RESULTADO
# ==========================================================

@dataclass
class EstimacionEsfuerzo:
    junior_min: float
    junior_max: float
    senior_min: float
    senior_max: float
    horas_scopeguardian: float
    ahorro_vs_junior: float
    ahorro_vs_senior: float


# ==========================================================
# PROMPT LLM
# ==========================================================

PROMPT_ESTIMACION = """
Actúa como un arquitecto software senior con experiencia real en proyectos backend con integraciones cloud.

Debes estimar el tiempo TOTAL necesario para que:

1) Un desarrollador Junior implemente esta PoC completa.
2) Un desarrollador Senior implemente esta PoC completa.

IMPORTANTE:
- Incluye análisis, diseño, lectura de documentación, integración externa, pruebas y despliegue.
- No estimes solo escritura de código.
- Sé realista.
- Devuelve ÚNICAMENTE JSON válido con esta estructura exacta:

{
  "junior_horas": number,
  "senior_horas": number
}

No añadas texto fuera del JSON.
"""


# ==========================================================
# FUNCIÓN PRINCIPAL
# ==========================================================

def calcular_estimacion_llm(
    descripcion_proyecto: str,
    modo: str,
    tiempo_real_scopeguardian_horas: float,
) -> EstimacionEsfuerzo:

    prompt = f"""
{PROMPT_ESTIMACION}

Modo de generación del sistema: {modo}

Descripción técnica de la PoC:

{descripcion_proyecto}
"""

    contenido = chat_completion_json(
        prompt=prompt,
        system="Responde únicamente con JSON válido.",
        temperature=0.0,
        max_tokens=400,
    )

    try:
        data = json.loads(contenido)
        junior = float(data.get("junior_horas", 0))
        senior = float(data.get("senior_horas", 0))
    except Exception:
        # Fallback conservador si falla el modelo
        junior = 40.0
        senior = 20.0

    # Generamos rango ±10%
    junior_min = junior * 0.9
    junior_max = junior * 1.1
    senior_min = senior * 0.9
    senior_max = senior * 1.1

    ahorro_vs_junior = min(
        90.0,
        max(0.0, (junior - tiempo_real_scopeguardian_horas) / junior * 100),
    )

    ahorro_vs_senior = min(
        90.0,
        max(0.0, (senior - tiempo_real_scopeguardian_horas) / senior * 100),
    )

    return EstimacionEsfuerzo(
        junior_min=round(junior_min, 1),
        junior_max=round(junior_max, 1),
        senior_min=round(senior_min, 1),
        senior_max=round(senior_max, 1),
        horas_scopeguardian=round(tiempo_real_scopeguardian_horas, 2),
        ahorro_vs_junior=round(ahorro_vs_junior, 1),
        ahorro_vs_senior=round(ahorro_vs_senior, 1),
    )


# ==========================================================
# MARKDOWN
# ==========================================================

def generar_bloque_markdown(estimacion: EstimacionEsfuerzo) -> str:
    return f"""
## Estimación comparativa de esfuerzo

| Perfil | Tiempo estimado |
|--------|-----------------|
| Junior | {estimacion.junior_min} – {estimacion.junior_max} horas |
| Senior | {estimacion.senior_min} – {estimacion.senior_max} horas |
| ScopeGuardian | {estimacion.horas_scopeguardian} horas (medido) |

### Ahorro estimado

- Reducción frente a Junior: {estimacion.ahorro_vs_junior}%  
- Reducción frente a Senior: {estimacion.ahorro_vs_senior}%  
""".strip()
