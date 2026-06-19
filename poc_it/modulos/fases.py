"""
Módulo de detección de fase de madurez del proyecto utilizando LLM.

Responsabilidad:
Delegar en el modelo la clasificación del estado real del usuario.
"""

from enum import Enum
from poc_it.modulos.models import PlantillaUsuario
from poc_it.infraestructura.llm_client import chat_completion_json


class FaseProyecto(str, Enum):
    FASE_0 = "FASE_0"  # Nada implementado
    FASE_1 = "FASE_1"  # Arquitectura definida pero sin código
    FASE_2 = "FASE_2"  # Código ya existente


PROMPT_EXTRACCION_FASE = """
Analiza la descripción del usuario y responde EXCLUSIVAMENTE en formato JSON válido.

Debes inferir señales semánticas, NO decidir la fase directamente.

Devuelve exactamente este esquema:

{
  "menciona_codigo_existente": boolean,
  "menciona_refactor_o_mejora": boolean,
  "menciona_elementos_concretos_de_codigo": boolean,
  "nivel_confianza": number
}

Reglas:
- menciona_codigo_existente: true si el usuario afirma tener código ya implementado.
- menciona_refactor_o_mejora: true si habla de refactorizar, optimizar o mejorar algo existente.
- menciona_elementos_concretos_de_codigo: true si menciona clases, controladores, servicios, repositorios propios.
- nivel_confianza: número entre 0 y 1 indicando qué tan seguro estás del análisis.
- No añadas texto fuera del JSON.
"""


def detectar_fase(datos: PlantillaUsuario) -> FaseProyecto:
    """
    Detecta la fase del proyecto mediante extracción estructurada
    de señales semánticas usando el LLM en modo JSON.
    """

    descripcion = f"""
Problema:
{datos.problema}

Funcionalidades:
{datos.funcionalidades}

Usuarios:
{datos.usuarios}
"""

    prompt = f"{PROMPT_EXTRACCION_FASE}\n\nDescripción del usuario:\n{descripcion}"

    data = chat_completion_json(
        prompt=prompt,
        system=None,
        temperature=0.0,
        max_tokens=200,
        fase="clasificacion",
    )

    try:
        import json
        señales = json.loads(data)
    except Exception:
        # Fallback conservador
        return FaseProyecto.FASE_0

    menciona_codigo = señales.get("menciona_codigo_existente", False)
    menciona_refactor = señales.get("menciona_refactor_o_mejora", False)
    menciona_elementos = señales.get("menciona_elementos_concretos_de_codigo", False)

    # Lógica estructural mínima y conservadora
    if menciona_codigo or menciona_refactor or menciona_elementos:
        return FaseProyecto.FASE_2

    return FaseProyecto.FASE_0
