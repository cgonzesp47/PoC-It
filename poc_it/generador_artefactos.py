"""
PoC-it – Generador de Proyecto (LLM) con SPEC + Generación por Lotes

Estrategia v2 (más robusta y con menos tokens por llamada):

Fase 1) El modelo genera un SPEC (JSON pequeño) con:
  - entrypoint (fijo recomendado: app.main:app)
  - comando de ejecución
  - lista EXACTA de archivos a generar
  - definición de endpoints/servicios/configuración (alto nivel)

Fase 2) El modelo genera el contenido por LOTES de archivos guiado por el SPEC.

Fase 3) Validación local:
  - AST (sintaxis)
  - coherencia de paths (no inventar archivos)
  - política de imports (absolutos desde app.)
  - resolución básica de imports internos

Fase 4) Repair loop dirigido:
  - si falla validación, pedir corrección SOLO de los archivos implicados.

Objetivo:
- Mantener libertad funcional (la PoC puede ser “cualquier cosa”)
- Imponer invariantes mínimos para que SIEMPRE arranque:
  - paquete raíz app/
  - entrypoint app.main:app
  - imports internos desde app.
  - __init__.py en carpetas con código
"""

from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple, Set, Optional, Iterable

from poc_it.generador.guardrails import (
    guardrails_por_spec,
    seleccionar_error_bloqueante,
)
from poc_it.generador.json_utils import extraer_json_tolerante
from poc_it.generador.repair_loop import (
    aplicar_patch_en_memoria as _aplicar_patch_en_memoria,
    aplicar_repair_loop_imports,
)
from poc_it.generador.restrictions import compilar_restricciones
from poc_it.generador.spec_alignment import (
    alinear_spec_con_contexto as _alinear_spec_con_contexto,
)
from poc_it.generador.spec_validation import (
    completar_inits_en_files as _completar_inits_en_files,
    normalizar_paths as _normalizar_paths,
    persistir_spec_debug as _persistir_spec_debug,
    validar_spec as _validar_spec,
)
from poc_it.generador.utils_imports import (
    extraer_from_imports,
    extraer_imports,
)
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

    Además de AST:
    - Detecta patrones conocidos que rompen en runtime (p.ej. Pydantic v2 BaseSettings).
    """
    for f in files:
        path = f.get("path", "")
        content = f.get("content", "") or ""

        if path.endswith(".py"):
            if not _codigo_python_valido(content):
                return False

            # Guardrail: Pydantic v2 rompe `from pydantic import BaseSettings`
            if "from pydantic import BaseSettings" in content:
                return False

    return True




def _reparar_spec_prompt(
    prompt_spec_base: str,
    raw_resp: str,
    errores: List[str] | None = None,
) -> str:
    """
    Construye un prompt de reparación de SPEC, usando como entrada la respuesta cruda previa.

    Objetivo: evitar reintentos completos "desde cero" cuando el modelo devolvió JSON truncado
    o inválido. Pedimos reconstruir el mismo SPEC, completo y parseable.
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


