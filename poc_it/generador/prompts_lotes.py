from __future__ import annotations

import json
from typing import List, Optional


def build_prompt_lote(*, spec: dict, lote: List[str], file_contracts: Optional[List[dict]] = None) -> str:
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

    # New contract-first input: explicit file-level contracts for this lote.
    # If not provided, legacy behavior remains (but the contract-first flow should always pass it).
    file_contracts = file_contracts or []
    if not isinstance(file_contracts, list):
        file_contracts = []

    return f"""
TAREA
Genera el CONTENIDO de los siguientes archivos de un proyecto FastAPI.

PATRONES CANÓNICOS (COPIAR LITERALMENTE, NO IMPROVISAR)
- FastAPI Depends (CORRECTO):
  - BIEN: `db: AsyncSession = Depends(get_db)`
  - MAL:  `db: Depends(get_db)`
  - BIEN: `svc: Service = Depends(get_service)`
- Settings lazy (PROHIBIDO evaluar env obligatoria en import-time):
  - BIEN: `def get_settings(): return Settings()` (cacheada) y crear engine/session dentro de `get_engine()`/`get_db()`
  - MAL: `settings = Settings()` o `engine = create_engine(Settings().URL)` en import-time
- Si hay capa Service, debe ser inyectable (o no uses overrides en tests):
  - BIEN: `def get_product_service(...): return ProductService(...)` y `svc: ProductService = Depends(get_product_service)`

INVARIANTES (COMPILABLE / IMPORTABLE)
- Paquete raíz: app/
- Entrypoint: app.main:app
- Imports internos: absolutos desde app.*
- No inventes nuevos archivos: solo los solicitados en este lote.

REGLA CRÍTICA SOBRE MODELOS (evitar 'app.models' como paquete inexistente)
- Si existe un archivo `app/models.py` (módulo), entonces:
  - Está PROHIBIDO importar desde submódulos tipo `app.models.product` o `app.models.user`.
  - Los imports correctos son: `from app.models import Product` (si Product está en models.py) o `from app import models` y usar `models.Product`.
- Solo puedes usar `from app.models.<x> import <Y>` si y SOLO si en el lote existe realmente el directorio `app/models/` y el archivo `app/models/<x>.py`.
- Si no estás seguro, usa el import más seguro: `from app.models import <Modelo>`.
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
- Pydantic v2 OBLIGATORIO (PRAGMÁTICO: reduce alucinaciones por versiones):
  - PROHIBIDO usar patrones de Pydantic v1:
    - `orm_mode = True`
    - `BaseModel.from_orm(...)`
    - `from pydantic import BaseSettings` (en v2 se usa `pydantic-settings`)
  - Para modelos de respuesta basados en ORM/atributos:
    - En schemas: `from pydantic import BaseModel, ConfigDict` y luego `model_config = ConfigDict(from_attributes=True)`
    - Para convertir ORM->schema: `Schema.model_validate(obj)` (no `from_orm`)
- Para settings:
    - usar `from pydantic_settings import BaseSettings`
    - exponer `get_settings()` cacheada con `@lru_cache` (OJO: solo en funciones SYNC).

REGLA CRÍTICA ASYNC + CACHE (EVITAR `cannot reuse already awaited coroutine`)
- Está PROHIBIDO decorar con `@lru_cache` una función `async def`.
  - MAL:
    - `@lru_cache`
      `async def get_engine(): ...`
    - Esto cachea el coroutine y en la 2ª llamada rompe: `RuntimeError: cannot reuse already awaited coroutine`.
  - BIEN (patrón recomendado):
    - `@lru_cache`
      `def get_engine() -> AsyncEngine:`
        - `settings = get_settings()`
        - `return create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)`
    - `async def get_db():`
        - `engine = get_engine()`
        - `async with async_sessionmaker(engine, ...)() as session: yield session`
  - Alternativa válida:
    - No cachear engine (pero entonces no recrearlo por request; usar un singleton de módulo).

REGLA CRÍTICA: no captures HTTPException en 500
- Si dentro de un endpoint haces:
  - `raise HTTPException(status_code=404, ...)`
  - tu `except Exception` NO debe capturarlo y convertirlo a 500.
- Patrón correcto:
  - `except HTTPException: raise`
  - `except Exception as e: logger.exception(...); raise HTTPException(500, ...)`
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

FILE CONTRACTS (fuente de verdad por archivo; CUMPLIR ESTRICTAMENTE):
{json.dumps(file_contracts, ensure_ascii=False)}

Reglas contract-first:
- Cumple estos file contracts exactamente.
- No generes símbolos públicos fuera de contrato salvo helpers privados necesarios.
- No inventes archivos fuera del lote.
- No muevas endpoints entre archivos.

SPEC (resumen/soporte, no reemplaza contracts):
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

REGLA CRÍTICA DEPENDENCIAS (evitar fallos tipo aiosqlite)
- Si en CUALQUIER archivo Python del lote usas un import de un paquete externo (no stdlib, no fastapi/starlette/pydantic/sqlalchemy),
  entonces ESE paquete debe aparecer en `dependencies` (si runtime) o `dev_dependencies` (si solo tests).
- Si usas `sqlite+aiosqlite` o importas `aiosqlite`, DEBES incluir `aiosqlite` en dev_dependencies o dependencies.
- Si NO puedes garantizar una dependencia (porque es integración externa no disponible), NO la uses: degrada a stub/no-op y documenta pasos manuales.

REGLA CRÍTICA MODO PARCIAL (tests herméticos)
- Si el proyecto depende de integraciones externas (DB, Drive, APIs), los tests generados DEBEN ser herméticos:
  - Opción A (preferida): tests que NO toquen esas integraciones (mock de dependencias FastAPI vía dependency_overrides).
  - Opción B: marcar como skipeables (pytest.skip) si falta la configuración/dependencia.
- En modo PARCIAL, está PROHIBIDO que un test falle por falta de paquetes opcionales (p.ej. aiosqlite) o por intentar abrir conexiones reales.
  El test debe:
    - o bien mockear la dependencia,
    - o bien skippear con un mensaje claro.

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
