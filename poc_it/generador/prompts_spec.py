from __future__ import annotations

import json
from typing import List


def build_prompt_spec(
    *,
    descripcion_global: str,
    contexto_normalizado: dict | None = None,
) -> str:
    """
    Prompt SPEC intencionalmente corto.
    - El SPEC define el QUÉ (estructura/contratos), no el CÓMO (implementación).
    - Las reglas de implementación se aplican por lotes durante la generación de archivos.

    Importante:
    - Función pura (sin side effects).
    - Mantener el texto igual (sin cambios semánticos) para no afectar comportamiento.
    """
    if contexto_normalizado:
        objetivo = contexto_normalizado.get("objetivo_tecnico", "")
        funcionalidades = contexto_normalizado.get("funcionalidades_clave", [])
        integraciones = contexto_normalizado.get("integraciones_externas", [])
        restricciones = contexto_normalizado.get("restricciones_tecnicas", [])

        descripcion_structurada = f"""
OBJETIVO:
{objetivo}

FUNCIONALIDADES:
{chr(10).join(f"- {f}" for f in funcionalidades) if funcionalidades else "- No especificadas"}

INTEGRACIONES:
{chr(10).join(f"- {i}" for i in integraciones) if integraciones else "- No especificadas"}

RESTRICCIONES:
{chr(10).join(f"- {r}" for r in restricciones) if restricciones else "- No especificadas"}
"""
    else:
        descripcion_structurada = f"DESCRIPCIÓN:\n{descripcion_global}\n"

    return f"""
TAREA
Genera un SPEC (plan) en JSON para una PoC FastAPI.

{descripcion_structurada}

INVARIANTES (ESTRUCTURALES)
- Paquete raíz: app/
- Entrypoint: app.main:app
- Comando local: uvicorn app.main:app --reload
- Imports internos: absolutos desde app.*
- La app DEBE ser importable sin configuración externa (no validar credenciales/config en import-time).
- Si el proyecto usa persistencia con SQLAlchemy (PostgreSQL/SQLite/etc.) y define modelos, debe incluir bootstrap simple del esquema (modo PoC):
  - Heurística: si existen modelos (Base + declarative models) y no hay migraciones, crea las tablas al arrancar (startup/lifespan) con `Base.metadata.create_all()`.
  - En async SQLAlchemy, usa el patrón: `async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)`.
  - Heurística de errores: no enmascares errores de infraestructura (DB caída, credenciales, timeouts) como `400 invalid_payload`.
    - Errores de DB/conectividad => 500/503 (y log con traceback).
    - 400/409 solo para errores de negocio/validación propia (FastAPI/Pydantic ya maneja 422).
  - Esto es para PoCs/local; en proyectos reales, lo normal es migraciones (Alembic), pero NO lo uses salvo que el usuario lo pida.

EL SPEC DEBE INCLUIR
- "files": lista EXACTA de rutas a generar (relativas)
- "endpoints": lista de endpoints con method/path/file/func
  - IMPORTANTE: `endpoints[].path` es el path FINAL que debe exponer FastAPI.
  - `endpoints[].request`: contrato de request (tipo + esquema)
    - type: "json" | "multipart" | "query" | "none"
    - schema: objeto JSON Schema-like (solo si type="json")
  - `endpoints[].response`: contrato de response (json_example mínimo)
  - `endpoints[].errors`: lista de códigos HTTP esperados (p.ej. [401,403,404])
- "env": variables de entorno esperadas (nombres exactos y para qué sirven) (si aplica)
- "dependencies": lista de dependencias PyPI mínimas (runtime) (si aplica)
- "dev_dependencies": lista de dependencias PyPI para desarrollo/tests (si aplica)
- "contracts": reglas de comportamiento por endpoint (códigos de error esperados y condición) (si aplica)
- "restrictions": lista de restricciones ejecutables (enforcement-ready) derivadas de restricciones del usuario
  - Formato por item:
    - id: string corto
    - applies_to: lista de globs (p.ej. ["*.py", "requirements.txt"]) (opcional; default "*")
    - must_not_contain: lista de substrings prohibidas (opcional)
    - must_contain_any: lista de substrings requeridas (opcional; al menos una debe aparecer)

SALIDA
Devuelve EXCLUSIVAMENTE JSON válido con la estructura:

{{
  "entrypoint": "app.main:app",
  "run_command": "uvicorn app.main:app --reload",
  "imports_policy": "absolute_from_app",
  "files": ["app/main.py", "..."],
  "dependencies": ["fastapi", "uvicorn", "..."],
  "dev_dependencies": ["pytest", "pytest-mock", "httpx"],
  "env": [{{"name":"VAR", "description":"..."}}],
  "endpoints": [
    {{
      "method":"GET",
      "path":"/x",
      "file":"app/endpoints/x.py",
      "func":"x",
      "request": {{"type":"none"}},
      "response": {{"json_example": {{"status":"ok"}}}},
      "errors": [401,403,404],
      "bundle_files":[
        "app/services/x_service.py",
        "app/utils/x_utils.py"
      ]
    }}
  ],
  "contracts": [{{"endpoint":"/x","rules":["..."]}}],
  "notes": "breve opcional"
}}

REGLAS
- Devuelve EXCLUSIVAMENTE JSON válido.
- Prohibido usar fences Markdown (``` o ```json).
- No incluyas texto fuera del JSON.
- No inventes archivos Python fuera de app/.
- Incluye requirements.txt y README.md en files.
- Si declaras endpoints, usa `bundle_files` para listar los módulos internos que el endpoint necesita (services/utils/etc.) y que deben generarse en el MISMO lote que el endpoint para evitar imports/símbolos faltantes.
- Persistencia (SOLO si el usuario lo pide explícitamente o el contexto lo requiere):
  - Si el usuario menciona base de datos/ORM (p.ej. PostgreSQL/SQLite/SQLAlchemy) o aparece `DATABASE_URL` en el contexto:
    - Entonces sí: declara `DATABASE_URL` en `env`, añade dependencias (SQLAlchemy/driver) y define bootstrap simple del esquema.
    - En el startup/lifespan de FastAPI, crear tablas automáticamente con `Base.metadata.create_all()` (modo PoC).
    - Documenta en README que en modo PoC se crean tablas automáticamente al arrancar.
  - Si el contexto NO menciona persistencia/DB:
    - Prohibido inventar `DATABASE_URL`, modelos (`app/models.py`) o dependencias de DB (sqlalchemy/aiosqlite/psycopg, etc.).
- NO inventes endpoints: los endpoints implementados deben ser exactamente los listados en `endpoints`.
- Si el usuario ha especificado explícitamente endpoints en la descripción (p.ej. “exponer endpoint ... /ruta ...”), el SPEC DEBE incluirlos en `endpoints` y en `files`.
  - Prohibido degradar silenciosamente a “stub” u omitir endpoints solicitados.
  - Si no puedes describir el endpoint con suficiente detalle, incluye igualmente el endpoint con `request.type` adecuado y añade en `notes` qué asunción has hecho.
- Consistencia de rutas (obligatorio, para evitar /x/x):
  - Estrategia única A (recomendada): `include_router(..., prefix=\"\")` y decorators con el path completo (p.ej. `@router.get(\"/health\")`).
  - Prohibido añadir prefixes no vacíos en `include_router` si el decorator ya incluye el path completo.
- Consistencia request:
  - Si `endpoints[].request.type == \"json\"`: el endpoint debe usar `Body`/Pydantic model y `application/json`. Prohibido `UploadFile`/`File`.
  - Si `endpoints[].request.type == \"multipart\"`: entonces sí usar `UploadFile`/`File`.
- /health determinista:
  - Si un endpoint declara response.json_example fijo y request.type == \"none\", no debe tener try/except genérico ni dependencias externas; devolver directamente el JSON de ejemplo.
""".strip()




