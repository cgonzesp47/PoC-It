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
from datetime import datetime
from pathlib import Path
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
  "complejidad_inferida": "BAJA | MEDIA | ALTA | CRITICA",

  "contratos_api": [
    {
      "method": "GET | POST | PUT | PATCH | DELETE",
      "path": "/ruta",

      "request": {
        "type": "json | multipart | query | none",
        "schema_hint": {},

        "evidence": "Cita literal de la plantilla que justifica el tipo de request (si existe)",
        "assumption": "Si no hay evidencia suficiente, explica brevemente la asunción (o deja vacío)"
      },

      "response": {
        "json_example": {},
        "evidence": "Cita literal de la plantilla que justifica la respuesta esperada (si existe)"
      },

      "notes": "string opcional"
    }
  ]
}

Reglas:
- No inventes información que no esté implícita.
- Si un campo no aplica, devuelve lista vacía.
- No añadas texto fuera del JSON.

Reglas específicas para contratos_api (MUY IMPORTANTE):
- Solo incluye endpoints que el usuario haya descrito explícitamente (método y/o ruta).
- Evidencia (OBLIGATORIO):
  - Para cada contrato, rellena request.evidence y/o response.evidence con una CITA LITERAL (copiada tal cual) de la plantilla.
  - Si NO existe evidencia literal suficiente para fijar request.type o schema_hint:
    - usa request.type="none" y schema_hint vacío,
    - y explica en request.assumption por qué no se pudo inferir.
- Prohibido “inventar” type/schema_hint sin evidence.
- Si el usuario menciona ejemplo de respuesta, refleja response.json_example y añade response.evidence con la cita literal.
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
        max_tokens=900,
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
            "contratos_api": [],
        }

    # Asegurar clave para trazabilidad (evita que se "pierda" silenciosamente)
    if not isinstance(data, dict):
        data = {
            "objetivo_tecnico": plantilla.problema,
            "actores_principales": [],
            "funcionalidades_clave": [],
            "integraciones_externas": [],
            "restricciones_tecnicas": [],
            "requisitos_no_funcionales": [],
            "riesgos_inherentes": [],
            "complejidad_inferida": "MEDIA",
            "contratos_api": [],
        }

    data.setdefault("contratos_api", [])

    # Persistencia debug del contexto normalizado final (para verificar que llega al SPEC)
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (debug_dir / f"contexto_normalizado_{ts}.json").write_text(
            json.dumps(
                {
                    "timestamp": ts,
                    "plantilla": {
                        "nombre": plantilla.nombre,
                        "problema": plantilla.problema,
                        "usuarios": plantilla.usuarios,
                        "funcionalidades": plantilla.funcionalidades,
                        "limites": plantilla.limites,
                        "tecnologias": plantilla.tecnologias,
                    },
                    "contexto_normalizado": data,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        # no romper ejecución por debug
        pass

    return data