def _reparar_spec_desde_spec(
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




def _agrupar_lotes(files: List[str], spec: dict | None = None) -> List[List[str]]:
    """
    Agrupa archivos en lotes para reducir tokens, minimizando incoherencias entre lotes.

    Estrategia (mantenible / sin hardcodear proveedores):
    - 1) Lote core: main + config + __init__ (lo mínimo para que el proyecto "compile")
    - 2) Lotes por "bundle" de endpoint: cada endpoint se genera junto con sus módulos
         declarados en el SPEC (services/utils/etc.) para evitar imports de símbolos inexistentes
         entre lotes.
    - 3) Resto de archivos (requirements/readmes/otros)

    Requisito:
    - El SPEC puede incluir opcionalmente:
      - endpoints[*].bundle_files: lista de paths (además del propio endpoint file) requeridos por ese endpoint.
    Si no existe, se usa el fallback simple por carpetas (comportamiento anterior).
    """
    norm_files = [p.replace("\\", "/") for p in files]

    # ----------------------------
    # 0) fallback si no hay spec
    # ----------------------------
    if not isinstance(spec, dict) or not isinstance(spec.get("endpoints"), list):
        core: List[str] = []
        endpoints: List[str] = []
        services: List[str] = []
        resto: List[str] = []

        for p in norm_files:
            if p in ("app/main.py", "app/__init__.py") or p.startswith("app/config/"):
                core.append(p)
            elif p.startswith("app/endpoints/"):
                endpoints.append(p)
            elif p.startswith("app/services/"):
                services.append(p)
            else:
                resto.append(p)

        lotes: List[List[str]] = []
        if core:
            lotes.append(core)
        if endpoints:
            lotes.append(endpoints)
        if services:
            lotes.append(services)
        if resto:
            lotes.append(resto)
        return lotes

    allowed = set(norm_files)

    # ----------------------------
    # 1) core
    # ----------------------------
    core = [p for p in norm_files if p in ("app/main.py", "app/__init__.py") or p.startswith("app/config/")]

    # ----------------------------
    # 2) bundles por endpoint
    # ----------------------------
    bundles: List[List[str]] = []
    consumed: Set[str] = set(core)

    for ep in spec.get("endpoints", []):
        if not isinstance(ep, dict):
            continue
        ep_file = str(ep.get("file") or "").replace("\\", "/")
        if not ep_file or ep_file not in allowed:
            continue

        bundle = [ep_file]
        extra = ep.get("bundle_files", [])
        if isinstance(extra, list):
            for x in extra:
                xp = str(x).replace("\\", "/")
                if xp in allowed:
                    bundle.append(xp)

        # normaliza y evita duplicados
        bundle = [p for p in dict.fromkeys(bundle).keys()]
        bundles.append(bundle)
        consumed.update(bundle)

    # Si el SPEC no trae bundles útiles, volvemos al comportamiento clásico
    if not bundles:
        return _agrupar_lotes(norm_files, spec=None)

    # ----------------------------
    # 3) resto
    # ----------------------------
    resto = [p for p in norm_files if p not in consumed]

    lotes: List[List[str]] = []
    if core:
        lotes.append(core)
    lotes.extend(bundles)
    if resto:
        lotes.append(resto)

    return lotes


def _validar_paths_generados(files_generados: List[Dict[str, str]], allowed_paths: Set[str]) -> Tuple[bool, List[str]]:
    errores: List[str] = []
    for f in files_generados:
        p = (f.get("path") or "").replace("\\", "/")
        if not p:
            errores.append("Archivo sin 'path'")
            continue
        if p not in allowed_paths:
            errores.append(f"El modelo devolvió un path no permitido: {p}")
    return (len(errores) == 0), errores


def _validar_imports_internos(
    files_generados: List[Dict[str, str]],
    allowed_paths: Set[str],
) -> Tuple[bool, List[str]]:
    """
    Política:
    - Imports internos deben ser absolutos desde app.* (si importan módulos del proyecto)
    - Si aparece 'from services' / 'import services' u otros módulos de primer nivel no permitidos, se marca error.
    - Resolución básica: si importan app.x.y, se verifica existencia del archivo correspondiente en allowed_paths.

    Extra (crítico):
    - Verifica coherencia de símbolos para `from app.x.y import Z`:
      si el target module existe dentro del proyecto, Z debe existir en ese archivo (AST),
      evitando errores tipo ImportError "cannot import name ...".
    """
    errores: List[str] = []

    # Mapa módulo->path disponible (solo para app.*)
    mod_to_path: Dict[str, str] = {}
    for p in allowed_paths:
        if p.startswith("app/") and p.endswith(".py"):
            mod = p[:-3].replace("/", ".")  # app/services/a.py -> app.services.a
            mod_to_path[mod] = p

    # Índice path->content para chequear símbolos
    src_by_path: Dict[str, str] = {}
    for f in files_generados:
        fp = (f.get("path") or "").replace("\\", "/")
        if fp.endswith(".py"):
            src_by_path[fp] = f.get("content") or ""

    for f in files_generados:
        p = (f.get("path") or "").replace("\\", "/")
        if not p.endswith(".py"):
            continue

        src = f.get("content") or ""
        imports = extraer_imports(src)

        # 1) Validación de módulos importados
        for imp in imports:
            if imp.startswith("app."):
                # Validación resoluble
                if imp in mod_to_path:
                    continue
                # Permitir imports a paquetes (app.services) si existe __init__.py
                pkg_path = imp.replace(".", "/") + "/__init__.py"
                if pkg_path in allowed_paths:
                    continue
                # Si el import apunta a un módulo esperado del proyecto (allowed_paths),
                # pero ese fichero aún no está presente en `files_generados`, lo tratamos
                # como dependencia ausente para que el repair loop lo pida.
                expected_path = imp.replace(".", "/") + ".py"
                if expected_path in allowed_paths:
                    errores.append(f"{p}: dependencia interna ausente (no generada aún): {expected_path}")
                else:
                    errores.append(f"{p}: import interno no resoluble: {imp}")
            else:
                # Señales típicas de fallo estructural
                if imp.split(".")[0] in ("services", "endpoints", "config"):
                    errores.append(f"{p}: import inválido (debe ser desde app.*): {imp}")

        # 2) Validación de símbolos importados: from app.* import X
        for mod, sym in extraer_from_imports(src):
            if not mod.startswith("app."):
                continue

            target_path = mod_to_path.get(mod)
            if not target_path:
                # si es paquete, no validamos símbolo (podría ser export en __init__.py)
                continue

            target_src = src_by_path.get(target_path)
            if target_src is None:
                # El archivo existe en el plan (allowed_paths) pero no está presente en el estado actual.
                # Esto debe disparar repair loop del lote correcto, no un error de símbolo.
                errores.append(
                    f"{p}: dependencia interna ausente (no generada aún): {target_path}"
                )
                continue

            try:
                tree = ast.parse(target_src)
            except Exception:
                # AST ya se valida por otra vía; aquí no añadimos ruido
                continue

            defined: Set[str] = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined.add(node.name)
                elif isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            defined.add(t.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    defined.add(node.target.id)

            if sym not in defined:
                # Debug útil: en fallos típicos (p.ej. `router`) necesitamos ver qué
                # se consideró "defined" realmente, y una muestra del fichero target.
                if sym == "router":
                    snippet = "\n".join((target_src or "").splitlines()[:80])
                    print(
                        f"[DEBUG] Falta símbolo 'router' en {mod} ({target_path}). "
                        f"Definidos={sorted(list(defined))[:30]} "
                        f"target_len={len(target_src)} src_by_path_keys={len(src_by_path)} "
                        f"Snippet(80l):\n{snippet}\n---"
                    )
                errores.append(
                    f"{p}: from-import inválido: '{sym}' no existe en {mod} ({target_path})"
                )

    return (len(errores) == 0), errores


# ==========================================================
# SPEC NORMALIZATION / SANITIZATION
# ==========================================================



def _build_repair_prompt_por_restriccion(
    *,
    spec: dict,
    full_errors: List[str],
    target_error_path: str,
    target_error_msg: str,
    repair_paths: List[str],
    files_generados: List[Dict[str, str]],
) -> str:
    """
    Prompt de repair "atómico": atacar 1 error/restricción a la vez.
    """
    by_path = {(f.get("path") or "").replace("\\", "/"): (f.get("content") or "") for f in files_generados if f.get("path")}
    target_src = by_path.get(target_error_path, "")

    # limitar tamaño del source para no quemar tokens (pero mantener suficiente contexto)
    target_lines = target_src.splitlines()
    if len(target_lines) > 260:
        target_src = "\n".join(target_lines[:260]) + "\n# ... (truncado)"

    # Acotar a archivos implicados: el target + cualquier otro que guardrails haya marcado
    # (pero manteniendo el repair atómico en el target como objetivo principal)
    scoped_paths = [p for p in dict.fromkeys([target_error_path] + (repair_paths or [])).keys() if p]

    # Extraer la restriction relevante del SPEC (best-effort, por id textual en el error)
    restrictions = spec.get("restrictions", [])
    rid = None
    m = re.search(r"viola restriction '([^']+)'", target_error_msg or "")
    if m:
        rid = m.group(1).strip()

    restriction_obj = None
    if rid and isinstance(restrictions, list):
        for r in restrictions:
            if isinstance(r, dict) and str(r.get("id") or "").strip() == rid:
                restriction_obj = r
                break

    restriction_block = json.dumps(restriction_obj, ensure_ascii=False) if restriction_obj else "(no disponible)"

    return f"""
TAREA
Corrige UN ÚNICO incumplimiento bloqueante de guardrails del proyecto, con cambios mínimos y verificables.

ERROR OBJETIVO (PRIORITARIO)
- Archivo: {target_error_path}
- Error: {target_error_msg}

RESTRICCIÓN (si está disponible en SPEC.restrictions)
{restriction_block}

CONTEXTO
- No re-arquitectures el proyecto.
- No añadas endpoints ni cambies rutas/métodos del SPEC.
- No añadas dependencias nuevas salvo que el propio SPEC lo exija.
- Enfócate en eliminar el patrón prohibido o cumplir el patrón requerido de ESTA restricción.
- Si la restricción es must_not_contain: el/los patrones NO deben aparecer en el archivo tras el cambio.

CÓDIGO ACTUAL (fragmento) de {target_error_path}:
```python
{target_src}
```

ARCHIVOS QUE PUEDES MODIFICAR (paths exactos; devuelve SOLO de esta lista):
{json.dumps(scoped_paths, ensure_ascii=False)}

TODOS LOS ERRORES DE GUARDRAILS (para contexto; NO intentes arreglarlos todos a la vez):
- {chr(10).join(full_errors)}

SALIDA (EXCLUSIVAMENTE JSON válido):
{{ "files": [{{"path":"...", "content":"..."}}] }}

REGLAS
- Devuelve SOLO archivos dentro de la lista permitida.
- El contenido debe ser completo (no parcial).
- No incluyas texto fuera del JSON.
""".strip()





# ==========================================================
# PROMPT LIBRE DE GENERACIÓN COMPLETA
# ==========================================================


def _construir_prompt_spec(
    descripcion_global: str,
    contexto_normalizado: dict | None = None,
) -> str:
    """
    Prompt SPEC intencionalmente corto.
    - El SPEC define el QUÉ (estructura/contratos), no el CÓMO (implementación).
    - Las reglas de implementación se aplican por lotes durante la generación de archivos.
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
  - En el startup/lifespan de FastAPI, crear tablas automáticamente con `Base.metadata.create_all()`.
  - En async SQLAlchemy: `async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)`.
  - NO uses Alembic a menos que el usuario lo pida explícitamente.

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
- Si en `env` declaras `DATABASE_URL` y hay endpoints de escritura (POST/PUT/PATCH/DELETE):
  - Debes incluir modelos SQLAlchemy (tablas) y un startup/lifespan que haga `create_all` para que el primer POST no falle.
  - Documenta en README que en modo PoC se crean tablas automáticamente al arrancar.
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
"""


# ==========================================================
# GENERACIÓN PRINCIPAL CON REINTENTOS
# ==========================================================


def _merge_files_generados(
    base: List[Dict[str, str]],
    patch: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """
    Merge estable:
    - Mantiene `base` como fuente principal.
    - Sobrescribe solo paths presentes en `patch` con contenido no vacío.
    - Añade paths nuevos del patch si no existían.
    """
    idx = {
        (f.get("path") or "").replace("\\", "/"): i
        for i, f in enumerate(base)
        if isinstance(f, dict)
    }
    for f in patch or []:
        if not isinstance(f, dict):
            continue
        p = (f.get("path") or "").replace("\\", "/")
        c = (f.get("content") or "")
        if not p or not c.strip():
            continue
        if p in idx:
            base[idx[p]]["content"] = c
        else:
            base.append({"path": p, "content": c})
    return base


def _validar_y_reparar_final(
    *,
    spec: dict,
    files_generados: List[Dict[str, str]],
    allowed_paths: Set[str],
    intentos: int,
) -> bool:
    """
    Aplica la fase final de validación + repairs sobre `files_generados` IN-MEMORY.

    Mantiene EXACTAMENTE el comportamiento previo (antes duplicado en 2 flows):
    - AST global
    - imports internos + repair loop de imports
    - guardrails + repair atómico por restricción

    Devuelve:
    - True si el proyecto queda en estado válido
    - False si no converge o hay fallos no reparables
    """
    # 1) AST global
    if not _validar_proyecto(files_generados):
        return False

    # 2) Imports globales vs allowed_paths
    ok_imports, e_imports = _validar_imports_internos(files_generados, allowed_paths)
    if not ok_imports:
        patched_ok, _repair_paths = aplicar_repair_loop_imports(
            spec=spec,
            files_generados=files_generados,
            allowed_paths=allowed_paths,
            errores_imports=e_imports,
            chat_completion_json=chat_completion_json,
            intentos=intentos,
        )
        if not patched_ok:
            return False

        ok_imports2, _e_imports2 = _validar_imports_internos(files_generados, allowed_paths)
        if not ok_imports2:
            return False

    # 3) Guardrails por SPEC (contrato usuario): si fallan, intentamos repair dirigido
    guard = guardrails_por_spec(spec, files_generados)
    if guard.warnings:
        print("[DEBUG] Guardrails warnings:", guard.warnings)

    if not guard.ok:
        if not guard.repair_paths:
            return False

        max_guardrail_repairs = max(2, intentos)
        for _ in range(max_guardrail_repairs):
            sel = seleccionar_error_bloqueante(guard.errors)
            if not sel:
                break
            target_path, target_msg = sel

            prompt_fix = _build_repair_prompt_por_restriccion(
                spec=spec,
                full_errors=guard.errors,
                target_error_path=target_path,
                target_error_msg=target_msg,
                repair_paths=guard.repair_paths,
                files_generados=files_generados,
            )

            raw = chat_completion_json(
                prompt=prompt_fix,
                system=None,
                temperature=0.1,
                max_tokens=1600,
                fase="generacion_codigo",
            )
            data = extraer_json_tolerante(raw) or {}
            cand = data.get("files")
            if isinstance(cand, list) and cand:
                _aplicar_patch_en_memoria(files_generados, cand)

                guard = guardrails_por_spec(spec, files_generados)
                if guard.ok:
                    break
                continue
            break

        if not guard.ok:
            return False

    return True


def generar_proyecto_desde_spec(
    *,
    spec: dict,
    descripcion_global: str,
    contexto_normalizado: dict | None = None,
    intentos: int = 2,
    files_iniciales: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Genera/regenera código reutilizando un SPEC ya existente (fuente de verdad).

    Caso de uso principal:
    - Repair loops posteriores (runtime/tests) donde NO queremos "rebobinar" a Fase 1
      (regenerar/alinear SPEC), sino re-generar archivos guiados por el SPEC ya validado.

    Contrato:
    - `spec` debe ser dict. Si está vacío o inválido, se delega en `generar_proyecto_completo`
      para mantener compatibilidad (fallback conservador).
    """
    if not isinstance(spec, dict) or not spec:
        return generar_proyecto_completo(
            descripcion_global=descripcion_global,
            contexto_normalizado=contexto_normalizado,
            intentos=intentos,
        )

    files_plan = _completar_inits_en_files(spec.get("files", []))
    spec["files"] = files_plan
    allowed_paths = set(files_plan)

    # mantener la misma compilación/sanitización de restricciones
    spec["restrictions"] = compilar_restricciones(spec, contexto_normalizado, intentos=intentos)

    # (idéntico a Fase 2/3 de generar_proyecto_completo, pero sin Fase 1/1.5)
    # Si nos pasan `files_iniciales`, actuamos en modo REPAIR incremental:
    # - no re-generamos todo por lotes
    # - reusamos el estado y solo intentamos reparar imports/guardrails al final
    if isinstance(files_iniciales, list) and files_iniciales:
        files_generados = [
            {"path": (f.get("path") or "").replace("\\", "/"), "content": (f.get("content") or "")}
            for f in files_iniciales
            if isinstance(f, dict) and f.get("path")
        ]

        if not _validar_y_reparar_final(
            spec=spec,
            files_generados=files_generados,
            allowed_paths=allowed_paths,
            intentos=intentos,
        ):
            return {"files": []}

        return {"files": files_generados, "spec": spec}
    lotes = _agrupar_lotes(files_plan, spec=spec)
    lotes = sorted(
        lotes,
        key=lambda lote: 1
        if any(p in ("app/main.py", "app/__init__.py") or p.startswith("app/config/") for p in lote)
        else 0,
    )

    files_generados: List[Dict[str, str]] = []

    for lote in lotes:
        lote_set = set(lote)

        contracts = spec.get("contracts", [])
        env = spec.get("env", [])
        dependencies = spec.get("dependencies", [])
        dev_dependencies = spec.get("dev_dependencies", [])
        restrictions = spec.get("restrictions", [])

        prompt_lote = f"""
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
    - En CADA `except Exception as e`: loguea SIEMPRE con stacktrace: `logger.exception(\"<contexto>\")` antes de lanzar `HTTPException`.
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
  - En `app/main.py` DEBES usar `app.include_router(<router>, prefix=\"\")` (prefix vacío) para todos los routers.
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
"""

        lote_files: Optional[List[Dict[str, str]]] = None
        ultimo_raw: str = ""
        errores_lote: List[str] = []

        for intento_lote in range(max(1, intentos)):
            raw = chat_completion_json(
                prompt=prompt_lote,
                system=None,
                temperature=0.2,
                max_tokens=2500,
                fase="generacion_codigo",
            )
            ultimo_raw = raw
            data = extraer_json_tolerante(raw)
            if not data:
                continue
            cand = data.get("files")
            if not isinstance(cand, list) or not cand:
                continue

            cand_norm = []
            for f in cand:
                if not isinstance(f, dict):
                    continue
                p = (f.get("path") or "").replace("\\", "/")
                if p in lote_set:
                    cand_norm.append({"path": p, "content": f.get("content", "")})

            missing = sorted(list(lote_set - {ff.get("path") for ff in cand_norm if ff.get("path")}))
            empty = sorted([ff.get("path") for ff in cand_norm if not (ff.get("content") or "").strip()])
            if missing or empty:
                print(f"[DEBUG] Lote generado incompleto. Missing={missing} Empty={empty}")

            empty = [p for p in empty if not str(p).endswith("/__init__.py")]
            ok_nonempty = not missing and not empty
            ok_paths, e_paths = _validar_paths_generados(cand_norm, lote_set)
            ok_ast = _validar_proyecto(cand_norm)

            if ok_nonempty and ok_paths and ok_ast:
                lote_files = cand_norm
                break

            errores_lote = []
            if not ok_nonempty:
                for pth in missing:
                    errores_lote.append(f"Archivo no devuelto por el modelo: {pth}")
                for pth in empty:
                    errores_lote.append(f"Archivo sin contenido (content vacío): {pth}")
            if not ok_paths:
                errores_lote.extend(e_paths)
            if not ok_ast:
                errores_lote.append("Fallo de sintaxis (AST) en algún archivo del lote")

            if intento_lote == max(1, intentos) - 1 and missing:
                prompt_lote = f"""
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
"""
            else:
                prompt_lote = f"""
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
"""

        if not lote_files:
            print("[DEBUG] No se pudo generar un lote válido.")
            if errores_lote:
                print("[DEBUG] Errores lote:", errores_lote)
            return {"files": []}

        existentes = {(f["path"].replace("\\", "/")): f for f in files_generados}

        for f in lote_files:
            pth = f["path"]
            new_content = f.get("content") or ""
            prev = existentes.get(pth)
            prev_content = (prev.get("content") or "") if isinstance(prev, dict) else ""

            if prev and prev_content.strip() and not new_content.strip():
                print(
                    f"[DEBUG] Merge: ignorando sobrescritura VACÍA de '{pth}' "
                    f"(prev_len={len(prev_content)}, new_len={len(new_content)})"
                )
                continue

            existentes[pth] = f

        files_generados = list(existentes.values())

    # -------------------------
    # FASE 3: VALIDACIÓN FINAL
    # -------------------------
    if not _validar_proyecto(files_generados):
        print("[DEBUG] Fallo AST en validación final.")
        return {"files": []}

    ok_imports, e_imports = _validar_imports_internos(files_generados, allowed_paths)
    if not ok_imports:
        print("[DEBUG] Fallo imports en validación final:", e_imports)
        return {"files": []}

    ok_guard, e_guard, repair_paths, guard_warnings = guardrails_por_spec(spec, files_generados)
    if guard_warnings:
        print("[DEBUG] Guardrails warnings:", guard_warnings)

    if not ok_guard:
        print("[DEBUG] Fallo guardrails SPEC:", e_guard)
        return {"files": []}

    return {"files": files_generados, "spec": spec}


def generar_proyecto_completo(
    descripcion_global: str,
    contexto_normalizado: dict | None = None,
    intentos: int = 2,
) -> Dict[str, Any]:
    """
    Genera el proyecto completo usando SPEC + generación por lotes.

    Mantiene:
    - validación AST
    Añade:
    - validación SPEC (paths mínimos, __init__.py, entrypoint)
    - no permitir paths inventados
    - política de imports internos desde app.*
    - validación básica de resolución de imports internos
    - repair loop dirigido por lote si falla
    """

    # -------------------------
    # FASE 1: SPEC
    # -------------------------
    prompt_spec = _construir_prompt_spec(
        descripcion_global,
        contexto_normalizado=contexto_normalizado,
    )

    spec: Optional[dict] = None
    errores_spec: List[str] = []

    # Política:
    # - Intento 1: generar SPEC desde prompt base
    # - Si no parsea o no valida: NO reintentamos "desde cero"; pedimos REPARAR
    prompt_spec_base = prompt_spec

    for intento_spec in range(max(1, intentos)):
        resp = chat_completion_json(
            prompt=prompt_spec,
            system=None,
            temperature=0.1,
            max_tokens=1800,
            provider_hint="docs",
            fase="documentacion",
        )
        spec = extraer_json_tolerante(resp)

        if not spec:
            errores_spec = ["SPEC no parseable (JSON inválido/truncado o texto extra no extraíble)"]

            # Guardar RAW del SPEC para diagnóstico (timeouts / truncados / rate-limit)
            try:
                debug_dir = Path("output/_debug")
                debug_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                (debug_dir / f"spec_raw_{ts}.txt").write_text(resp or "", encoding="utf-8")
            except Exception:
                pass

            # En vez de regenerar todo, pedimos reparar la respuesta cruda.
            prompt_spec = _reparar_spec_prompt(
                prompt_spec_base=prompt_spec_base,
                raw_resp=resp,
                errores=errores_spec,
            )
            continue

        ok, errores = _validar_spec(spec)
        if not ok:
            errores_spec = errores
            prompt_spec = _reparar_spec_prompt(
                prompt_spec_base=prompt_spec_base,
                raw_resp=resp,
                errores=errores_spec,
            )
            spec = None
            continue

        # -------------------------
        # FASE 1.5: alineación SPEC vs ContextoNormalizado (si existe)
        # -------------------------
        if isinstance(contexto_normalizado, dict) and contexto_normalizado:
            patched, audit_errors = _alinear_spec_con_contexto(
                spec,
                contexto_normalizado,
                intentos=1,
                provider_hint="docs",
                max_tokens=1400,
            )

            # Caso clave: el precheck determinista o el auditor en modo compacto pueden devolver:
            # - patched == spec (dict) + audit_errors (lista MUST)
            # En ese caso, NO podemos aceptar el SPEC tal cual: debemos repair patch-style y reintentar.
            if isinstance(patched, dict) and audit_errors:
                errores_spec = audit_errors
                prompt_spec = _reparar_spec_desde_spec(
                    prompt_spec_base=prompt_spec_base,
                    spec_actual=patched,
                    errores=errores_spec,
                    contexto_normalizado=contexto_normalizado,
                )
                spec = None
                continue

            if isinstance(patched, dict):
                spec = patched

                ok2, errores2 = _validar_spec(spec)
                if ok2:
                    # Persistimos el SPEC alineado para diagnóstico (en debug).
                    _persistir_spec_debug(
                        nombre_archivo=f"spec_ok_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                        spec=spec,
                        descripcion_global=descripcion_global,
                        contexto_normalizado=contexto_normalizado,
                    )
                    break

                # si el patch rompe invariantes estructurales, pedimos reparación guiada
                errores_spec = (audit_errors or []) + errores2
                prompt_spec = _reparar_spec_prompt(
                    prompt_spec_base=prompt_spec_base,
                    raw_resp=resp,
                    errores=errores_spec,
                )
                spec = None
                continue

            # patched no dict => auditor no concluyente
            errores_spec = audit_errors or ["auditor: no pudo alinear SPEC con contexto"]
            prompt_spec = _reparar_spec_prompt(
                prompt_spec_base=prompt_spec_base,
                raw_resp=resp,
                errores=errores_spec,
            )
            spec = None
            continue

        # si no hay contexto_normalizado, ya es válido estructuralmente
        _persistir_spec_debug(
            nombre_archivo=f"spec_ok_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            spec=spec,
            descripcion_global=descripcion_global,
            contexto_normalizado=contexto_normalizado,
        )
        break

    if not spec:
        print("[DEBUG] No se pudo generar un SPEC válido.")
        if errores_spec:
            print("[DEBUG] Errores SPEC:", errores_spec)

        # Persistencia de debug (solo en fallo) para diagnosticar:
        # - si el LLM devolvió JSON inválido / truncado
        # - o si el SPEC incumple invariantes (entrypoint/run_command/files/endpoints[*].file, etc.)
        try:
            debug_dir = Path("output/_debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_path = debug_dir / f"spec_failure_{ts}.json"
            payload = {
                "timestamp": ts,
                "descripcion_global": descripcion_global,
                "contexto_normalizado": contexto_normalizado,
                "errores_spec": errores_spec,
                "prompt_spec": prompt_spec,
                # `resp` y `spec` no están disponibles aquí si falló antes de parsear o si se pisaron;
                # por eso guardamos lo que tengamos en variables locales si existen.
                "last_raw_response": locals().get("resp"),
                "last_parsed_spec": locals().get("spec"),
            }
            debug_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[DEBUG] SPEC failure dump guardado en: {debug_path.as_posix()}")
        except Exception as e:
            print(f"[DEBUG] No se pudo guardar SPEC failure dump: {e}")

        return {"files": []}

    files_plan = _completar_inits_en_files(spec.get("files", []))
    # Persistimos la corrección en el propio SPEC para que el modelo la vea en lotes posteriores
    spec["files"] = files_plan
    allowed_paths = set(files_plan)

    # -------------------------
    # Enriquecimiento: compilar restricciones a formato ejecutable
    # -------------------------
    spec["restrictions"] = compilar_restricciones(spec, contexto_normalizado, intentos=intentos)

    # -------------------------
    # FASE 2: GENERACIÓN POR LOTES
    # -------------------------
    # Orden estable y mantenible:
    # - Primero generar endpoints (y sus bundles) para asegurar que existe `router`.
    # - Luego generar core (main/config) que incluye routers.
    # Nota: mantenerlo como una ordenación simple (sin reglas por proveedor).
    lotes = _agrupar_lotes(files_plan, spec=spec)
    lotes = sorted(
        lotes,
        key=lambda lote: 1
        if any(p in ("app/main.py", "app/__init__.py") or p.startswith("app/config/") for p in lote)
        else 0,
    )
    files_generados: List[Dict[str, str]] = []

    for lote in lotes:
        lote_set = set(lote)

        # Extrae “contratos” y “env/deps/restrictions” del SPEC para que el modelo implemente
        # decisiones específicas de ESTA PoC sin hardcodearlas globalmente.
        contracts = spec.get("contracts", [])
        env = spec.get("env", [])
        dependencies = spec.get("dependencies", [])
        dev_dependencies = spec.get("dev_dependencies", [])
        restrictions = spec.get("restrictions", [])

        prompt_lote = f"""
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
    - En CADA `except Exception as e`: loguea SIEMPRE con stacktrace: `logger.exception(\"<contexto>\")` antes de lanzar `HTTPException`.
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
  - En `app/main.py` DEBES usar `app.include_router(<router>, prefix=\"\")` (prefix vacío) para todos los routers.
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
"""

        # reintentos por lote
        lote_files: Optional[List[Dict[str, str]]] = None
        ultimo_raw: str = ""
        errores_lote: List[str] = []

        for intento_lote in range(max(1, intentos)):
            raw = chat_completion_json(
                prompt=prompt_lote,
                system=None,
                temperature=0.2,
                max_tokens=2500,
                fase="generacion_codigo",
            )
            ultimo_raw = raw
            data = extraer_json_tolerante(raw)
            if not data:
                continue
            cand = data.get("files")
            if not isinstance(cand, list) or not cand:
                continue

            # normalizar y filtrar por lote (por si el modelo mete de más)
            cand_norm = []
            for f in cand:
                if not isinstance(f, dict):
                    continue
                p = (f.get("path") or "").replace("\\", "/")
                if p in lote_set:
                    cand_norm.append({"path": p, "content": f.get("content", "")})

            # Debug mantenible: si el modelo "olvida" contenido (p.ej. endpoints vacíos),
            # lo veremos explícitamente. Esto es clave para entender por qué `router`
            # no aparece aunque sea obligatorio.
            missing = sorted(
                list(lote_set - {ff.get("path") for ff in cand_norm if ff.get("path")})
            )
            empty = sorted(
                [ff.get("path") for ff in cand_norm if not (ff.get("content") or "").strip()]
            )
            if missing or empty:
                print(f"[DEBUG] Lote generado incompleto. Missing={missing} Empty={empty}")

            # __init__.py puede estar vacío legítimamente; no lo tratamos como error
            empty = [p for p in empty if not str(p).endswith("/__init__.py")]
            ok_nonempty = not missing and not empty
            ok_paths, e_paths = _validar_paths_generados(cand_norm, lote_set)
            ok_ast = _validar_proyecto(cand_norm)

            # NOTA (coste vs coherencia):
            # La validación de imports internos contra el estado acumulado es útil, pero cara en tokens
            # porque obliga al modelo a “adivinar” dependencias en el mismo lote.
            # Nueva política: en validación por lote SOLO exigimos:
            # - contenido no vacío
            # - paths válidos
            # - AST válido
            # La coherencia de imports se valida al FINAL (fase 3) y se repara de forma dirigida.
            if ok_nonempty and ok_paths and ok_ast:
                lote_files = cand_norm
                break

            errores_lote = []
            if not ok_nonempty:
                for pth in missing:
                    errores_lote.append(f"Archivo no devuelto por el modelo: {pth}")
                for pth in empty:
                    errores_lote.append(f"Archivo sin contenido (content vacío): {pth}")
            if not ok_paths:
                errores_lote.extend(e_paths)
            if not ok_ast:
                errores_lote.append("Fallo de sintaxis (AST) en algún archivo del lote")

            # Último intento: si faltan archivos, pedir SOLO los missing (quirúrgico)
            if intento_lote == max(1, intentos) - 1 and missing:
                prompt_lote = f"""
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
"""
            else:
                prompt_lote = f"""
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
"""

        if not lote_files:
            print("[DEBUG] No se pudo generar un lote válido.")
            if errores_lote:
                print("[DEBUG] Errores lote:", errores_lote)
            return {"files": []}

        # Merge al conjunto global.
        # Regla mantenible: nunca sobreescribir un archivo ya generado con contenido NO vacío
        # por una versión vacía/blanca (esto suele ser un fallo del modelo en reintentos o lotes posteriores).
        existentes = {(f["path"].replace("\\", "/")): f for f in files_generados}

        for f in lote_files:
            pth = f["path"]
            new_content = f.get("content") or ""
            prev = existentes.get(pth)
            prev_content = (prev.get("content") or "") if isinstance(prev, dict) else ""

            if prev and prev_content.strip() and not new_content.strip():
                print(
                    f"[DEBUG] Merge: ignorando sobrescritura VACÍA de '{pth}' "
                    f"(prev_len={len(prev_content)}, new_len={len(new_content)})"
                )
                continue

            existentes[pth] = f

        files_generados = list(existentes.values())

    # -------------------------
    # FASE 3: VALIDACIÓN FINAL
    # -------------------------
    # DEBUG: confirmar restrictions finales (post-compilación + sanitización)
    try:
        rs = spec.get("restrictions", [])
        if isinstance(rs, list) and rs:
            resumen = [
                {
                    "id": r.get("id"),
                    "applies_to": r.get("applies_to"),
                    "must_any_len": len(r.get("must_contain_any") or []),
                    "must_not_len": len(r.get("must_not_contain") or []),
                }
                for r in rs
                if isinstance(r, dict)
            ]
            print(
                "[DEBUG] Restrictions finales (resumen):",
                json.dumps(resumen, ensure_ascii=False),
            )
    except Exception:
        pass

    if not _validar_y_reparar_final(
        spec=spec,
        files_generados=files_generados,
        allowed_paths=allowed_paths,
        intentos=intentos,
    ):
        print("[DEBUG] Fase final de validación/repair no convergió.")
        return {"files": []}

    return {"files": files_generados, "spec": spec}
