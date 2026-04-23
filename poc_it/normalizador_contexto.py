"""
PoC-it - Normalizador de Contexto

Responsabilidad:
- Recibir una PlantillaUsuario rellenada.
- Convertirla en un contexto técnico estructurado y normalizado.
- Reducir ambigüedad narrativa.
- Servir como única fuente formal de contexto para el sistema.

Este módulo NO genera código.
Solo estructura y formaliza el contexto.
"""

from __future__ import annotations

import json
from typing import Dict, Any

from poc_it.llm_client import chat_completion_json
from poc_it.models import PlantillaUsuario


PROMPT_NORMALIZACION = """
Actúa como un arquitecto software senior.

Tu tarea es transformar una plantilla de definición de PoC en un contexto técnico estructurado.

Devuelve ÚNICAMENTE JSON válido con la siguiente estructura:

{
  "objetivo_tecnico": "string",
  "actores_principales": ["string"],
  "funcionalidades_clave": ["string"],
  "integraciones_externas": ["string"],
  "restricciones_tecnicas": ["string"],
  "requisitos_no_funcionales": ["string"],
  "riesgos_inherentes": ["string"],
  "complejidad_inferida": "BAJA | MEDIA | ALTA | CRITICA"
}

Reglas:
- No inventes información que no esté implícita.
- Si un campo no aplica, devuelve lista vacía.
- No añadas texto fuera del JSON.
"""


def normalizar_plantilla(plantilla: PlantillaUsuario) -> Dict[str, Any]:
    """
    Normaliza la plantilla del usuario en un contexto técnico estructurado.
    """

    prompt = f"""
{PROMPT_NORMALIZACION}

Plantilla proporcionada por el usuario:

Nombre: {plantilla.nombre}
Problema: {plantilla.problema}
Usuarios: {plantilla.usuarios}
Funcionalidades: {plantilla.funcionalidades}
Límites: {plantilla.limites}
Tecnologías declaradas: {plantilla.tecnologias}
"""

    respuesta = chat_completion_json(
        prompt=prompt,
        system="Responde exclusivamente con JSON válido.",
        temperature=0.0,
        max_tokens=800,
        fase="normalizacion_contexto",
    )

    try:
        data = json.loads(respuesta)
    except Exception:
        # Fallback mínimo estructurado
        data = {
            "objetivo_tecnico": plantilla.problema,
            "actores_principales": [],
            "funcionalidades_clave": [],
            "integraciones_externas": [],
            "restricciones_tecnicas": [],
            "requisitos_no_funcionales": [],
            "riesgos_inherentes": [],
            "complejidad_inferida": "MEDIA",
        }

    return data
