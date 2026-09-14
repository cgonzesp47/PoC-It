from __future__ import annotations
import json
import re
from typing import Optional, Any


def extraer_json_tolerante(respuesta: str) -> Optional[dict]:
    """
    Intenta parsear JSON de forma tolerante.

    Casos soportados:
    - JSON limpio
    - Texto extra antes/después del JSON
    - Respuestas con bloques Markdown (```json ... ```)
    - Respuestas con múltiples bloques: extrae el primer {...} que parezca JSON

    Nota:
    - Si el JSON está truncado y NO hay cierre '}', no se puede recuperar aquí.
    - Si el JSON está truncado pero contiene al menos una '}' final de algún objeto,
      intentamos extraer el mayor bloque {...} posible.
    """
    if not isinstance(respuesta, str) or "{" not in respuesta:
        return None

    s = respuesta.strip()

    # 1) strip de fences Markdown si existen
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL | re.IGNORECASE)
        if m:
            s = m.group(1).strip()

    # 2) intento directo
    #
    # strict=False: el LLM devuelve habitualmente código multi-línea dentro del campo
    # "content" con saltos de línea reales (no escapados como \n), que el parser JSON
    # estricto rechaza como "Invalid control character". Es un fallo intermitente muy
    # frecuente en ficheros de código largos/multi-línea, ajeno a si el contenido en sí
    # es válido. `strict=False` permite caracteres de control literales dentro de strings
    # sin relajar ninguna otra regla de la gramática JSON.
    try:
        return json.loads(s, strict=False)
    except Exception:
        pass

    # 3) fallback "greedy": del primer '{' al último '}' (si existe)
    try:
        start = s.index("{")
        end = s.rindex("}")
        candidate = s[start : end + 1]
        return json.loads(candidate, strict=False)
    except Exception:
        return None
