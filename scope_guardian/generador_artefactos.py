"""
ScopeGuardian - Generador de Artefactos

Responsabilidad:
Expandir un BloqueGenerable en artefactos concretos
(archivos completos) para un proyecto FastAPI existente.

Este módulo:
- NO materializa archivos.
- NO mezcla responsabilidades.
- Reescribe archivos completos (política Opción A).
- Valida sintaxis Python.
- Reintenta UNA vez en caso de error sintáctico.
"""

from __future__ import annotations

import ast
import json
from typing import Dict

import ollama

from scope_guardian.capacidades import BloqueGenerable


# ==========================================================
# CONSTRUCCIÓN DE PROMPT
# ==========================================================


def _construir_prompt(
    bloque: BloqueGenerable,
    descripcion_global: str,
    estructura_actual: Dict[str, str],
) -> str:
    archivos_existentes = "\n".join(f"- {ruta}" for ruta in estructura_actual.keys())

    return f"""
Eres un generador de artefactos para un proyecto FastAPI existente.

Bloque a implementar:
{bloque.descripcion}

Descripción general del proyecto:
{descripcion_global}

Estructura actual del proyecto:
{archivos_existentes}

Reglas:
- Puedes reescribir completamente cualquier archivo necesario.
- NO generes archivos innecesarios.
- Mantén coherencia con FastAPI.
- Devuelve exclusivamente JSON válido con esta estructura:

{{
  "ruta/archivo.py": "contenido completo del archivo",
  ...
}}

- No añadas texto fuera del JSON.
- No generes explicaciones.
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


#Ahora mismo solo verificamos:
#if ".." in ruta:
#En el futuro podríamos añadir:
#   - Evitar rutas absolutas (`/`)
#   - Evitar sobrescribir fuera del proyecto
#   - Validar extensión permitida (.py, .md, .txt, etc.)
def _validar_conflictos(estructura: Dict[str, str]) -> None:
    if not estructura:
        raise ValueError("El bloque no generó artefactos")

    for ruta in estructura.keys():
        if ".." in ruta:
            raise ValueError(f"Ruta inválida detectada: {ruta}")


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


def generar_artefactos_para_bloque(
    bloque: BloqueGenerable,
    descripcion_global: str,
    estructura_actual: Dict[str, str],
) -> Dict[str, str]:
    """
    Genera artefactos completos para un bloque específico.

    Reescribe archivos completos si es necesario.
    Reintenta UNA vez en caso de error sintáctico.
    """

    prompt = _construir_prompt(
        bloque=bloque,
        descripcion_global=descripcion_global,
        estructura_actual=estructura_actual,
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
        estructura_generada = json.loads(contenido)
    except json.JSONDecodeError as e:
        raise ValueError("El LLM no devolvió JSON válido para artefactos") from e

    # Normalizar código
    estructura_generada = {
        ruta: _normalizar_codigo(contenido)
        for ruta, contenido in estructura_generada.items()
    }

    # Validaciones estructurales
    _validar_conflictos(estructura_generada)

    # Validación sintáctica con reintento único
    try:
        _validar_sintaxis_python(estructura_generada)
    except SyntaxError:
        estructura_corregida = estructura_generada.copy()

        for ruta, contenido in estructura_generada.items():
            if ruta.endswith(".py"):
                try:
                    ast.parse(contenido)
                except SyntaxError:
                    corregido = _corregir_archivo_con_error(ruta, contenido)
                    estructura_corregida[ruta] = corregido

        _validar_sintaxis_python(estructura_corregida)
        estructura_generada = estructura_corregida

    return estructura_generada
