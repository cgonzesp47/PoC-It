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


def _extraer_json_tolerante(respuesta: str) -> Optional[dict]:
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
    try:
        return json.loads(s)
    except Exception:
        pass

    # 3) fallback "greedy": del primer '{' al último '}' (si existe)
    try:
        start = s.index("{")
        end = s.rindex("}")
        candidate = s[start : end + 1]
        return json.loads(candidate)
    except Exception:
        return None


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


from poc_it.generador.spec_validation import (
    persistir_spec_debug as _persistir_spec_debug,
)

from poc_it.generador.spec_alignment import (
    alinear_spec_con_contexto as _alinear_spec_con_contexto,
)


from poc_it.generador.spec_validation import (
    normalizar_paths as _normalizar_paths,
    completar_inits_en_files as _completar_inits_en_files,
)


from poc_it.generador.spec_validation import (
    validar_spec as _validar_spec,
)


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


from poc_it.generador.utils_imports import (
    extraer_imports,
    extraer_from_imports,
)






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

def _sanitizar_restrictions(restrictions: List[dict]) -> List[dict]:
    """
    Limpieza defensiva y GENÉRICA de restrictions (enforcement-ready).

    Objetivo:
    - Mantener restricciones útiles (evitar alucinaciones claras).
    - Reducir falsos positivos que bloquean PoCs realistas.

    Convenciones:
    - Cada restriction puede incluir opcionalmente:
      - severity: "BLOCK" | "WARN"
      - kind: "SECURITY" | "STRUCTURAL" | "CONTRACT" | "QUALITY"
    - Si no viene severity, la inferimos por TIPO (kind) y por heurísticas de riesgo
      (sin mirar palabras clave específicas de una PoC concreta).
    """
    if not isinstance(restrictions, list):
        return []

    def _norm_patterns(applies_to_val: Any) -> List[str]:
        if not isinstance(applies_to_val, list):
            return []
        return [str(x).replace("\\", "/").strip() for x in applies_to_val if str(x).strip()]

    def _is_overbroad(applies_to_val: Any) -> bool:
        """
        Overbroad = aplica a "todo" o a scopes tan amplios que hacen `must_contain_any` frágil.
        Ejemplos típicos:
        - ["*"], ["*.py"]
        - ["app/*"], ["app/"], ["app/*.py"]
        - cualquier lista que incluya "*" o "*.py"
        """
        norm = _norm_patterns(applies_to_val)
        if not norm:
            return True
        if "*" in norm or "*.py" in norm:
            return True
        if any(p in ("app/*", "app/", "app/*.py") for p in norm):
            return True
        return False

    def _infer_kind(rr: dict) -> str:
        """
        Clasificación de tipo (agnóstica):
        - SECURITY: evitar secretos/credenciales embebidas
        - STRUCTURAL: invariantes del generador (paths/imports/routers), baja tolerancia al incumplimiento
        - CONTRACT: invariantes del contrato (request/response/endpoints) pero suelen ser verificables por otras vías
        - QUALITY: estilo/calidad/mantenibilidad (no deben bloquear generación)
        """
        kind = str(rr.get("kind") or "").strip().upper()
        if kind in ("SECURITY", "STRUCTURAL", "CONTRACT", "QUALITY"):
            return kind

        must_not = [str(x).lower() for x in (rr.get("must_not_contain") or []) if str(x).strip()]
        must_any = [str(x).lower() for x in (rr.get("must_contain_any") or []) if str(x).strip()]

        # Heurística genérica de secretos (alto riesgo real)
        high_risk = ("password", "private_key", "api_key", "apikey", "secret", "token=")
        if any(any(t in p for t in high_risk) for p in (must_not + must_any)):
            return "SECURITY"

        # must_contain_any suele ser "contractual/guía", no estructural
        if must_any:
            return "CONTRACT"

        # default conservador: QUALITY (no bloquear)
        return "QUALITY"

    def _inferir_severidad(rr: dict) -> str:
        """
        Política por tipo:
        - SECURITY -> BLOCK (evitar credenciales/secretos en código)
        - STRUCTURAL -> BLOCK (si se usan para invariantes internas del generador)
        - CONTRACT -> WARN (preferimos no bloquear: hay otras validaciones deterministas)
        - QUALITY -> WARN
        """
        kind = _infer_kind(rr)
        if kind in ("SECURITY", "STRUCTURAL"):
            return "BLOCK"
        return "WARN"

    cleaned: List[dict] = []
    for r in restrictions:
        if not isinstance(r, dict):
            continue

        rr = dict(r)

        # Normalización defensiva
        applies_to = rr.get("applies_to")
        must_any = rr.get("must_contain_any") or []
        must_not = rr.get("must_not_contain") or []

        if not isinstance(must_any, list):
            must_any = [str(must_any)]
        if not isinstance(must_not, list):
            must_not = [str(must_not)]
        rr["must_contain_any"] = must_any
        rr["must_not_contain"] = must_not

        rr["kind"] = _infer_kind(rr)

        # Severidad: si no viene, inferir por tipo (kind)
        severity = str(rr.get("severity") or "").strip().upper()
        if severity not in ("BLOCK", "WARN"):
            severity = _inferir_severidad(rr)
        rr["severity"] = severity

        def _looks_api_specific_must_any(patterns: List[str]) -> bool:
            """
            must_contain_any demasiado específico a una API/implementación concreta.
            Ejemplo: exigir exactamente os.getenv('DATABASE_URL') cuando también sería válido
            usar Settings, os.environ.get, etc.
            """
            joined = " ".join(str(x) for x in patterns)
            return "os.getenv(" in joined or "os.environ[" in joined or "os.environ.get(" in joined

        # must_contain_any es frágil si aplica a demasiado scope:
        # - lo anulamos
        # - y degradamos severidad a WARN
        if must_any and _is_overbroad(applies_to):
            rr["must_contain_any"] = []
            rr["severity"] = "WARN"
            rr["kind"] = "QUALITY"

        # aunque el scope sea específico, si must_contain_any exige una API concreta, no bloqueamos
        if must_any and _looks_api_specific_must_any(must_any):
            rr["severity"] = "WARN"
            if rr.get("kind") == "STRUCTURAL":
                rr["kind"] = "CONTRACT"

        cleaned.append(rr)

    return cleaned


