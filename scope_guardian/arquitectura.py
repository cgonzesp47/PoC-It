"""
Módulo de diseño arquitectónico.
Responsabilidad única: generar diseño estructural (sin código).
"""

import ollama
from scope_guardian.models import PlantillaUsuario


PROMPT_ARQUITECTURA = """
Usa EXACTAMENTE estas tecnologías:
{tecnologias}

IMPORTANTE:
- NO generes bloques de código.
- NO incluyas ``` ni implementación detallada.
- Este paso es SOLO diseño estructural.
- Define contratos, endpoints y responsabilidades, pero no desarrolles código.

Diseña la siguiente PoC:

{descripcion}

Completa esta estructura sin añadir secciones adicionales:

## ENTIDADES
- NombreClase (campos y propósito)

## ENDPOINTS
- METODO /ruta
  - Qué hace
  - Request (estructura JSON)
  - Response (estructura JSON)
  - Códigos HTTP

## CAPAS Y RESPONSABILIDADES
- Controller: responsabilidad
- Service: responsabilidad
- Repositorio en memoria: responsabilidad

## REGLAS Y LIMITACIONES
- Cómo se respeta almacenamiento en memoria
- Cómo se gestionan errores
- Cómo se simula latencia (conceptualmente)

Termina con END_OF_REPORT.
"""


def generar_arquitectura(datos: PlantillaUsuario) -> str:
    descripcion = f"""
Problema:
{datos.problema}

Funcionalidades:
{datos.funcionalidades}

Límites:
{datos.limites}
"""

    prompt = PROMPT_ARQUITECTURA.format(
        tecnologias=datos.tecnologias,
        descripcion=descripcion,
    )

    respuesta = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt}],
        options={
            "temperature": 0.2,
            "num_predict": 700,
            "stop": ["END_OF_REPORT"],
        },
    )

    texto = respuesta.get("message", {}).get("content", "").strip()

    if "END_OF_REPORT" in texto:
        texto = texto.split("END_OF_REPORT")[0].strip()

    return texto
