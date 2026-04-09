"""
PoC-it – Generador Libre de Proyecto Completo (Arquitectura basada en LLM cloud)

Nueva estrategia:

- El modelo genera el proyecto completo.
- Devuelve JSON estructurado con lista de archivos.
- No imponemos arquitectura.
- No imponemos capas.
- No imponemos scaffolding.
- El modelo decide estructura.

Mantenemos:
- Validación sintáctica AST para cada archivo Python.
- Reintentos automáticos si hay errores.
"""

from __future__ import annotations

import ast
import json
from typing import Dict, Any, List

from poc_it.llm_client import chat_completion_json


# ==========================================================
# VALIDACIÓN SINTÁCTICA
# ==========================================================


def _codigo_python_valido(codigo: str) -> bool:
    try:
        ast.parse(codigo)
        return True
    except Exception:
        return False


def _validar_proyecto(files: List[Dict[str, str]]) -> bool:
    """
    Valida sintácticamente todos los archivos .py generados.
    """
    for f in files:
        path = f.get("path", "")
        content = f.get("content", "")

        if path.endswith(".py"):
            if not _codigo_python_valido(content):
                return False

    return True


# ==========================================================
# PROMPT LIBRE DE GENERACIÓN COMPLETA
# ==========================================================


def _construir_prompt_proyecto_completo(descripcion_global: str) -> str:
    return f"""
Eres un arquitecto backend senior experto en diseño de APIs en Python.

Tu tarea es generar una PoC backend COMPLETA y ejecutable basada en la siguiente descripción.

DESCRIPCIÓN DE LA POC:
{descripcion_global}

REQUISITOS:

- Usa Python y FastAPI.
- La arquitectura queda a tu criterio.
- Puedes organizar carpetas libremente.
- Debe ser ejecutable con uvicorn.
- Incluye requirements.txt.
- Incluye README.md breve.
- No incluyas comentarios meta.
- No incluyas texto fuera del JSON.

Devuelve EXCLUSIVAMENTE JSON válido con esta estructura:

{{
  "files": [
    {{
      "path": "ruta/archivo.ext",
      "content": "contenido completo del archivo"
    }}
  ]
}}

Reglas importantes:
- No generes texto fuera del JSON.
- Cada archivo debe incluir todo su contenido.
- Los archivos Python deben ser sintácticamente válidos.
- No uses bloques ```.

Genera el proyecto ahora.
"""


# ==========================================================
# GENERACIÓN PRINCIPAL CON REINTENTOS
# ==========================================================


def generar_proyecto_completo(
    descripcion_global: str,
    intentos: int = 3,
) -> Dict[str, Any]:
    """
    Genera el proyecto completo delegando la arquitectura al modelo.
    Mantiene validación AST y reintentos.
    """

    prompt = _construir_prompt_proyecto_completo(descripcion_global)

    for intento in range(intentos):
        respuesta_json = chat_completion_json(
            prompt=prompt,
            system=None,
            temperature=0.2,
            max_tokens=6000,
        )

        try:
            data = json.loads(respuesta_json)
        except Exception:
            continue

        files = data.get("files", [])

        if not isinstance(files, list) or not files:
            continue

        if _validar_proyecto(files):
            return {"files": files}

        # Si falla validación AST, pedimos corrección explícita
        prompt = f"""
El proyecto generado anteriormente tiene errores de sintaxis en archivos Python.

Corrige los errores y devuelve nuevamente el JSON completo siguiendo exactamente el mismo formato.

Proyecto anterior:
{respuesta_json}
"""

    # Si tras reintentos falla, devolvemos estructura mínima
    return {"files": []}