def _compilar_restricciones(
    spec: dict,
    contexto_normalizado: dict | None,
    intentos: int = 2,
) -> List[dict]:
    """
    Compila restricciones en lenguaje natural (contexto_normalizado.restricciones_tecnicas)
    a reglas ejecutables (spec.restrictions).

    Objetivo:
    - Mecanismo genérico para que el sistema NO ignore restricciones del usuario.
    - Evita hardcodear tecnologías: la traducción a patrones la hace un LLM pequeño (JSON).
    - Salida: lista de dicts con keys: id, applies_to?, must_not_contain?, must_contain_any?
    """
    restricciones = []
    if isinstance(spec.get("restrictions"), list):
        restricciones = [r for r in spec.get("restrictions", []) if isinstance(r, dict)]

    # Si ya hay restricciones ejecutables, las respetamos y no gastamos tokens.
    # PERO: aplicamos una sanitización genérica para evitar falsos positivos masivos.
    if restricciones:
        return _sanitizar_restrictions(restricciones)

    restricciones_tecnicas = []
    if isinstance(contexto_normalizado, dict):
        restricciones_tecnicas = contexto_normalizado.get("restricciones_tecnicas") or []

    # Si no hay restricciones en texto, no generamos nada.
    if not restricciones_tecnicas:
        return []

    prompt = f"""
TAREA
Convierte las RESTRICCIONES del usuario (texto) en reglas ejecutables para validar código.

ENTRADA
- Restricciones (texto):
{json.dumps(restricciones_tecnicas, ensure_ascii=False)}

- Archivos del proyecto (paths) para acotar applies_to:
{json.dumps(spec.get("files", []), ensure_ascii=False)}

SALIDA (JSON)
Devuelve EXCLUSIVAMENTE JSON válido con:
{{
  "restrictions": [
    {{
      "id": "string_corto",
      "applies_to": ["*.py"], 
      "must_not_contain": ["substr1", "..."],
      "must_contain_any": ["substrA", "..."]
    }}
  ]
}}

REGLAS IMPORTANTES (GENÉRICAS)
- No inventes tecnologías no mencionadas en las restricciones.
- Solo genera restricciones que se puedan validar de forma razonable con substrings o regex.
- Usa patrones REALISTAS de código/config para detectar incumplimientos (imports, funciones típicas, variables de entorno, dependencias).
- Minimiza falsos positivos: preferir pocos patrones pero discriminativos.
- Si una restricción es conceptual y no se puede validar por patrones simples, omítela.

SOPORTE REGEX (IMPORTANTE)
- En must_contain_any y must_not_contain puedes usar items que empiecen por `re:` para expresar regex.
- Usa regex SOLO cuando haya variaciones sintácticas habituales (ej. imports equivalentes en Python).

- EVITA restricciones demasiado amplias que rompen el proyecto:
  - No generes must_not_contain con tokens genéricos como: "import", "from", "def", "class".
  - “/health sin dependencias externas” NO significa “sin imports”: significa sin SDKs/clientes externos, sin red, sin llamadas a servicios.
- `must_contain_any` significa: al menos UNA de esas substrings/patrones debe aparecer en algún archivo que aplique.
- `must_not_contain` significa: ninguna de esas substrings/patrones debe aparecer.

REGLA (GENÉRICA) - EVITAR RESTRICCIONES INÚTILES/FRÁGILES
- NO generes restricciones que dupliquen validaciones ya hechas por el sistema (imports internos, paths, sintaxis).
- NO generes restricciones con tokens genéricos o estructurales del lenguaje (ej: "import", "from", "def", "class", comillas, docstrings).
- Si una restricción requiere tolerar variaciones sintácticas, usa `re:`. Si no, usa substring.
"""
    for _ in range(max(1, intentos)):
        raw = chat_completion_json(
            prompt=prompt,
            system=None,
            temperature=0.1,
            max_tokens=700,
            fase="generacion_codigo",
        )
        data = _extraer_json_tolerante(raw) or {}
        rs = data.get("restrictions")
        if isinstance(rs, list):
            out = []
            for r in rs:
                if not isinstance(r, dict):
                    continue
                rid = str(r.get("id") or "").strip()
                if not rid:
                    continue
                out.append(
                    {
                        "id": rid,
                        "applies_to": r.get("applies_to") if isinstance(r.get("applies_to"), list) else ["*.py"],
                        "must_not_contain": r.get("must_not_contain") if isinstance(r.get("must_not_contain"), list) else [],
                        "must_contain_any": r.get("must_contain_any") if isinstance(r.get("must_contain_any"), list) else [],
                    }
                )
            # Filtro de seguridad post-proceso:
            # - elimina reglas con tokens absurdamente genéricos (falsos positivos)
            # - elimina reglas que se aplicarían a TODO el código Python pero exigen patrones específicos de una integración
            #   (p.ej. obligar a "google.auth.default()" en health.py/__init__.py/main.py).
            banned = {"import", "from", "def", "class", '"""', "'''", '"', "'"}
            cleaned: List[dict] = []

            def _is_overbroad_applies_to(applies_to_val: Any) -> bool:
                if not isinstance(applies_to_val, list) or not applies_to_val:
                    return True
                norm = [str(x).strip() for x in applies_to_val if str(x).strip()]
                return norm == ["*"] or norm == ["*.py"] or "*" in norm

            def _looks_integration_specific(patterns: List[str]) -> bool:
                # Heurística genérica: si exige un token con puntos/paréntesis típico de una API
                # (ej. "google.auth.default()", "drive.permissions().create("), eso NO debe exigirse
                # sobre todo el proyecto; solo sobre módulos de integración.
                for p in patterns:
                    s = str(p)
                    if s.startswith("re:"):
                        s = s[3:]
                    if "." in s and "(" in s:
                        return True
                return False

            def _has_structural_must_contain(patterns: List[str]) -> bool:
                # Patrones demasiado “estructurales” para exigirlos como must_contain_any.
                # Ej: forzar "from app" en todos los módulos provoca falsos positivos masivos.
                structural = ("from app", "import app", "app.")
                for p in patterns:
                    s = str(p).lower()
                    if any(tok in s for tok in structural):
                        return True
                return False

            for rr in out:
                if not isinstance(rr, dict):
                    continue

                must_not = rr.get("must_not_contain") or []
                must_any = rr.get("must_contain_any") or []
                applies_to = rr.get("applies_to")

                if isinstance(must_not, list) and any(str(x).strip().lower() in banned for x in must_not):
                    continue

                # Descarta reglas must_contain_any overbroad con patrones estructurales (falsos positivos)
                if (
                    must_any
                    and isinstance(must_any, list)
                    and _is_overbroad_applies_to(applies_to)
                    and _has_structural_must_contain(must_any)
                ):
                    continue

                # Si es una regla overbroad y además exige patrones específicos -> descartar (falsos positivos)
                if (
                    must_any
                    and isinstance(must_any, list)
                    and _is_overbroad_applies_to(applies_to)
                    and _looks_integration_specific(must_any)
                ):
                    continue

                # Normalización genérica: si la regla intenta imponer "imports absolutos" con must_contain_any,
                # la convertimos a validación negativa (más robusta y compatible con __init__.py vacíos).
                rid = str(rr.get("id") or "").strip().lower()
                if rid in ("use-absolute-imports", "use_absolute_imports", "absolute-imports"):
                    rr["must_contain_any"] = []
                    rr["must_not_contain"] = list(
                        set((rr.get("must_not_contain") or []) + ["from services", "import services", "from endpoints", "import endpoints", "from config", "import config"])
                    )

                cleaned.append(rr)

            return _sanitizar_restrictions(cleaned)
    return []