def reparar_spec_prompt(
    *,
    prompt_spec_base: str,
    raw_resp: str,
    errores: List[str] | None = None,
) -> str:
    """
    Construye un prompt de reparación de SPEC, usando como entrada la respuesta cruda previa.

    Objetivo: evitar reintentos completos "desde cero" cuando el modelo devolvió JSON truncado
    o inválido. Pedimos reconstruir el mismo SPEC, completo y parseable.

    Importante:
    - Función pura (sin side effects).
    - Mantener el texto igual (sin cambios semánticos) para no afectar comportamiento.
    """
    errores = errores or []

    return f"""
La respuesta anterior pretendía ser un SPEC en JSON pero NO es parseable o está truncada.

Errores detectados:
- {chr(10).join(errores) if errores else "(no disponibles)"}

RESPUESTA CRUDA ANTERIOR (entrada a reparar):
{raw_resp}

TAREA
Devuelve de nuevo el SPEC COMPLETO como JSON válido.

IMPORTANTE (ANTI-TRUNCADO)
- La respuesta anterior puede estar TRUNCADA. Reconstruye el JSON COMPLETO.
- Asegura que se cierran TODOS los corchetes y llaves.
- Asegura que no quedan strings sin cerrar.
- Si el JSON es largo, prioriza completar la estructura y campos antes que añadir texto descriptivo.

REGLAS NO NEGOCIABLES
- Devuelve EXCLUSIVAMENTE JSON válido.
- Prohibido usar fences Markdown (``` o ```json).
- Prohibido incluir texto fuera del JSON.
- Mantén la MISMA estructura lógica requerida por el prompt original.
- Si faltan campos, inclúyelos aunque sea con valores vacíos razonables:
  - strings vacíos "", listas vacías [], objetos vacíos {{}}.
- No inventes nuevos endpoints o archivos no coherentes con la respuesta cruda.

PROMPT ORIGINAL (referencia; NO lo repitas en la salida):
{prompt_spec_base}
""".strip()


