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

Regla principal:

Es A si:
- La tecnología base es Python
- Utiliza FastAPI como framework principal
- Es una API REST
- Aunque incluya integraciones externas (Google Cloud, OAuth, Stripe, bases de datos, etc.)

Es B solo si:
- No usa Python
- No usa FastAPI
- Es otro framework (Spring Boot, Node, .NET, etc.)
- O no es una API REST backend

IMPORTANTE:
Las integraciones externas NO convierten la solución en NO_GENERABLE.
En esos casos podrá generarse parcialmente.

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
            "num_predict": 15,
        },
    )

    texto = respuesta.get("message", {}).get("content", "").strip().upper()

    # Normalización defensiva
    texto = texto.replace(")", "").replace(".", "").strip()

    # Aceptamos múltiples variantes robustamente
    if texto.startswith("A") or "GENERABLE_AUTOMATICAMENTE" in texto:
        return True

    if texto.startswith("B") or "NO_GENERABLE_AUTOMATICAMENTE" in texto:
        return False

    # Fallback conservador pero informativo
    # Si contiene FASTAPI explícitamente, asumimos generable
    tecnologias = (datos.tecnologias or "").lower()
    if "fastapi" in tecnologias:
        return True

    return False