def _aplicar_patch_en_memoria(
    files_generados: List[Dict[str, str]],
    patch_files: List[Dict[str, str]],
) -> None:
    """
    Aplica un patch (lista de {"path","content"}) sobre `files_generados` EN MEMORIA.
    Regla conservadora: solo sobrescribe si el nuevo contenido no está vacío.
    """
    if not isinstance(patch_files, list) or not patch_files:
        return

    idx = {(f.get("path") or "").replace("\\", "/"): i for i, f in enumerate(files_generados) if isinstance(f, dict)}
    for f in patch_files:
        if not isinstance(f, dict):
            continue
        p = (f.get("path") or "").replace("\\", "/")
        c = f.get("content") or ""
        if not p or not c.strip():
            continue
        if p in idx:
            files_generados[idx[p]]["content"] = c
        else:
            files_generados.append({"path": p, "content": c})


def _extraer_errores_por_archivo(errores: List[str]) -> Dict[str, List[str]]:
    """
    Agrupa errores tipo "<path>: ..." por path.
    """
    out: Dict[str, List[str]] = {}
    for e in errores or []:
        s = str(e)
        if ":" not in s:
            continue
        p, rest = s.split(":", 1)
        p = p.strip().replace("\\", "/")
        if not p:
            continue
        out.setdefault(p, []).append(rest.strip())
    return out


