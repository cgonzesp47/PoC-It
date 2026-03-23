"""
Módulo de clasificación de viabilidad.
Responsabilidad única: decidir si la PoC es generable automáticamente.
"""

import ollama
from scope_guardian.models import PlantillaUsuario


PROMPT_CLASIFICACION = """
Debes elegir UNA única opción.

A) GENERABLE_AUTOMATICAMENTE
B) NO_GENERABLE_AUTOMATICAMENTE

Solo es A si la solución es:
- API REST simple
- Python
- FastAPI
- CRUD básico

En cualquier otro caso es B.

Responde SOLO con:
A
o
B
"""


def clasificar_viabilidad(datos: PlantillaUsuario) -> bool:
    prompt = f"""{PROMPT_CLASIFICACION}

Tecnologias:
{datos.tecnologias}
"""

    respuesta = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt}],
        options={
            "temperature": 0.0,
            "num_predict": 3,
            "stop": ["\n"],
        },
    )

    texto = respuesta.get("message", {}).get("content", "").strip().upper()

    if texto.startswith("A"):
        return True
    if texto.startswith("B"):
        return False

    return False
