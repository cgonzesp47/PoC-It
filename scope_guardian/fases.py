"""
Módulo de detección de fase de madurez del proyecto utilizando LLM.

Responsabilidad:
Delegar en el modelo la clasificación del estado real del usuario.
"""

from enum import Enum
import ollama
from scope_guardian.models import PlantillaUsuario


class FaseProyecto(str, Enum):
    FASE_0 = "FASE_0"  # Nada implementado
    FASE_1 = "FASE_1"  # Arquitectura definida pero sin código
    FASE_2 = "FASE_2"  # Código ya existente


PROMPT_CLASIFICACION_FASE = """
Analiza la descripción del usuario y determina en qué fase se encuentra su proyecto.

FASE_0:
- No tiene nada implementado.
- Parte desde cero.
- Quiere crear la API o proyecto desde el inicio.
- No ha especificado en qué fase se encuentra.

FASE_1:
- Tiene arquitectura o diseño conceptual.
- Aún no hay código implementado.
- Está validando contratos o estructura.

FASE_2:
- Ya existe código implementado.
- Quiere mejorar, refactorizar u optimizar.

Responde EXCLUSIVAMENTE con una de estas opciones:
FASE_0
FASE_1
FASE_2
"""


def detectar_fase(datos: PlantillaUsuario) -> FaseProyecto:
    """
    Utiliza el LLM para clasificar la fase del proyecto.
    """

    descripcion = f"""
Problema:
{datos.problema}

Funcionalidades:
{datos.funcionalidades}

Usuarios:
{datos.usuarios}
"""

    prompt = f"{PROMPT_CLASIFICACION_FASE}\n\nDescripción del usuario:\n{descripcion}"

    respuesta = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt}],
        options={
            "temperature": 0.0,
            "num_predict": 5,
            "stop": ["\n"],
        },
    )

    texto = respuesta.get("message", {}).get("content", "").strip().upper()

    # Normalización defensiva
    if texto.startswith("FASE_1"):
        return FaseProyecto.FASE_1

    if texto.startswith("FASE_2"):
        return FaseProyecto.FASE_2

    # Si el modelo duda, responde vacío o algo ambiguo,
    # asumimos por defecto FASE_0 (caso más conservador)
    return FaseProyecto.FASE_0
