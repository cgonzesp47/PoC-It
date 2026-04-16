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


def _construir_prompt_proyecto_completo(
    descripcion_global: str,
    contexto_normalizado: dict | None = None,
) -> str:
    if contexto_normalizado:
        objetivo = contexto_normalizado.get("objetivo_tecnico", "")
        actores = contexto_normalizado.get("actores_principales", [])
        funcionalidades = contexto_normalizado.get("funcionalidades_clave", [])
        integraciones = contexto_normalizado.get("integraciones_externas", [])
        restricciones = contexto_normalizado.get("restricciones_tecnicas", [])

        descripcion_structurada = f"""
OBJETIVO TÉCNICO:
{objetivo}

ACTORES PRINCIPALES:
{chr(10).join(f"- {a}" for a in actores) if actores else "- No especificados"}

FUNCIONALIDADES CLAVE:
{chr(10).join(f"- {f}" for f in funcionalidades) if funcionalidades else "- No especificadas"}

INTEGRACIONES EXTERNAS:
{chr(10).join(f"- {i}" for i in integraciones) if integraciones else "- No especificadas"}

RESTRICCIONES TÉCNICAS:
{chr(10).join(f"- {r}" for r in restricciones) if restricciones else "- No especificadas"}
"""
    else:
        descripcion_structurada = f"""
DESCRIPCIÓN DE LA POC:
{descripcion_global}
"""

    return f"""
Eres un arquitecto backend senior experto en diseño de APIs en Python.

Tu tarea es generar una PoC backend COMPLETA y ejecutable basada en la siguiente definición.

{descripcion_structurada}

REQUISITOS GENERALES:

- Usa Python y FastAPI como framework base.
- El proyecto debe estar organizado en múltiples archivos y carpetas.
- No está permitido implementar toda la lógica en un único archivo main.py.
- Debe existir como mínimo:
  - Carpeta app/ para endpoints o capa API.
  - Módulo o carpeta dedicada a servicios o lógica de negocio.
  - Módulo de configuración desacoplado.
- Si existe integración externa, debe existir una carpeta o módulo específico (por ejemplo integration/) para aislarla.
- Separa claramente:
  - Capa API (endpoints)
  - Capa de lógica o integración externa
  - Configuración
- No inicialices clientes externos en el módulo global.
- Debe ser ejecutable con uvicorn.
- Incluye requirements.txt.
- Incluye README.md mínimo que describa estructura básica del proyecto (el README profesional será generado posteriormente por el sistema).
- Si existen partes que no pueden automatizarse completamente, incluye también README_MANUAL.md mínimo explicando solo qué debe configurarse manualmente.
- No incluyas comentarios meta.
- No incluyas texto fuera del JSON.

REGLAS ESTRUCTURALES OBLIGATORIAS:

1. El proyecto debe tener al menos 3 archivos Python diferenciados.
2. No se acepta una solución monolítica en un único archivo.
3. La integración externa debe estar desacoplada de los endpoints.
4. La configuración debe poder modificarse sin alterar la lógica de negocio.
5. Si no cumples estas reglas, reestructura el proyecto antes de devolver el JSON.

GOBERNANZA TÉCNICA OBLIGATORIA:

1. Nunca generes credenciales reales ni claves privadas ficticias.
2. No generes archivos de credenciales simuladas (por ejemplo JSON con claves inventadas).
3. Si una integración requiere:
   - Credenciales reales
   - Permisos IAM
   - Servicios cloud habilitados
   - Certificados
   - Variables de entorno sensibles
   - Configuración externa no automatizable
   entonces:
     - Implementa el código preparado para esa integración.
     - Explica en README_MANUAL.md qué debe hacer el usuario manualmente.
     - No simules valores sensibles.
4. No inventes claves PEM ni tokens falsos funcionales.
5. Si algo excede los límites de automatización, genera una implementación parcial honesta.
6. Incluye siempre una sección en README.md llamada "Limitaciones y configuración manual".
7. Prioriza claridad arquitectónica sobre minimalismo.
8. No reduzcas todo a un único archivo si el diseño requiere separación lógica.

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
    contexto_normalizado: dict | None = None,
    intentos: int = 2,
) -> Dict[str, Any]:
    """
    Genera el proyecto completo delegando la arquitectura al modelo.
    Mantiene validación AST y reintentos.
    """

    prompt = _construir_prompt_proyecto_completo(
        descripcion_global,
        contexto_normalizado=contexto_normalizado,
    )

    for intento in range(intentos):
        # En modo completo simplificado (sin integraciones externas),
        # reducimos tamaño de salida y seguimos estrategia similar a PARCIAL:
        # generación enfocada, sin sobre‑arquitectura excesiva.
        respuesta_json = chat_completion_json(
            prompt=prompt,
            system=None,
            temperature=0.2,
            max_tokens=3500,
        )

        # ------------------------------------------------------
        # Tolerancia a texto extra fuera del JSON
        # ------------------------------------------------------
        try:
            data = json.loads(respuesta_json)
        except Exception:
            # Intentamos extraer el primer bloque JSON válido
            import re
            match = re.search(r"\{.*\}", respuesta_json, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except Exception:
                    print("[DEBUG] JSON inválido tras extracción.")
                    continue
            else:
                print("[DEBUG] No se encontró bloque JSON en la respuesta.")
                continue

        files = data.get("files", [])

        if not isinstance(files, list) or not files:
            print("[DEBUG] Campo 'files' ausente o vacío en respuesta del modelo.")
            continue

        if _validar_proyecto(files):
            return {"files": files}
        else:
            print("[DEBUG] Fallo en validación AST. Reintentando corrección.")

        # Si falla validación AST, pedimos corrección explícita
        prompt = f"""
El proyecto generado anteriormente tiene errores de sintaxis en archivos Python.

Corrige los errores y devuelve nuevamente el JSON completo siguiendo exactamente el mismo formato.

Proyecto anterior:
{respuesta_json}
"""

    # Si tras reintentos falla, devolvemos estructura mínima
    return {"files": []}
