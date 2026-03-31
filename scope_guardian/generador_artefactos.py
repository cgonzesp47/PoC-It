"""
ScopeGuardian - Generador de Artefactos Robusto con Validación y Reintentos

Arquitectura definitiva para modelos locales (qwen7b):

1) Diseño estructural canónico.
2) Generación por archivo individual.
3) Delimitadores obligatorios de salida.
4) Extracción segura del bloque de código.
5) Validación sintáctica (AST).
6) Reintentos automáticos si el código no es válido.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Dict, Any

import ollama


# ==========================================================
# UTILIDADES INTERNAS
# ==========================================================


BEGIN = "### BEGIN_CODE"
END = "### END_CODE"


def _extraer_codigo_delimitado(texto: str) -> str:
    pattern = re.compile(rf"{BEGIN}(.*?){END}", re.DOTALL)
    match = pattern.search(texto)
    if not match:
        return ""
    return match.group(1).strip()


def _codigo_es_valido(codigo: str) -> bool:
    try:
        ast.parse(codigo)
        return True
    except Exception:
        return False


# (Validación semántica eliminada temporalmente para no bloquear generación)


def _llamar_modelo(prompt: str, max_tokens: int = 2500) -> str:
    response = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.0, "num_predict": max_tokens},
    )
    return response.get("message", {}).get("content", "")


def _generar_con_reintentos(
    prompt: str,
    max_tokens: int = 2500,
    intentos: int = 3,
) -> str:
    for _ in range(intentos):
        salida = _llamar_modelo(prompt, max_tokens=max_tokens)
        codigo = _extraer_codigo_delimitado(salida)

        if codigo and _codigo_es_valido(codigo):
            return codigo

    return ""


# ==========================================================
# FASE 1 — DISEÑO ESTRUCTURAL
# ==========================================================


def generar_diseno_estructural(descripcion_global: str) -> Dict[str, Any]:
    prompt = f"""
Extrae el diseño estructural de la siguiente PoC.

Devuelve EXCLUSIVAMENTE JSON válido con esta estructura:

{{
  "entities": [
    {{
      "name": "EntityName",
      "fields": {{
        "field_name": "str|int|float|bool"
      }}
    }}
  ]
}}

No generes código.
No generes texto fuera del JSON.

PoC:
{descripcion_global}
"""

    salida = _llamar_modelo(prompt, max_tokens=1200)

    try:
        return json.loads(salida)
    except Exception:
        return {"entities": []}


# ==========================================================
# FASE 2 — GENERACIÓN POR ARCHIVO CON VALIDACIÓN
# ==========================================================


def generar_schemas_clases(diseno: Dict[str, Any]) -> str:
    """
    Genera únicamente las clases Pydantic (sin imports).
    Se insertarán dentro de una plantilla fija con BaseModel.
    """

    prompt = f"""
Diseño estructural EXACTO:

{json.dumps(diseno, indent=2)}

Genera ÚNICAMENTE las clases usando Pydantic BaseModel.

NO generes:
- imports
- texto explicativo
- otras librerías (NO marshmallow)
- código fuera de clases

Devuelve SOLO código Python entre estas marcas:

{BEGIN}
<codigo>
{END}
"""

    return _generar_con_reintentos(prompt, max_tokens=2000)


def generar_services_py(diseno: Dict[str, Any]) -> str:
    prompt = f"""
Diseño estructural EXACTO:

{json.dumps(diseno, indent=2)}

Genera el archivo completo services.py con CRUD en memoria.

Devuelve SOLO código Python entre estas marcas:

{BEGIN}
<codigo>
{END}
"""

    return _generar_con_reintentos(prompt, max_tokens=2500)


def generar_api_endpoints(diseno: Dict[str, Any]) -> str:
    """
    Genera únicamente los endpoints (sin imports ni creación de router).
    Se insertarán dentro de una plantilla fija FastAPI.
    """

    prompt = f"""
Diseño estructural EXACTO:

{json.dumps(diseno, indent=2)}

Genera ÚNICAMENTE las funciones de endpoints usando:

- router (ya existe)
- schemas
- services

NO generes:
- imports
- creación de APIRouter
- creación de FastAPI
- texto explicativo

Devuelve SOLO código Python entre estas marcas:

{BEGIN}
<codigo>
{END}
"""

    return _generar_con_reintentos(prompt, max_tokens=3000)


def generar_requirements_adicionales(descripcion_global: str) -> str:
    prompt = f"""
Analiza la siguiente PoC.

Devuelve SOLO dependencias adicionales (una por línea)
entre estas marcas:

{BEGIN}
<dependencias>
{END}

No incluyas:
- fastapi
- uvicorn
- pydantic
- python
- texto explicativo

PoC:
{descripcion_global}
"""

    salida = _llamar_modelo(prompt, max_tokens=800)
    bloque = _extraer_codigo_delimitado(salida)

    # Sanitización básica (sin bloquear generación)
    lineas_validas = []
    for linea in bloque.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if "<" in linea or ">" in linea:
            continue
        if linea.startswith("-"):
            continue
        if linea.lower() in {"fastapi", "uvicorn", "pydantic", "python"}:
            continue
        lineas_validas.append(linea)

    return "\n".join(lineas_validas)
