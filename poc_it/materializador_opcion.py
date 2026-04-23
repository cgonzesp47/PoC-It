"""
ScopeGuardian - Materializador de Opciones

Responsabilidad:
Dado el texto estructurado de una opción seleccionada por el usuario,
generar un prompt técnico detallado y ejecutar una nueva llamada al LLM
para desarrollar dicha opción.

Diseño:
- No hardcodea tipos de opciones
- Trabaja con la plantilla generada por el modelo
- Separa extracción → expansión → materialización
"""

from __future__ import annotations

import re
from poc_it.llm_client import chat_completion_text


# ==========================================================
# 1. Parseo estructurado de opción
# ==========================================================

def parsear_opcion(opcion_texto: str) -> dict:
    """
    Extrae los campos estructurados de la plantilla:
    - Clase afectada
    - Cambio específico
    - Impacto técnico
    """

    patron_clase = r"Clase afectada:\s*(.*)"
    patron_cambio = r"Cambio específico:\s*(.*)"
    patron_impacto = r"Impacto técnico:\s*(.*)"

    clase = re.search(patron_clase, opcion_texto)
    cambio = re.search(patron_cambio, opcion_texto)
    impacto = re.search(patron_impacto, opcion_texto)

    return {
        "clase_afectada": clase.group(1).strip() if clase else "",
        "cambio_especifico": cambio.group(1).strip() if cambio else "",
        "impacto_tecnico": impacto.group(1).strip() if impacto else "",
    }


# ==========================================================
# 2. Generación de prompt detallado
# ==========================================================

def construir_prompt_materializacion(
    datos_opcion: dict,
    contexto_proyecto: str,
) -> str:
    """
    Construye un prompt más detallado para que el LLM
    expanda técnicamente la opción seleccionada.
    """

    return f"""
Contexto del proyecto:

{contexto_proyecto}

El usuario ha decidido implementar la siguiente mejora:

Clase afectada: {datos_opcion["clase_afectada"]}
Cambio específico: {datos_opcion["cambio_especifico"]}
Impacto técnico: {datos_opcion["impacto_tecnico"]}

Tarea:

1. Expande técnicamente el cambio específico.
2. Describe pasos concretos para implementarlo.
3. Mantén coherencia con el contexto del proyecto.
4. No hagas suposiciones no justificadas.
5. No generes explicación meta.

Si el cambio implica código, puedes generarlo.
Si implica diseño o configuración, descríbelo de forma estructurada.

Termina con END_OF_REPORT.
"""


# ==========================================================
# 3. Ejecución de materialización
# ==========================================================

def materializar_opcion(
    opcion_texto: str,
    contexto_proyecto: str,
) -> str:
    """
    Orquesta:
    - Parseo
    - Construcción de prompt
    - Llamada al modelo
    """

    datos = parsear_opcion(opcion_texto)

    prompt = construir_prompt_materializacion(
        datos_opcion=datos,
        contexto_proyecto=contexto_proyecto,
    )

    texto = chat_completion_text(
        prompt=prompt,
        system=None,
        temperature=0.2,
        max_tokens=900,
        fase="documentacion",
    ).strip()

    if "END_OF_REPORT" in texto:
        texto = texto.split("END_OF_REPORT")[0].strip()

    return texto
