"""
Módulo de clasificación de viabilidad (versión JSON estructurado).

Nueva estrategia:

    - Una sola llamada al LLM
    - Salida en JSON pequeño y parseable
    - Sin letras sueltas
    - Sin num_predict=1
    - Sin hardcodeo de listas
    - Sin múltiples capas frágiles

El modelo decide:

{
  "generable": true | false,
  "modo": "completo" | "parcial"
}
"""

import json
import re

from poc_it.llm_client import chat_completion_json
from poc_it.models import PlantillaUsuario, ModoGeneracion


PROMPT_CLASIFICACION_JSON = """
Analiza la siguiente PoC.

Devuelve ÚNICAMENTE un JSON válido con esta estructura exacta:

{
  "generable": true o false,
  "modo": "completo" o "parcial"
}

Reglas:

1) generable (MUY IMPORTANTE):

La decisión de "generable" depende EXCLUSIVAMENTE de la tecnología base.

- generable = false SOLO si:
    - No usa Python
    - O no usa FastAPI
    - O no es una API backend REST

- generable = true SI:
    - Usa Python
    - Y usa FastAPI
    - Y es una API backend REST

IMPORTANTE:
Las integraciones externas (Google, Cloud Run, Service Account, OAuth, bases de datos, etc.)
NUNCA convierten la solución en no generable.
Las integraciones externas SOLO afectan al "modo" (completo o parcial),
pero NO deben afectar a "generable".

2) modo (SOLO si generable = true):

Una solución es "completo" ÚNICAMENTE si:
- Todo funciona en memoria
- No requiere credenciales
- No requiere APIs externas
- No requiere servicios cloud
- No requiere bases de datos externas
- No requiere configuración fuera del propio código

Una solución es "parcial" si incluye CUALQUIERA de los siguientes:
- Integración con Google, AWS, Azure, Stripe u otros servicios externos
- Uso de Service Accounts
- Uso de OAuth o autenticación externa
- Necesidad de credenciales o archivos JSON
- Despliegue en Cloud Run u otra infraestructura cloud
- Dependencia de APIs externas
- Dependencia de bases de datos externas

Ejemplos:
- CRUD en memoria con FastAPI → completo
- FastAPI + Google Drive / Cloud Run / Service Account → parcial
- FastAPI + Base de datos externa → parcial

No expliques nada.
No escribas texto fuera del JSON.
"""


def clasificar_viabilidad(datos: PlantillaUsuario) -> ModoGeneracion:
    """
    Clasifica la viabilidad usando una única llamada estructurada al LLM.
    """

    prompt = f"""{PROMPT_CLASIFICACION_JSON}

PLANTILLA COMPLETA:

NOMBRE:
{datos.nombre}

PROBLEMA:
{datos.problema}

USUARIOS:
{datos.usuarios}

FUNCIONALIDADES:
{datos.funcionalidades}

LIMITES:
{datos.limites}

TECNOLOGIAS:
{datos.tecnologias}
"""

    try:
        texto = chat_completion_json(
            prompt=prompt,
            system="Responde únicamente con JSON válido.",
            temperature=0.0,
        )

        print("\n[DEBUG CLASIFICADOR JSON] Respuesta cruda del LLM:")
        print(texto)
        print("")

        data = json.loads(texto)

        generable = data.get("generable")
        modo = data.get("modo")

        # ==========================================
        # Segunda comprobación semántica (LLM)
        # ==========================================
        # Si el modelo dice generable=false,
        # hacemos una verificación directa del stack.

        if generable is False:
            prompt_verificacion = f"""
La siguiente PoC ha sido clasificada como no generable.

Confirma únicamente si usa Python + FastAPI como backend REST.

Responde SOLO con JSON válido:

{{
  "usa_fastapi_python": true o false
}}

PLANTILLA:

NOMBRE:
{datos.nombre}

PROBLEMA:
{datos.problema}

FUNCIONALIDADES:
{datos.funcionalidades}

TECNOLOGIAS:
{datos.tecnologias}
"""
            try:
                texto_check = chat_completion_json(
                    prompt=prompt_verificacion,
                    system="Responde únicamente con JSON válido.",
                    temperature=0.0,
                )
                data_check = json.loads(texto_check)
                usa_fastapi_python = data_check.get("usa_fastapi_python")

                if usa_fastapi_python is True:
                    print("[DEBUG RECHECK] Confirmado Python + FastAPI. Corrigiendo generable=true.")
                    generable = True
                else:
                    return ModoGeneracion.ASESOR

            except Exception as e:
                print(f"[DEBUG ERROR RECHECK] {e}")
                return ModoGeneracion.ASESOR

        if not generable:
            return ModoGeneracion.ASESOR

        if modo == "completo":
            return ModoGeneracion.COMPLETO

        if modo == "parcial":
            return ModoGeneracion.PARCIAL

        # Si algo raro ocurre
        return ModoGeneracion.PARCIAL

    except Exception as e:
        print(f"[DEBUG ERROR JSON] {e}")
        return ModoGeneracion.ASESOR
