"""
Módulo de diseño arquitectónico.
Responsabilidad única: generar diseño estructural (sin código).
"""

from poc_it.llm_client import chat_completion_text
from poc_it.models import PlantillaUsuario


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

    texto = chat_completion_text(
        prompt=prompt,
        temperature=0.2,
        max_tokens=700,
    ).strip()

    if "END_OF_REPORT" in texto:
        texto = texto.split("END_OF_REPORT")[0].strip()

    return texto
