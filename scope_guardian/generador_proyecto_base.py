"""
ScopeGuardian - Generador de Proyecto Base

Responsabilidad:
Generar la estructura mínima viable de un proyecto FastAPI
en función de los bloques generables detectados.

Este módulo:
- NO materializa archivos en disco.
- NO mezcla responsabilidades.
- NO contiene estructura hardcodeada.
- Valida sintaxis Python antes de devolver resultados.
- Reintenta una vez si detecta error sintáctico.
"""

from __future__ import annotations

import ast
import json
from typing import Dict, List

import ollama


# ==========================================================
# CONSTRUCCIÓN DE PROMPT
# ==========================================================


def _construir_prompt(
    bloques_generables: List[str],
    descripcion_global: str,
) -> str:
    bloques_texto = "\n".join(f"- {b}" for b in bloques_generables)

    return f"""
Eres un generador de proyectos backend FastAPI.

Debes generar la estructura mínima viable de un proyecto
que permita implementar estos bloques:

{bloques_texto}

Descripción general del proyecto:
{descripcion_global}

Dominio:
- Python 3.11
- FastAPI
- Proyecto backend REST
- No incluir funcionalidades adicionales no solicitadas

Devuelve exclusivamente JSON válido con esta estructura:

{{
  "ruta/archivo.py": "contenido del archivo",
  ...
}}

Reglas:
- Incluir solo archivos estrictamente necesarios.
- No incluir explicaciones.
- No incluir texto fuera del JSON.
- El proyecto debe poder arrancar con: uvicorn main:app --reload
"""
    

# ==========================================================
# UTILIDADES INTERNAS
# ==========================================================


def _normalizar_codigo(contenido: str) -> str:
    contenido = contenido.strip()

    if contenido.startswith("```python"):
        contenido = contenido.replace("```python", "").replace("```", "").strip()
    elif contenido.startswith("```"):
        contenido = contenido.replace("```", "").strip()

    return contenido


def _validar_sintaxis_python(estructura: Dict[str, str]) -> None:
    for ruta, contenido in estructura.items():
        if ruta.endswith(".py"):
            try:
                ast.parse(contenido)
            except SyntaxError as e:
                raise SyntaxError(f"Error sintáctico en {ruta}: {e}") from e


def _validar_coherencia_minima(estructura: Dict[str, str]) -> None:
    if "main.py" not in estructura:
        raise ValueError("La estructura generada no contiene main.py")

    main_content = estructura["main.py"]

    if "FastAPI" not in main_content:
        raise ValueError("main.py no contiene referencia a FastAPI")

    # Frágil, el modelo podría generar: application = FastAPI() o api = FastAPI()
    if "app = FastAPI" not in main_content:
        raise ValueError("main.py no define instancia FastAPI")


def _corregir_archivo_con_error(ruta: str, contenido: str) -> str:
    prompt = f"""
El siguiente archivo Python tiene un error sintáctico.
Corrige exclusivamente el archivo manteniendo su intención original.

Archivo: {ruta}

Contenido:
```python
{contenido}
```

Devuelve SOLO código Python válido.
"""

    response = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt}],
        options={
            "temperature": 0.0,
            "num_predict": 2000,
        },
    )

    corregido = response.get("message", {}).get("content", "").strip()
    return _normalizar_codigo(corregido)


# ==========================================================
# FUNCIÓN PRINCIPAL
# ==========================================================


def generar_proyecto_base(
    nombre_proyecto: str,
    bloques_generables: List[str],
    descripcion_global: str,
) -> Dict[str, str]:
    """
    Genera estructura mínima viable FastAPI validada.

    Reintenta UNA vez en caso de error sintáctico.
    """

    prompt = _construir_prompt(
        bloques_generables=bloques_generables,
        descripcion_global=descripcion_global,
    )

    response = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt}],
        format="json",
        options={
            "temperature": 0.0,
            "num_predict": 3000,
        },
    )

    contenido = response.get("message", {}).get("content", "{}")

    try:
        estructura = json.loads(contenido)
    except json.JSONDecodeError as e:
        raise ValueError("El LLM no devolvió JSON válido para la estructura base") from e

    # Normalizar código
    estructura = {
        ruta: _normalizar_codigo(contenido)
        for ruta, contenido in estructura.items()
    }

    # Validación sintáctica con reintento único
    try:
        _validar_sintaxis_python(estructura)
    except SyntaxError as error:
        # Reintentar solo archivos con error
        estructura_corregida = estructura.copy()

        for ruta, contenido in estructura.items():
            if ruta.endswith(".py"):
                try:
                    ast.parse(contenido)
                except SyntaxError:
                    corregido = _corregir_archivo_con_error(ruta, contenido)
                    estructura_corregida[ruta] = corregido

        # Validar nuevamente
        _validar_sintaxis_python(estructura_corregida)
        estructura = estructura_corregida

    # Validación mínima de coherencia
    _validar_coherencia_minima(estructura)

    return estructura
