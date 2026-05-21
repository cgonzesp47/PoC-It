from __future__ import annotations

import json
from typing import List


def build_prompt_lote(*, spec: dict, lote: List[str]) -> str:
    """
    Construye el prompt del lote (FASE 2).

    Importante:
    - Función pura (sin side effects).
    - Mantener el texto igual (sin cambios semánticos) para no afectar comportamiento.
    """
    contracts = spec.get("contracts", [])
    env = spec.get("env", [])
    dependencies = spec.get("dependencies", [])
    dev_dependencies = spec.get("dev_dependencies", [])
    restrictions = spec.get("restrictions", [])

    return f"""
TAREA
Genera el CONTENIDO de los siguientes archivos de un proyecto FastAPI.

INVARIANTES (COMPILABLE / IMPORTABLE)
- Paquete raíz: app/
- Entrypoint: app.main:app
- Imports internos: absolutos desde app.*
- No inventes nuevos archivos: solo los solicitados en este lote.
- La app DEBE ser importable sin configuración externa: `python -c "import app.main"` debe funcionar aunque falten variables de entorno/credenciales.
  - No validar credenciales ni configuración obligatoria en import-time.
  - No instanciar clientes/servicios externos en import-time si requieren parámetros (credenciales, IDs, URLs, etc.).
  - La creación de servicios debe ocurrir dentro de funciones/endpoints o mediante factorías lazy (dependency injection con FastAPI `Depends`).
  - La validación de configuración debe hacerse en runtime (p.ej. al ejecutar el endpoint que la necesita).
- Política de errores en endpoints (si este lote contiene endpoints):
  - Captura excepciones esperables (auth/permisos/config faltante/timeouts) y mapea a `HTTPException` con `detail` estructurado:
    - `error_code` (string corto), `message`, `hint` (si aplica)
  - LOGGING OBLIGATORIO (para ver el error específico SIEMPRE):
    - Define `logger = logging.getLogger(__name__)` en cada módulo de endpoint/servicio donde haya try/except.
    - En CADA `except Exception as e`: loguea SIEMPRE con stacktrace: `logger.exception("<contexto>")` antes de lanzar `HTTPException`.
    - No hacer `except Exception` silencioso sin logging.
  - Evita 500 genéricos por wiring (AttributeError, KeyError, TypeError): valida inputs/config en runtime y devuelve 400/401 según proceda.
- Pydantic v2: PROHIBIDO `from pydantic import BaseSettings`.
  - Si hay settings, usar `pydantic-settings` y patrón canónico get_settings() con lru_cache.
- Router pattern (OBLIGATORIO):
  - En CADA archivo `app/endpoints/*.py` debes definir EXACTAMENTE: `router = APIRouter()`
  - Los endpoints deben declararse como `@router.get(...)` / `@router.post(...)`.
  - `app/main.py` importará `router` desde cada endpoint y hará `app.include_router(router)`, por tanto el símbolo `router` debe existir SIEMPRE.
  - Prohibido `from app.main import app`
  - Prohibido `@app.get/post/...`
- RUTAS (anti /x/x) - OBLIGATORIO:
  - En `app/main.py` DEBES usar `app.include_router(<router>, prefix="")` (prefix vacío) para todos los routers.
  - En los archivos `app/endpoints/*.py`, los decorators DEBEN usar el path completo final.
  - Prohibido usar prefix no vacío en `include_router` (si lo haces, se duplican rutas).
- No incluyas texto fuera del JSON.
- No uses bloques ```.

DECISIONES / CONTRATOS DE ESTA PoC (fuente de verdad)
- ENV esperada:
{json.dumps(env, ensure_ascii=False)}
- Dependencias esperadas (runtime):
{json.dumps(dependencies, ensure_ascii=False)}
- Dependencias esperadas (dev/tests):
{json.dumps(dev_dependencies, ensure_ascii=False)}
- Contratos de comportamiento (por endpoint):
{json.dumps(contracts, ensure_ascii=False)}
- Restricciones ejecutables (NO NEGOCIABLES):
{json.dumps(restrictions, ensure_ascii=False)}

SPEC COMPLETO (referencia):
{json.dumps(spec, ensure_ascii=False)}

ARCHIVOS A GENERAR EN ESTE LOTE (exactos):
{json.dumps(lote, ensure_ascii=False)}

SALIDA (JSON):
{{
  "files": [
    {{"path": "ruta", "content": "contenido"}}
  ]
}}

Reglas:
- Devuelve SOLO archivos cuyo path esté en la lista del lote.
- Incluye el contenido completo del archivo.
- Si el archivo es requirements.txt: debe reflejar dependencies (mínimas) y nada inventado.
- Si el archivo es .py debe ser sintácticamente válido.
""".strip()


def build_prompt_lote_missing(*, spec: dict, missing: List[str]) -> str:
    """
    Prompt de último intento cuando faltan archivos (missing) en un lote.

    Importante:
    - Función pura.
    - Mantener el texto igual para no alterar el comportamiento.
    """
    return f"""
Faltan archivos del lote y NO pueden omitirse.

Devuelve EXCLUSIVAMENTE estos archivos (y ninguno más), con contenido COMPLETO (no vacío):
{json.dumps(missing, ensure_ascii=False)}

SPEC (referencia):
{json.dumps(spec, ensure_ascii=False)}

SALIDA (JSON):
{{
  "files": [
    {{"path": "ruta", "content": "contenido"}}
  ]
}}

REGLAS
- No incluyas texto fuera del JSON.
- Los paths deben ser exactamente los indicados.
- No devuelvas content vacío.
""".strip()


def build_prompt_lote_fix_errors(
    *,
    spec: dict,
    lote: List[str],
    errores_lote: List[str],
    ultimo_raw: str,
) -> str:
    """
    Prompt de reparación por lote cuando hay errores (paths/AST/contenido vacío).

    Importante:
    - Función pura.
    - Mantener el texto igual para no alterar el comportamiento.
    """
    return f"""
Hay errores en los archivos del lote. Corrige SOLO los archivos de este lote.

Errores:
- {chr(10).join(errores_lote)}

REGLA CRÍTICA (from-import):
- Si un archivo hace `from app.x.y import SIMBOLO`, entonces SIMBOLO DEBE existir realmente en `app/x/y.py`.
- Si el símbolo no existe, tienes dos opciones válidas:
  1) Crear/añadir ese símbolo en el módulo importado, o
  2) Cambiar el import/código para NO requerir ese símbolo.
- No inventes nombres como verify_token / auth_utils si el SPEC no define autenticación.

SPEC:
{json.dumps(spec, ensure_ascii=False)}

Archivos del lote:
{json.dumps(lote, ensure_ascii=False)}

Respuesta anterior:
{ultimo_raw}

Devuelve nuevamente el JSON con los archivos corregidos (solo los del lote).
""".strip()
