"""
ScopeGuardian - Módulo de Capacidades del Sistema

Objetivo:
Determinar qué partes de una PoC pueden ser generadas automáticamente
y cuáles requieren intervención manual (credenciales, permisos,
infraestructura externa, decisiones organizativas).

Diseño:
- No hardcodea tecnologías específicas.
- No contiene lógica acoplada a Google, Spring o similares.
- Se basa en extracción estructurada vía LLM.
- Separa:
    1) Extracción semántica
    2) Clasificación de capacidad
    3) Resultado estructurado
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any
import json
from poc_it.llm_client import chat_completion_json


# ==========================================================
# MODELOS
# ==========================================================

@dataclass
class BloqueGenerable:
    descripcion: str


@dataclass
class BloqueManual:
    descripcion: str
    motivo: str


@dataclass
class EvaluacionCapacidades:
    generables: List[BloqueGenerable]
    manuales: List[BloqueManual]


# ==========================================================
# PROMPT DE EXTRACCIÓN
# ==========================================================

PROMPT_EVALUAR_CAPACIDADES = """
Analiza la descripción técnica de la PoC y clasifica sus componentes en:

1) BLOQUES_GENERABLES_AUTOMATICAMENTE
2) BLOQUES_QUE_REQUIEREN_INTERVENCION_MANUAL

Devuelve EXCLUSIVAMENTE JSON válido con esta estructura:

{
  "generables": [
    { "descripcion": "..." }
  ],
  "manuales": [
    { 
      "descripcion": "...",
      "motivo": "..."
    }
  ]
}

Reglas:
- Un bloque es generable si puede implementarse solo con código,
  configuración o archivos generados automáticamente.
- Un bloque es manual si requiere:
    - Credenciales reales
    - Permisos externos
    - Infraestructura externa
    - Decisiones organizativas
- No añadas texto fuera del JSON.
"""


# ==========================================================
# FUNCIÓN PRINCIPAL
# ==========================================================

def evaluar_capacidades(descripcion_proyecto: str) -> EvaluacionCapacidades:
    """
    Orquesta:
    - Extracción semántica estructurada
    - Clasificación en generables vs manuales
    """

    prompt = f"""
{PROMPT_EVALUAR_CAPACIDADES}

Descripción de la PoC:

{descripcion_proyecto}
"""

    contenido = chat_completion_json(
        prompt=prompt,
        system=None,
        temperature=0.0,
        max_tokens=800,
        fase="estimacion",
    )

    try:
        data: Dict[str, Any] = json.loads(contenido)
    except Exception:
        # Fallback seguro
        return EvaluacionCapacidades(generables=[], manuales=[])

    generables = [
        BloqueGenerable(**bloque)
        for bloque in data.get("generables", [])
        if isinstance(bloque, dict)
    ]

    manuales = [
        BloqueManual(**bloque)
        for bloque in data.get("manuales", [])
        if isinstance(bloque, dict)
    ]

    return EvaluacionCapacidades(
        generables=generables,
        manuales=manuales,
    )