def reparar_spec_desde_spec(
    *,
    prompt_spec_base: str,
    spec_actual: dict | None,
    errores: List[str] | None = None,
    contexto_normalizado: dict | None = None,
) -> str:
    """
    Construye un prompt de reparación de SPEC aplicando cambios sobre el SPEC ACTUAL (patch-style).

    Fuente de verdad:
    - CONTEXTO_NORMALIZADO (incluye contratos_api) es el contrato del usuario.
    - El SPEC debe alinearse con ese contrato; no al revés.

    Objetivo:
    - Evitar reconstrucciones “desde cero” que reintroducen patrones por defecto (p.ej. multipart upload).
    - Forzar cambios mínimos y convergentes sobre el spec_actual.

    Importante:
    - Función pura (sin side effects).
    - Mantener el texto igual (sin cambios semánticos) para no afectar comportamiento.
    """
    errores = errores or []

    ctx_block = ""
    if isinstance(contexto_normalizado, dict) and contexto_normalizado:
        ctx_block = f"""

CONTEXTO_NORMALIZADO (FUENTE DE VERDAD; el SPEC DEBE cumplirlo):
{json.dumps(contexto_normalizado, ensure_ascii=False)}
"""

    spec_block = ""
    if isinstance(spec_actual, dict) and spec_actual:
        spec_block = f"""

SPEC_ACTUAL (a corregir; aplica cambios MINIMOS aquí):
{json.dumps(spec_actual, ensure_ascii=False)}
"""

    return f"""
TAREA
Corrige el SPEC_ACTUAL para que cumpla el CONTEXTO_NORMALIZADO (contratos) y los errores MUST indicados.
NO reconstruyas desde cero: modifica el SPEC_ACTUAL lo mínimo imprescindible.

Errores MUST detectados:
- {chr(10).join(errores) if errores else "(no disponibles)"}
{ctx_block}
{spec_block}

REGLAS NO NEGOCIABLES
- Devuelve EXCLUSIVAMENTE JSON válido (el SPEC completo).
- Prohibido Markdown, fences o texto fuera del JSON.
- No inventes endpoints/archivos no coherentes con el CONTEXTO_NORMALIZADO.
- Mantén invariantes estructurales:
  - entrypoint: app.main:app
  - run_command: uvicorn app.main:app --reload
  - imports_policy: absolute_from_app

REGLAS ESPECÍFICAS DE CONTRATOS (CRÍTICO)
- Debes comparar CONTEXTO_NORMALIZADO.contratos_api (FUENTE DE VERDAD) con SPEC.endpoints.
- Para cada contrato_api (method + path):
  - Debe existir un endpoint equivalente en SPEC.endpoints.
  - Sus campos contractuales deben ser consistentes (al menos request.type y response.json_example si existen).
- Si hay discrepancias entre contrato_api y SPEC.endpoints:
  - Debes tomar como referencia SIEMPRE el contrato_api.
  - Modifica el SPEC_ACTUAL con cambios mínimos para que SPEC.endpoints coincida con contratos_api.
- Si el SPEC tiene endpoints extra que NO están en contratos_api y el contexto parece enumerar explícitamente los endpoints esperados:
  - Elimina esos endpoints extra del SPEC (cambios mínimos).
- Ajusta SPEC.dependencies/env/contracts para que no contradigan los contratos.
- NO reconstruyas el SPEC desde cero.

PROMPT ORIGINAL (referencia de estructura; NO lo repitas en la salida):
{prompt_spec_base}
""".strip()
