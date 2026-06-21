"""
Agente de clasificación de viabilidad.

Nueva versión basada en ProjectContext.

Responsabilidad:
- Analizar el contexto actual del proyecto
- Determinar si la PoC es generable
- Determinar el modo de generación
- Enriquecer el ProjectContext con:
    - Resultado de clasificación
    - Decisiones tomadas
    - Modelo utilizado
"""

import json
from typing import Dict

from poc_it.infraestructura.llm_client import chat_completion_json
from poc_it.modulos.models import (
    ProjectContext,
    ModoGeneracion,
)


SYSTEM_MESSAGE = "Responde únicamente con JSON válido."


def _construir_prompt(context: ProjectContext) -> str:
    """
    Construye el prompt dinámicamente a partir del contexto actual.
    No contiene valores hardcodeados específicos de ninguna PoC.
    """

    contexto_serializado = context.model_dump_json(
        indent=2,
        exclude_none=True
    )

    return f"""
Analiza el siguiente contexto de proyecto para determinar su viabilidad técnica.

Devuelve ÚNICAMENTE un JSON válido con esta estructura exacta:

{{
  "generable": true o false,
  "modo": "completo" o "parcial"
}}

Criterios:

1) "generable" depende exclusivamente de si el proyecto:
   - Usa Python
   - Usa FastAPI
   - Es una API backend REST

IMPORTANTE:
Si el proyecto usa Python + FastAPI como API backend REST,
entonces SIEMPRE debe marcarse como "generable": true,
aunque incluya integraciones externas (Google, Cloud Run,
Service Accounts, bases de datos externas, APIs externas, etc.).

Las integraciones externas NUNCA convierten la solución en no generable.
Solo afectan al valor de "modo".

2) "modo" (solo si generable = true):
   - "completo" si funciona completamente en memoria sin dependencias externas
   - "parcial" si requiere integraciones externas, credenciales,
     servicios cloud, bases de datos externas o APIs externas

No incluyas explicaciones.
No escribas texto fuera del JSON.

CONTEXTO DEL PROYECTO:
{contexto_serializado}
"""


def _interpretar_respuesta(data: Dict) -> ModoGeneracion:
    """
    Traduce la respuesta estructurada del modelo a ModoGeneracion.
    """

    generable = data.get("generable")
    modo = data.get("modo")

    if generable is False:
        return ModoGeneracion.ASESOR

    if generable is True:
        if modo == "completo":
            return ModoGeneracion.COMPLETO
        if modo == "parcial":
            return ModoGeneracion.PARCIAL

    # Fallback seguro
    return ModoGeneracion.ASESOR


def clasificar_viabilidad(context: ProjectContext) -> ProjectContext:
    """
    Ejecuta la clasificación de viabilidad sobre el contexto actual.

    - No devuelve valores sueltos.
    - Enriquece el ProjectContext.
    """

    prompt = _construir_prompt(context)

    respuesta = chat_completion_json(
        prompt=prompt,
        system=SYSTEM_MESSAGE,
        temperature=0.0,
        fase="clasificacion",
    )

    try:
        data = json.loads(respuesta)
    except Exception:
        # Fallback defensivo si el modelo devuelve JSON inválido
        context.clasificacion = ModoGeneracion.ASESOR.value
        return context

    modo_generacion = _interpretar_respuesta(data)

    # Registrar resultado en el contexto
    context.viabilidad = None  # Se definirá completamente en fase posterior
    context.agregar_decision(
        descripcion="Clasificación de viabilidad",
        justificacion=f"El modelo determinó modo={modo_generacion.value}",
        impacto="Define el flujo de generación posterior"
    )

    context.registrar_modelo(
        fase="clasificacion",
        modelo="chat_completion_json"  # No hardcodea proveedor concreto
    )

    # Guardamos el modo en decisiones implícitamente.
    # El orquestador puede usar modo_generacion directamente si lo necesita.
    context.clasificacion = modo_generacion.value

    return context