def _seleccionar_error_bloqueante(errores: List[str]) -> Optional[Tuple[str, str]]:
    """
    Selecciona un único error "prioritario" para reparación atómica.
    Devuelve (path, mensaje) si puede; si no, None.
    """
    by_file = _extraer_errores_por_archivo(errores)

    # Heurística simple y estable:
    # - priorizar archivos de config/DB (suelen disparar security/env)
    # - luego endpoints/services
    preferred_prefixes = ("app/config/", "app/db", "app/settings", "app/endpoints/", "app/services/")
    for pref in preferred_prefixes:
        for p, msgs in by_file.items():
            if p.startswith(pref) or p == "app/db.py":
                return p, msgs[0]

    for p, msgs in by_file.items():
        return p, msgs[0]
    return None


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


def _guardrails_por_spec(
    spec: dict,
    files_generados: List[Dict[str, str]],
) -> Tuple[bool, List[str], List[str], List[str]]:
    """
    Guardrails genéricos basados en SPEC (sin conocimiento de dominio):
    - No endpoints extra (routers incluidos y decorators deben corresponder al SPEC)
    - Enforce request.type json vs multipart (UploadFile/File/python-multipart)
    - Health determinista si request.type=none y response.json_example fijo
    - Logging obligatorio: si hay 'except Exception' debe haber logger.exception en el mismo fichero
    - Enforce restricciones declaradas en SPEC.restrictions (con severidad BLOCK/WARN)
    Devuelve: (ok, errores_bloqueantes, paths_a_reparar, warnings)
    """
    errores: List[str] = []
    warnings: List[str] = []
    reparar: Set[str] = set()

    # --- índice path->content ---
    by_path: Dict[str, str] = {
        (f.get("path") or "").replace("\\", "/"): (f.get("content") or "")
        for f in files_generados
        if f.get("path")
    }

    # --- endpoints esperados del SPEC ---
    spec_eps = spec.get("endpoints", [])
    expected_ep_files: Set[str] = set()
    expected_ep_paths: Set[Tuple[str, str]] = set()  # (METHOD, "/path")
    request_type_by_path: Dict[str, str] = {}
    response_example_by_path: Dict[str, Any] = {}

    if isinstance(spec_eps, list):
        for ep in spec_eps:
            if not isinstance(ep, dict):
                continue
            ep_file = str(ep.get("file") or "").replace("\\", "/")
            if ep_file:
                expected_ep_files.add(ep_file)

            method = str(ep.get("method") or "").upper().strip()
            path = str(ep.get("path") or "").strip()
            if method and path:
                expected_ep_paths.add((method, path))
                req = ep.get("request") or {}
                if isinstance(req, dict):
                    rt = str(req.get("type") or "").strip()
                    if rt:
                        request_type_by_path[path] = rt
                resp = ep.get("response") or {}
                if isinstance(resp, dict) and "json_example" in resp:
                    response_example_by_path[path] = resp.get("json_example")

    # --- 1) main.py: include_router debe incluir SOLO routers de endpoints listados ---
    main_src = by_path.get("app/main.py", "")
    if main_src:
        # Heurística: si main.py importa app.endpoints.X y X.py no está en spec -> error
        for m in re.findall(r"from\\s+app\\.endpoints\\.([a-zA-Z0-9_]+)\\s+import\\s+router", main_src):
            f = f"app/endpoints/{m}.py"
            if expected_ep_files and f not in expected_ep_files:
                errores.append(f"Endpoint extra no listado en SPEC (importado en main.py): {f}")
                reparar.add("app/main.py")

    # --- 2) Enforce request.type=json vs multipart ---
    # Estrategia robusta:
    # - Si el SPEC declara explícitamente request.type por endpoint, lo aplicamos por path.
    # - Si el SPEC NO lo declara (modelo incompleto), evitamos falsos positivos y NO bloqueamos la generación.
    any_multipart_endpoint = any(rt == "multipart" for rt in request_type_by_path.values())

    # Si el SPEC no trae request.type, no podemos validar multipart vs json.
    has_request_types = len(request_type_by_path) > 0

    if has_request_types:
        # Prohíbe multipart global si no hay ningún endpoint multipart declarado.
        # Para reducir intermitencias, solo lo aplicamos cuando el SPEC declara
        # al menos un endpoint JSON (es decir, sabemos que NO debe ser multipart).
        any_json_endpoint = any(rt == "json" for rt in request_type_by_path.values())

        if any_json_endpoint:
            for p, src in by_path.items():
                if not p.startswith("app/endpoints/") or not p.endswith(".py"):
                    continue
                uses_uploadfile = "UploadFile" in src or "File(" in src
                if uses_uploadfile and not any_multipart_endpoint:
                    errores.append(
                        f"Se detecta multipart (UploadFile/File) pero el SPEC declara endpoints json y no declara multipart: {p}"
                    )
                    reparar.add(p)
                    # Asegurar siempre que el repair incluya requirements.txt para quitar python-multipart.
                    reparar.add("requirements.txt")

        # requirements: python-multipart solo si existe endpoint multipart en SPEC
        reqs = by_path.get("requirements.txt", "")
        if reqs:
            has_multipart = any(line.strip().lower() == "python-multipart" for line in reqs.splitlines())
            if has_multipart and not any_multipart_endpoint:
                errores.append(
                    "requirements.txt incluye python-multipart pero el SPEC no declara endpoints multipart."
                )
                reparar.add("requirements.txt")

    # --- 3) Logging obligatorio: except Exception -> logger.exception ---
    # Punto medio: WARNING por defecto; BLOCK solo en endpoints/servicios donde la trazabilidad es crítica.
    for p, src in by_path.items():
        if not p.endswith(".py"):
            continue
        if "except Exception" in src and "logger.exception" not in src:
            msg = f"Falta logging obligatorio (logger.exception) en: {p}"
            if p.startswith(("app/endpoints/", "app/services/")):
                errores.append(msg)
                reparar.add(p)
            else:
                warnings.append(msg)

    # --- 4) Respuesta coherente con json_example (GENÉRICO / PRAGMÁTICO) ---
    # Problema con la regla anterior:
    # - Un json_example es un EJEMPLO, no un valor determinista.
    # - En PoCs típicas (CRUD/DB/externos) el contenido varía y el endpoint puede necesitar try/except.
    #
    # Nueva regla:
    # - Para endpoints con request.type == "none" y response.json_example == dict:
    #   - NO se exige "return exacto"
    #   - Se valida de forma suave que el código menciona las claves esperadas (heurística)
    #   - Mantiene un guardrail útil sin bloquear PoCs realistas
    for path, json_example in response_example_by_path.items():
        request_type = request_type_by_path.get(path)
        if request_type != "none":
            continue
        if not isinstance(json_example, dict) or not json_example:
            continue

        expected_keys = [str(k) for k in json_example.keys()]

        for file_path, source_code in by_path.items():
            if not file_path.startswith("app/endpoints/") or not file_path.endswith(".py"):
                continue
            if path not in source_code or "@router" not in source_code:
                continue

            # Si el endpoint NO menciona ninguna key del ejemplo, es muy probable que no cumpla el contrato.
            mentions_any_key = any(
                f"\"{key}\"" in source_code or f"'{key}'" in source_code for key in expected_keys
            )
            if not mentions_any_key:
                errores.append(
                    f"Endpoint {path} no parece incluir ninguna de las claves esperadas {expected_keys} (json_example del SPEC): {file_path}"
                )
                reparar.add(file_path)

    # --- 5) Enforce restricciones genéricas (SPEC.restrictions) ---
    # Formato esperado (flexible):
    # restrictions: [
    #   {
    #     "id": "use_adc",
    #     "description": "... opcional ...",
    #     "applies_to": ["*.py", "app/services/*"],  # opcional; default: ["*"]
    #     "must_not_contain": ["from_service_account_file", "..."],
    #     "must_contain_any": ["google.auth.default(", "..."]
    #   }
    # ]
    def _match_glob(path: str, pattern: str) -> bool:
        # glob mínimo: * y prefijos/sufijos
        if pattern == "*" or not pattern:
            return True
        if pattern.startswith("*") and pattern.endswith("*"):
            return pattern.strip("*") in path
        if pattern.startswith("*"):
            return path.endswith(pattern[1:])
        if pattern.endswith("*"):
            return path.startswith(pattern[:-1])
        return path == pattern

    def _is_regex(s: str) -> bool:
        return isinstance(s, str) and s.startswith("re:")

    def _needle_matches(src: str, needle: str) -> bool:
        """
        Matcheo flexible para restricciones:
        - Por defecto: substring literal
        - Si needle empieza por 're:': se interpreta como regex (Python re)
        """
        if not isinstance(needle, str) or needle == "":
            return False
        if _is_regex(needle):
            pattern = needle[3:]
            try:
                return re.search(pattern, src) is not None
            except re.error:
                # regex inválida: fallback a substring del patrón bruto
                return pattern in src
        return needle in src

    def _applies(path: str, patterns: Iterable[str]) -> bool:
        return any(_match_glob(path, p) for p in patterns)

    restrictions = spec.get("restrictions", [])
    if isinstance(restrictions, list) and restrictions:
        for r in restrictions:
            if not isinstance(r, dict):
                continue

            rid = str(r.get("id") or "restriction").strip()
            applies_to = r.get("applies_to")
            if not isinstance(applies_to, list) or not applies_to:
                applies_to = ["*"]

            severity = str(r.get("severity") or "").strip().upper()
            if severity not in ("BLOCK", "WARN"):
                # default conservador
                severity = "BLOCK"

            must_not = r.get("must_not_contain") or []
            must_any = r.get("must_contain_any") or []

            if not isinstance(must_not, list):
                must_not = [str(must_not)]
            if not isinstance(must_any, list):
                must_any = [str(must_any)]

            for p, src in by_path.items():
                if not _applies(p, applies_to):
                    continue

                # must_not_contain
                for needle in must_not:
                    if not needle:
                        continue
                    if _needle_matches(src, str(needle)):
                        msg = f"{p}: viola restriction '{rid}': contiene '{needle}'"
                        if severity == "BLOCK":
                            errores.append(msg)
                            reparar.add(p)
                        else:
                            warnings.append(msg)

                # must_contain_any (si se define, al menos uno debe aparecer)
                if must_any:
                    if not any(_needle_matches(src, str(needle)) for needle in must_any):
                        msg = f"{p}: viola restriction '{rid}': no contiene ninguno de {must_any}"
                        if severity == "BLOCK":
                            errores.append(msg)
                            reparar.add(p)
                        else:
                            warnings.append(msg)

    ok = len(errores) == 0
    return ok, errores, sorted(reparar), warnings


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
- "dependencies": lista de dependencias PyPI mínimas (si aplica)
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
    idx = {(f.get("path") or "").replace("\\", "/"): i for i, f in enumerate(base) if isinstance(f, dict)}
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
    spec["restrictions"] = _compilar_restricciones(spec, contexto_normalizado, intentos=intentos)

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

        # Fase 3 (misma lógica final) sobre el estado existente
        if not _validar_proyecto(files_generados):
            return {"files": []}

        ok_imports, e_imports = _validar_imports_internos(files_generados, allowed_paths)
        if not ok_imports:
            repair_paths: List[str] = []
            for err in e_imports:
                if ":" in err:
                    p = err.split(":", 1)[0].strip()
                    if p and p not in repair_paths:
                        repair_paths.append(p)
                m = re.search(r"\((app\/[^\)]+\.py)\)", str(err))
                if m:
                    target_path = m.group(1).replace("\\", "/").strip()
                    if target_path and target_path not in repair_paths:
                        repair_paths.append(target_path)

            if not repair_paths:
                return {"files": []}

            prompt_fix_imports = f"""
Hay errores de imports internos (coherencia entre módulos) que impiden ejecutar el proyecto.
Corrige SOLO estos archivos y ninguno más.

Errores:
- {chr(10).join(e_imports)}

Archivos a corregir (paths exactos):
{json.dumps(repair_paths, ensure_ascii=False)}

SPEC (fuente de verdad):
{json.dumps(spec, ensure_ascii=False)}

SALIDA (JSON):
{{ "files": [{{"path":"...", "content":"..."}}] }}

REGLAS:
- Devuelve SOLO los archivos listados.
- No inventes nuevos paths.
- Si un archivo hace `from app.x.y import SIMBOLO`, entonces SIMBOLO debe existir realmente en el módulo importado.
- Si la dependencia importada NO existe en spec.files, elimina ese import y reestructura el código para no necesitarla.
- Mantén los endpoints exactamente como en el SPEC (mismos paths y métodos).
- Si hay try/except: incluye logging obligatorio con logger.exception().
"""
            raw = chat_completion_json(
                prompt=prompt_fix_imports,
                system=None,
                temperature=0.1,
                max_tokens=1600,
                fase="generacion_codigo",
            )
            data = _extraer_json_tolerante(raw) or {}
            cand = data.get("files")
            if isinstance(cand, list) and cand:
                _aplicar_patch_en_memoria(files_generados, cand)

                ok_imports2, e_imports2 = _validar_imports_internos(files_generados, allowed_paths)
                if not ok_imports2:
                    return {"files": []}
            else:
                return {"files": []}

        ok_guard, e_guard, repair_paths, guard_warnings = _guardrails_por_spec(spec, files_generados)
        if not ok_guard:
            if not repair_paths:
                return {"files": []}

            max_guardrail_repairs = max(2, intentos)
            for _ in range(max_guardrail_repairs):
                sel = _seleccionar_error_bloqueante(e_guard)
                if not sel:
                    break
                target_path, target_msg = sel

                prompt_fix = _build_repair_prompt_por_restriccion(
                    spec=spec,
                    full_errors=e_guard,
                    target_error_path=target_path,
                    target_error_msg=target_msg,
                    repair_paths=repair_paths,
                    files_generados=files_generados,
                )

                raw = chat_completion_json(
                    prompt=prompt_fix,
                    system=None,
                    temperature=0.1,
                    max_tokens=1600,
                    fase="generacion_codigo",
                )
                data = _extraer_json_tolerante(raw) or {}
                cand = data.get("files")
                if isinstance(cand, list) and cand:
                    _aplicar_patch_en_memoria(files_generados, cand)

                    ok_guard, e_guard, repair_paths, _ = _guardrails_por_spec(spec, files_generados)
                    if ok_guard:
                        break
                    continue
                break

            if not ok_guard:
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
- Dependencias esperadas:
{json.dumps(dependencies, ensure_ascii=False)}
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
            data = _extraer_json_tolerante(raw)
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

    ok_guard, e_guard, repair_paths, guard_warnings = _guardrails_por_spec(spec, files_generados)
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
        spec = _extraer_json_tolerante(resp)

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
    spec["restrictions"] = _compilar_restricciones(spec, contexto_normalizado, intentos=intentos)

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
- Dependencias esperadas:
{json.dumps(dependencies, ensure_ascii=False)}
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
            data = _extraer_json_tolerante(raw)
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
    # 1) AST global
    if not _validar_proyecto(files_generados):
        print("[DEBUG] Fallo AST en validación final.")
        return {"files": []}

    # 2) Imports globales vs allowed_paths
    ok_imports, e_imports = _validar_imports_internos(files_generados, allowed_paths)
    if not ok_imports:
        print("[DEBUG] Fallo imports en validación final:", e_imports)

        # Repair loop dirigido para imports:
        # Pedimos SOLO los ficheros implicados (o los que importan símbolos inexistentes),
        # y dejamos que el modelo ajuste imports o cree símbolos faltantes según el SPEC.
        # Estrategia: reparar los propios ficheros que reporta el validador (antes de pasar a guardrails).
        repair_paths: List[str] = []
        for err in e_imports:
            # Formato típico: "<path>: ... "
            if ":" in err:
                p = err.split(":", 1)[0].strip()
                if p and p not in repair_paths:
                    repair_paths.append(p)

            # Caso especial: from-import inválido por símbolo inexistente.
            # Incluimos también el módulo TARGET (el que está entre paréntesis) para reparar ambos a la vez.
            m = re.search(r"\((app\/[^\)]+\.py)\)", str(err))
            if m:
                target_path = m.group(1).replace("\\", "/").strip()
                if target_path and target_path not in repair_paths:
                    repair_paths.append(target_path)

        if not repair_paths:
            return {"files": []}

        prompt_fix_imports = f"""
Hay errores de imports internos (coherencia entre módulos) que impiden ejecutar el proyecto.
Corrige SOLO estos archivos y ninguno más.

Errores:
- {chr(10).join(e_imports)}

Archivos a corregir (paths exactos):
{json.dumps(repair_paths, ensure_ascii=False)}

SPEC (fuente de verdad):
{json.dumps(spec, ensure_ascii=False)}

SALIDA (JSON):
{{ "files": [{{"path":"...", "content":"..."}}] }}

REGLAS:
- Devuelve SOLO los archivos listados.
- No inventes nuevos paths.
- Si un archivo hace `from app.x.y import SIMBOLO`, entonces SIMBOLO debe existir realmente en el módulo importado.
- Si la dependencia importada NO existe en spec.files, elimina ese import y reestructura el código para no necesitarla.
- Mantén los endpoints exactamente como en el SPEC (mismos paths y métodos).
- Si hay try/except: incluye logging obligatorio con logger.exception().
"""
        raw = chat_completion_json(
            prompt=prompt_fix_imports,
            system=None,
            temperature=0.1,
            max_tokens=1600,
            fase="generacion_codigo",
        )
        data = _extraer_json_tolerante(raw) or {}
        cand = data.get("files")
        if isinstance(cand, list) and cand:
            patch = {
                (f.get("path") or "").replace("\\", "/"): (f.get("content") or "")
                for f in cand
                if isinstance(f, dict)
            }
            for i, f in enumerate(files_generados):
                p = (f.get("path") or "").replace("\\", "/")
                if p in patch and patch[p].strip():
                    files_generados[i]["content"] = patch[p]

            # revalidar imports tras repair
            ok_imports2, e_imports2 = _validar_imports_internos(files_generados, allowed_paths)
            if not ok_imports2:
                print("[DEBUG] Imports siguen fallando tras repair:", e_imports2)
                return {"files": []}
        else:
            return {"files": []}

    # 3) Guardrails por SPEC (contrato usuario): si fallan, intentamos repair dirigido
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
            print("[DEBUG] Restrictions finales (resumen):", json.dumps(resumen, ensure_ascii=False))
    except Exception:
        pass

    ok_guard, e_guard, repair_paths, guard_warnings = _guardrails_por_spec(spec, files_generados)
    if guard_warnings:
        print("[DEBUG] Guardrails warnings:", guard_warnings)

    if not ok_guard:
        print("[DEBUG] Fallo guardrails SPEC:", e_guard)

        if not repair_paths:
            return {"files": []}

        # Repair loop por RESTRICCIÓN (atómico):
        # - Selecciona 1 error bloqueante
        # - Pide al LLM un patch mínimo y verificable
        # - Revalida y repite N veces
        max_guardrail_repairs = max(2, intentos)

        for _ in range(max_guardrail_repairs):
            sel = _seleccionar_error_bloqueante(e_guard)
            if not sel:
                break
            target_path, target_msg = sel

            prompt_fix = _build_repair_prompt_por_restriccion(
                spec=spec,
                full_errors=e_guard,
                target_error_path=target_path,
                target_error_msg=target_msg,
                repair_paths=repair_paths,
                files_generados=files_generados,
            )

            raw = chat_completion_json(
                prompt=prompt_fix,
                system=None,
                temperature=0.1,
                max_tokens=1600,
                fase="generacion_codigo",
            )
            data = _extraer_json_tolerante(raw) or {}
            cand = data.get("files")
            if isinstance(cand, list) and cand:
                _aplicar_patch_en_memoria(files_generados, cand)

                ok_guard, e_guard, repair_paths, guard_warnings = _guardrails_por_spec(spec, files_generados)
                if guard_warnings:
                    print("[DEBUG] Guardrails warnings:", guard_warnings)
                if ok_guard:
                    break
                continue

            # si el modelo no devuelve patch usable, no tiene sentido iterar infinito
            break

        if not ok_guard:
            print("[DEBUG] Guardrails siguen fallando tras repair:", e_guard)
            return {"files": []}

    return {"files": files_generados, "spec": spec}
