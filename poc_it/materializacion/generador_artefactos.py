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
from poc_it.generador.spec_validation import (
    SpecValidationError as _SpecValidationError,
    completar_inits_en_files as _completar_inits_en_files,
    persistir_spec_debug as _persistir_spec_debug,
    repair_spec_deterministic as _repair_spec_deterministic,
    validate_spec as _validate_spec,
)
from poc_it.generador.spec_traceability import (
    TraceabilityError as _TraceabilityError,
    validate_request_ir_to_spec_traceability as _validate_request_ir_to_spec_traceability,
)
from poc_it.generador.prompts_lotes import (
    build_prompt_lote as _build_prompt_lote,
    build_prompt_lote_fix_errors as _build_prompt_lote_fix_errors,
    build_prompt_lote_missing as _build_prompt_lote_missing,
)
from poc_it.generador.file_contracts import (
    build_file_contracts_from_spec as _build_file_contracts_from_spec,
    file_contracts_to_dict as _file_contracts_to_dict,
)
from poc_it.materializacion.file_contracts_validation import (
    validate_generated_files_against_file_contracts as _validate_generated_files_against_file_contracts,
)
from poc_it.generador.prompts_guardrails import (
    build_repair_prompt_por_restriccion as _build_repair_prompt_por_restriccion,
)
from poc_it.generador.prompts_file_contracts import (
    build_prompt_file_contract as _build_prompt_file_contract,
    build_prompt_file_contract_fix as _build_prompt_file_contract_fix,
)
from poc_it.generador.validators import (
    validar_imports_internos as _validar_imports_internos,
    validar_paths_generados as _validar_paths_generados,
    validar_proyecto as _validar_proyecto,
)
from poc_it.entrada.demo_progress import demo_progress, is_demo_mode
from poc_it.infraestructura.llm_client import chat_completion_json, solicitarJSONEstructurado


# ==========================================================
# GENERACIÓN ROBUSTA POR ARCHIVO (FileContracts)
# ==========================================================


def _sort_file_contracts_for_generation(file_contracts: List[dict]) -> List[dict]:
    """
    Orden determinista recomendado para reducir referencias rotas.

    Orden requerido por la estrategia contract-first (fase actual):
    - package_init
    - requirements
    - config
    - endpoint
    - router
    - main
    - docs
    - unknown al final
    """
    kind_order = {
        "package_init": 0,
        "requirements": 1,
        "config": 2,
        "endpoint": 3,
        "router": 4,
        "main": 5,
        "docs": 6,
    }

    def key(fc: dict) -> tuple:
        k = str(fc.get("kind") or "")
        p = str(fc.get("path") or "")
        return (kind_order.get(k, 50), p)

    return sorted([fc for fc in file_contracts if isinstance(fc, dict)], key=key)


def _debug_safe_path_token(path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", (path or "unknown").replace("/", "_").replace("\\", "_"))


def _dump_codegen_raw_for_debug(*, path: str, intento: int, raw: str) -> None:
    """
    Persiste el RAW del LLM para diagnóstico cuando viene truncado/JSON inválido.

    Requisito: output/_debug/codegen_raw_<safe_path>_attempt_<n>.txt
    """
    try:
        safe = _debug_safe_path_token(path)
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"codegen_raw_{safe}_attempt_{intento}.txt").write_text(raw or "", encoding="utf-8")
    except Exception:
        pass


def _dump_codegen_contract_errors(*, path: str, errors: List[str]) -> None:
    """
    Requisito: output/_debug/codegen_contract_errors_<safe_path>.json
    """
    try:
        safe = _debug_safe_path_token(path)
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"codegen_contract_errors_{safe}.json").write_text(
            json.dumps({"path": path, "errors": errors}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _dump_codegen_file_accepted(*, path: str, file_obj: Dict[str, str]) -> None:
    """
    Requisito: output/_debug/codegen_file_<safe_path>.json
    """
    try:
        safe = _debug_safe_path_token(path)
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"codegen_file_{safe}.json").write_text(
            json.dumps(file_obj, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _extract_single_generated_file(
    *,
    data: dict,
    expected_path: str,
    allow_empty_content: bool = False,
) -> Dict[str, str] | None:
    """
    Acepta:
    - {"path": expected_path, "content": "..."}
    - {"files": [{"path": expected_path, "content": "..."}]}

    Rechaza:
    - JSON sin path/content
    - files con != 1
    - path distinto
    - content vacío (salvo allow_empty_content=True)
    """
    expected_path = (expected_path or "").replace("\\", "/")

    if not isinstance(data, dict) or not data:
        return None

    def _extract_one(obj: dict) -> Dict[str, str] | None:
        if not isinstance(obj, dict):
            return None
        if "path" not in obj or "content" not in obj:
            return None

        p_raw = obj.get("path")
        c_raw = obj.get("content")

        if not isinstance(p_raw, str):
            return None
        if not isinstance(c_raw, str):
            return None

        p = p_raw.replace("\\", "/")
        c = c_raw

        if p != expected_path:
            return None
        if (not c.strip()) and not allow_empty_content:
            return None
        return {"path": p, "content": c}

    if "path" in data and "content" in data:
        return _extract_one(data)

    files = data.get("files")
    if isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict):
        return _extract_one(files[0])

    return None


def _parse_single_file_response(
    *,
    raw: str,
    expected_path: str,
    allow_empty_content: bool = False,
) -> Tuple[Optional[Dict[str, str]], List[str]]:
    """
    Parser tolerante: devuelve (file_obj, errors).

    Requisito: el caller debe persistir RAW si falla parseo.
    """
    expected_path = (expected_path or "").replace("\\", "/")
    data = extraer_json_tolerante(raw) or {}
    if not isinstance(data, dict) or not data:
        return None, ["json_parse_failed"]

    files = data.get("files")
    if isinstance(files, list) and len(files) > 1:
        return None, ["multiple_files_returned"]

    # Mejora de error: si path es correcto pero content vacío, distinguimos el caso.
    try:
        if "path" in data and "content" in data:
            p = data.get("path")
            c = data.get("content")
            if isinstance(p, str) and p.replace("\\", "/") == expected_path and isinstance(c, str) and not c.strip():
                if not allow_empty_content:
                    return None, ["empty_content_not_allowed"]
        if isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict):
            p = files[0].get("path")
            c = files[0].get("content")
            if isinstance(p, str) and p.replace("\\", "/") == expected_path and isinstance(c, str) and not c.strip():
                if not allow_empty_content:
                    return None, ["empty_content_not_allowed"]
    except Exception:
        pass

    file_obj = _extract_single_generated_file(
        data=data,
        expected_path=expected_path,
        allow_empty_content=allow_empty_content,
    )
    if not file_obj:
        return None, ["single_file_extract_failed"]
    return file_obj, []


def _validate_single_generated_file_against_contract(
    *,
    file: Dict[str, str],
    contract: dict,
) -> List[str]:
    """
    Validación local mínima por archivo (determinista).
    Devuelve lista de errores (strings).
    """
    errors: List[str] = []
    expected_path = str(contract.get("path") or "").replace("\\", "/")
    kind = str(contract.get("kind") or "")

    path = str(file.get("path") or "").replace("\\", "/")
    content = str(file.get("content") or "")

    if path != expected_path:
        errors.append(f"path_mismatch: expected={expected_path} got={path}")
        return errors

    if kind != "package_init":
        if not content.strip():
            errors.append("empty_content")

    # AST parse si es .py y no está vacío (permitimos __init__ vacío)
    if expected_path.endswith(".py") and content.strip():
        try:
            import ast

            ast.parse(content)
        except SyntaxError as e:
            errors.append(f"syntax_error: {e}")

    # Checks por kind (mínimos)
    required_symbols = contract.get("required_symbols") or []
    if not isinstance(required_symbols, list):
        required_symbols = []

    if kind == "main":
        if "app" in required_symbols and "app" not in content:
            errors.append("missing_symbol_app")
        if "include_router" in content and "app.include_router" not in content:
            # suave, pero útil si el modelo alucina
            pass
    elif kind == "router":
        if "api_router" in required_symbols and "api_router" not in content:
            errors.append("missing_symbol_api_router")
    elif kind == "endpoint":
        if "router = APIRouter()" not in content:
            errors.append("missing_router_assignment")
        for sym in required_symbols:
            if not isinstance(sym, str) or not sym.strip():
                continue
            if sym == "router":
                continue

            # Solo tiene sentido validar defs para identificadores Python válidos.
            # Si el contract trae símbolos con caracteres especiales (p.ej. "create_producto("),
            # no deben romper el validador.
            if not sym.isidentifier():
                continue

            # requerido como def/async def
            # Evitar falsos positivos por strings/comentarios: buscamos defs al inicio de línea.
            pattern = r"(?m)^\s*(async\s+def|def)\s+" + re.escape(sym) + r"\s*\("
            try:
                ok = bool(re.search(pattern, content))
            except re.error:
                ok = False

            if not ok:
                errors.append(f"missing_required_symbol_def:{sym}")
    elif kind == "config":
        if "class Settings" not in content:
            errors.append("config_missing_settings_class")
        if "def get_settings" not in content:
            errors.append("config_missing_get_settings")

    return errors


def _related_contracts_for(
    *,
    contract: dict,
    file_contracts: List[dict],
    spec: dict,
    max_related: int = 6,
) -> List[dict]:
    """
    Usa bundle_files SOLO como contexto: busca contracts cuyos paths estén en bundle_files.
    """
    related: List[dict] = []
    p = str(contract.get("path") or "").replace("\\", "/")

    # Map path->contract
    by_path = {str(fc.get("path") or "").replace("\\", "/"): fc for fc in file_contracts if isinstance(fc, dict)}

    # Buscar endpoint en spec que coincida con este path
    for ep in spec.get("endpoints", []) if isinstance(spec, dict) else []:
        if not isinstance(ep, dict):
            continue
        if str(ep.get("file") or "").replace("\\", "/") != p:
            continue
        extras = ep.get("bundle_files") or []
        if isinstance(extras, list):
            for x in extras:
                xp = str(x or "").replace("\\", "/")
                if xp in by_path and xp != p:
                    related.append(by_path[xp])

    # Dedup + cap
    out = []
    seen = set()
    for fc in related:
        rp = str(fc.get("path") or "").replace("\\", "/")
        if rp in seen:
            continue
        seen.add(rp)
        out.append(fc)
        if len(out) >= max_related:
            break
    return out


def _max_tokens_for_kind(kind: str) -> int:
    kind = (kind or "").strip()
    if kind in ("endpoint",):
        return 3000
    if kind in ("router", "main", "config"):
        return 2200
    if kind in ("requirements", "docs"):
        return 1400
    return 1800


def _generar_archivos_por_file_contracts(
    *,
    spec: dict,
    file_contracts: List[dict],
    intentos: int,
    descripcion_global: str = "",
    contexto_normalizado: Optional[dict] = None,
) -> List[Dict[str, str]] | None:
    """
    Estrategia robusta principal:
    1 FileContract = 1 llamada LLM = 1 archivo.

    Contrato:
    - Devuelve list[{"path","content"}] si converge para TODOS los paths obligatorios.
    - Devuelve None si algún archivo obligatorio no converge.

    Reglas:
    - No generar paths fuera de spec.files
    - No generar archivos extra
    - No usar _agrupar_lotes()
    - No usar bundle_files para agrupar generación (solo contexto opcional)
    """
    allowed_paths = set((p or "").replace("\\", "/") for p in (spec.get("files") or []) if isinstance(p, str))
    sorted_contracts = _sort_file_contracts_for_generation(file_contracts)

    # Map para acumular incrementalmente (path->content)
    by_path: Dict[str, str] = {}

    for fc in sorted_contracts:
        path = str(fc.get("path") or "").replace("\\", "/")
        kind = str(fc.get("kind") or "")
        if not path or path not in allowed_paths:
            continue

        # package_init NO debe depender del LLM: determinista y robusto.
        if kind == "package_init":
            file_obj = {"path": path, "content": ""}
            by_path[path] = ""
            _dump_codegen_file_accepted(path=path, file_obj=file_obj)
            continue

        related = _related_contracts_for(contract=fc, file_contracts=sorted_contracts, spec=spec)

        last_errors: List[str] = []

        max_intentos_por_archivo = max(2, int(intentos))

        for intento in range(1, max_intentos_por_archivo + 1):
            if intento == 1:
                prompt = _build_prompt_file_contract(
                    spec=spec,
                    file_contract=fc,
                    related_contracts=related,
                    descripcion_global=descripcion_global,
                    contexto_normalizado=contexto_normalizado,
                )
                fase = "generacion_codigo_file_contract"
                temperature = 0.2
            else:
                prompt = _build_prompt_file_contract_fix(
                    spec=spec,
                    file_contract=fc,
                    previous_content=by_path.get(path, ""),
                    errors=last_errors or ["previous_attempt_failed"],
                    related_contracts=related,
                )
                fase = "generacion_codigo_file_contract_fix"
                temperature = 0.1

            raw = solicitarJSONEstructurado(
                prompt=prompt,
                system=None,
                temperature=temperature,
                max_tokens=_max_tokens_for_kind(kind),
                fase=fase,
                provider_hint="gen-code",
            )

            file_obj, parse_errs = _parse_single_file_response(
                raw=raw,
                expected_path=path,
                allow_empty_content=False,
            )
            if parse_errs:
                _dump_codegen_raw_for_debug(path=path, intento=intento, raw=raw)
                last_errors = parse_errs
                _dump_codegen_contract_errors(path=path, errors=last_errors)
                continue

            assert file_obj is not None

            local_errs = _validate_single_generated_file_against_contract(file=file_obj, contract=fc)
            if local_errs:
                last_errors = local_errs
                _dump_codegen_contract_errors(path=path, errors=last_errors)
                continue

            fc_violations = _validate_generated_files_against_file_contracts(
                files_generados=[file_obj],
                file_contracts=[fc],
            )
            if fc_violations:
                last_errors = [f"[{v.code}] {v.path}: {v.message}" for v in fc_violations]
                _dump_codegen_contract_errors(path=path, errors=last_errors)
                continue

            # accept
            by_path[path] = file_obj.get("content", "")
            _dump_codegen_file_accepted(path=path, file_obj=file_obj)
            last_errors = []
            break

        if path not in by_path:
            # no converge para un archivo obligatorio => aborta el proyecto completo
            if not last_errors:
                last_errors = ["generation_failed"]
            _dump_codegen_contract_errors(path=path, errors=last_errors)
            return None

    # materializa lista final en orden de spec.files (sin extras)
    out: List[Dict[str, str]] = []
    for p in spec.get("files", []) if isinstance(spec, dict) else []:
        if not isinstance(p, str):
            continue
        pn = p.replace("\\", "/")
        if pn in by_path:
            out.append({"path": pn, "content": by_path[pn]})

    # hard check: todos los archivos del spec deben existir (incluyendo __init__.py autocompletados)
    missing = sorted([p for p in allowed_paths if p not in by_path])
    if missing:
        for m in missing:
            _dump_codegen_contract_errors(path=m, errors=["missing_after_generation"])
        return None

    return out


# ==========================================================
# AGRUPACIÓN DE LOTES (DEPRECATED)
# ==========================================================


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


def _generar_archivos_por_lotes(
    *,
    spec: dict,
    lotes: List[List[str]],
    intentos: int,
    file_contracts: Optional[List[dict]] = None,
) -> List[Dict[str, str]] | None:
    """
    Genera el contenido por lotes (FASE 2) sin aplicar validación final.

    Nota:
    - Mantiene el comportamiento existente: reintentos por lote, reparación por lote,
      y merge estable evitando sobreescritura por contenido vacío.
    - Devuelve `None` si no se pudo generar algún lote de forma válida.
    """
    files_generados: List[Dict[str, str]] = []

    for lote in lotes:
        lote_set = set(lote)

        lote_contracts = []
        if isinstance(file_contracts, list) and file_contracts:
            lote_set = set(lote)
            lote_contracts = [c for c in file_contracts if isinstance(c, dict) and c.get("path") in lote_set]

        prompt_lote = _build_prompt_lote(spec=spec, lote=lote, file_contracts=lote_contracts)

        lote_files: Optional[List[Dict[str, str]]] = None
        ultimo_raw: str = ""
        errores_lote: List[str] = []

        for intento_lote in range(max(1, intentos)):
            raw = solicitarJSONEstructurado(
                prompt=prompt_lote,
                system=None,
                temperature=0.2,
                max_tokens=2500,
                fase="generacion_codigo",
                provider_hint="gen-code",
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
                if not is_demo_mode():
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
                prompt_lote = _build_prompt_lote_missing(spec=spec, missing=missing)
            else:
                prompt_lote = _build_prompt_lote_fix_errors(
                    spec=spec,
                    lote=lote,
                    errores_lote=errores_lote,
                    ultimo_raw=ultimo_raw,
                )

        if not lote_files:
            # Logging de diagnóstico: necesitamos saber POR QUÉ falla el lote.
            # Importante:
            # - En demo_mode evitamos prints ruidosos.
            # - En modo normal imprimimos datos accionables: lote, errores y un preview del raw.
            if not is_demo_mode():
                print("[DEBUG] No se pudo generar un lote válido.")
                print("[DEBUG] Lote:", list(lote))
                if errores_lote:
                    print("[DEBUG] Errores lote:", errores_lote)

                # Preview del último RAW para ver si el modelo:
                # - no devuelve JSON
                # - trunca la respuesta
                # - devuelve `files` vacío
                try:
                    raw_preview = (ultimo_raw or "").strip()
                    if len(raw_preview) > 1200:
                        raw_preview = raw_preview[:1200] + "\n...[truncated]..."
                    print("[DEBUG] Última respuesta RAW (preview):\n", raw_preview)
                except Exception:
                    pass
            return None

        # Merge estable (centralizado): no sobreescribir contenido no vacío con contenido vacío.
        # Mantiene el mismo comportamiento que el merge manual anterior.
        files_generados = _merge_files_generados(files_generados, lote_files)

    return files_generados


def _validar_y_reparar_final_with_report(
    *,
    spec: dict,
    files_generados: List[Dict[str, str]],
    allowed_paths: Set[str],
    intentos: int,
    file_contracts: Optional[List[dict]] = None,
) -> tuple[bool, dict]:
    """
    Variante reportable de `_validar_y_reparar_final`.

    Devuelve: (ok, report)
    report = {
      "ast_ok": bool,
      "file_contracts_ok": bool,
      "imports_ok": bool,
      "guardrails_ok": bool,
      "errors": [str],
      "warnings": [str],
    }

    Importante:
    - No imprime por stdout.
    - Mantiene el comportamiento (repairs) del flujo actual.
    """
    report = {
        "ast_ok": True,
        "file_contracts_ok": True,
        "imports_ok": True,
        "guardrails_ok": True,
        "errors": [],
        "warnings": [],
    }

    file_contracts = file_contracts or []
    if not isinstance(file_contracts, list):
        file_contracts = []

    # 1) AST global
    if not _validar_proyecto(files_generados):
        report["ast_ok"] = False
        report["errors"].append("AST_INVALID")
        return False, report

    # 1.1) Validación mínima contra FileContracts (post-generación, pre-guardrails)
    fc_violations = _validate_generated_files_against_file_contracts(
        files_generados=files_generados,
        file_contracts=file_contracts,
    )
    if fc_violations:
        report["file_contracts_ok"] = False
        report["errors"].append("FILE_CONTRACTS_VIOLATIONS")
        try:
            for v in fc_violations:
                report["errors"].append(f"[{v.code}] {v.path}: {v.message}")
        except Exception:
            pass

        repair_paths = sorted(list({v.path for v in fc_violations if getattr(v, "path", None)}))
        repair_paths = [p for p in repair_paths if p in allowed_paths]
        if not repair_paths:
            return False, report

        max_fc_repairs = max(2, intentos)
        for _ in range(max_fc_repairs):
            lote_contracts = [
                c
                for c in file_contracts
                if isinstance(c, dict) and (c.get("path") or "").replace("\\", "/") in set(repair_paths)
            ]
            errores = [f"[{v.code}] {v.path}: {v.message}" for v in fc_violations]

            prompt_fix = _build_prompt_lote_fix_errors(
                spec=spec,
                lote=repair_paths,
                errores_lote=errores,
                ultimo_raw=json.dumps(
                    {"files": [f for f in files_generados if f.get("path") in set(repair_paths)]},
                    ensure_ascii=False,
                ),
                file_contracts=lote_contracts,
                file_contract_errors=errores,
            )

            raw = solicitarJSONEstructurado(
                prompt=prompt_fix,
                system=None,
                temperature=0.1,
                max_tokens=2200,
                fase="generacion_codigo_repair_file_contracts",
                provider_hint="gen-code",
            )

            data = extraer_json_tolerante(raw) or {}
            cand = data.get("files")
            if isinstance(cand, list) and cand:
                _aplicar_patch_en_memoria(files_generados, cand)

            if not _validar_proyecto(files_generados):
                report["ast_ok"] = False
                report["errors"].append("AST_INVALID_AFTER_FC_REPAIR")
                continue
            fc_violations = _validate_generated_files_against_file_contracts(
                files_generados=files_generados,
                file_contracts=file_contracts,
            )
            if not fc_violations:
                report["file_contracts_ok"] = True
                break

        if fc_violations:
            report["file_contracts_ok"] = False
            report["errors"].append("FILE_CONTRACTS_REPAIR_NOT_CONVERGED")
            return False, report

    # 2) Imports globales vs allowed_paths
    ok_imports, e_imports = _validar_imports_internos(files_generados, allowed_paths)
    if not ok_imports:
        report["imports_ok"] = False
        report["errors"].append("IMPORTS_INVALID")
        try:
            for e in e_imports or []:
                report["errors"].append(str(e))
        except Exception:
            pass

        patched_ok, _repair_paths = aplicar_repair_loop_imports(
            spec=spec,
            files_generados=files_generados,
            allowed_paths=allowed_paths,
            errores_imports=e_imports,
            chat_completion_json=chat_completion_json,
            intentos=intentos,
        )
        if not patched_ok:
            report["errors"].append("IMPORTS_REPAIR_NOT_CONVERGED")
            return False, report

        ok_imports2, e_imports2 = _validar_imports_internos(files_generados, allowed_paths)
        if not ok_imports2:
            report["imports_ok"] = False
            report["errors"].append("IMPORTS_INVALID_AFTER_REPAIR")
            try:
                for e in e_imports2 or []:
                    report["errors"].append(str(e))
            except Exception:
                pass
            return False, report

        report["imports_ok"] = True

    # 3) Guardrails por SPEC (contrato usuario)
    guard = guardrails_por_spec(spec, files_generados)
    if guard.warnings:
        report["warnings"].extend(list(guard.warnings))

    if not guard.ok:
        report["guardrails_ok"] = False
        report["errors"].append("GUARDRAILS_FAILED")
        try:
            for e in guard.errors or []:
                report["errors"].append(str(e))
        except Exception:
            pass

        if not guard.repair_paths:
            return False, report

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
                    report["guardrails_ok"] = True
                    break
                continue
            break

        if not guard.ok:
            report["guardrails_ok"] = False
            report["errors"].append("GUARDRAILS_REPAIR_NOT_CONVERGED")
            return False, report

    report["guardrails_ok"] = True
    return True, report


def _validar_y_reparar_final(
    *,
    spec: dict,
    files_generados: List[Dict[str, str]],
    allowed_paths: Set[str],
    intentos: int,
    file_contracts: Optional[List[dict]] = None,
) -> bool:
    """
    Wrapper legacy para mantener compatibilidad: devuelve solo bool.

    Fuente de verdad: `_validar_y_reparar_final_with_report`.
    """
    ok, _report = _validar_y_reparar_final_with_report(
        spec=spec,
        files_generados=files_generados,
        allowed_paths=allowed_paths,
        intentos=intentos,
        file_contracts=file_contracts,
    )
    return bool(ok)


def _generar_desde_spec_validado(
    *,
    spec: dict,
    contexto_normalizado: dict | None,
    intentos: int,
    files_iniciales: Optional[List[Dict[str, str]]] = None,
    descripcion_global: str = "",
) -> Dict[str, Any]:
    """
    Ejecuta Fase 2 (generación por lotes) + Fase 3 (validación final) dado un SPEC ya válido.

    No genera ni alinea el SPEC: eso es responsabilidad del caller.
    """
    files_plan = _completar_inits_en_files(spec.get("files", []))
    spec["files"] = files_plan
    allowed_paths = set(files_plan)

    # ------------------------------------------------------------
    # Contract-first: derive explicit FileContracts from SPEC (deterministic)
    # ------------------------------------------------------------
    file_contracts = _build_file_contracts_from_spec(spec)
    file_contracts_dict = _file_contracts_to_dict(file_contracts)

    # Persist debug artifact
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "file_contracts.json").write_text(
            json.dumps(file_contracts_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    # mantener la misma compilación/sanitización de restricciones
    spec["restrictions"] = compilar_restricciones(spec, contexto_normalizado, intentos=intentos)

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
            file_contracts=file_contracts_dict,
        ):
            # Importante: si el SPEC es válido pero la fase final no converge, devolvemos el SPEC
            # para permitir reintentos aguas arriba (orquestador) reutilizando la “fuente de verdad”.
            return {"files": [], "spec": spec}

        return {"files": files_generados, "spec": spec}

    # ------------------------------------------------------------
    # Estrategia principal: generación robusta POR ARCHIVO (FileContract)
    # ------------------------------------------------------------
    files_generados = _generar_archivos_por_file_contracts(
        spec=spec,
        file_contracts=file_contracts_dict,
        intentos=intentos,
        descripcion_global=descripcion_global,
        contexto_normalizado=contexto_normalizado,
    )

    if not files_generados:
        return {
            "files": [],
            "spec": spec,
            "codegen_status": "failed",
            "materializable": False,
            "codegen_errors": ["file_contract_generation_failed"],
            "validation_report": None,
        }

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

    ok_final, report = _validar_y_reparar_final_with_report(
        spec=spec,
        files_generados=files_generados,
        allowed_paths=allowed_paths,
        intentos=intentos,
        file_contracts=file_contracts_dict,
    )

    # persist report para diagnóstico (siempre, best-effort)
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "final_validation_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    if not ok_final:
        if not is_demo_mode():
            print("[DEBUG] Fase final de validación/repair no convergió (pero hay candidatos).")

        return {
            "files": files_generados,
            "spec": spec,
            "codegen_status": "generated_with_errors",
            "materializable": True,
            "codegen_errors": ["final_validation_failed"],
            "validation_report": report,
        }

    return {
        "files": files_generados,
        "spec": spec,
        "codegen_status": "valid",
        "materializable": True,
        "codegen_errors": [],
        "validation_report": report,
    }


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

    return _generar_desde_spec_validado(
        spec=spec,
        contexto_normalizado=contexto_normalizado,
        intentos=intentos,
        files_iniciales=files_iniciales,
        descripcion_global=descripcion_global,
    )


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
    # FASE 1: SPEC (nuevo flujo: determinista desde IR)
    # -------------------------
    # Fuente de verdad:
    # - contexto_normalizado (si existe, ya viene normalizado)
    # - builder determinista desde IR
    #
    # Política:
    # - Por defecto: construir SPEC determinista desde IR.
    # - Fallback: solo si el builder fallase (bug) o si un feature flag fuerza legacy.
    from poc_it.generador.request_ir import build_request_ir_from_context
    from poc_it.generador.spec_builder import build_spec_from_request_ir

    spec: Optional[dict] = None
    errores_spec: List[str] = []

    force_legacy = False

    def _dump_debug_json(filename: str, payload: Any) -> None:
        try:
            debug_dir = Path("output/_debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / filename).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _serialize_validation_errors(errors: List[_SpecValidationError]) -> List[dict]:
        return [e.__dict__ for e in errors]

    def _serialize_traceability_errors(errors: List[_TraceabilityError]) -> List[dict]:
        return [e.__dict__ for e in errors]

    try:
        # DEBUG #1: input EXACTO a RequestIR
        _dump_debug_json("request_ir_input_context.json", contexto_normalizado)

        req_ir = build_request_ir_from_context(contexto_normalizado, descripcion_global=descripcion_global)

        # DEBUG #2: RequestIR como dict
        try:
            from poc_it.generador.request_ir import request_ir_to_dict

            _dump_debug_json("request_ir.json", request_ir_to_dict(req_ir))
        except Exception:
            pass

        # Checks defensivos: si el contexto traía señal estructurada, no aceptamos degradación silenciosa
        if isinstance(contexto_normalizado, dict):
            if (contexto_normalizado.get("contratos_api_propuestos") or []) and not req_ir.proposed_api_contracts:
                raise ValueError(
                    "RequestIR vacío: contexto_normalizado contenía contratos_api_propuestos pero RequestIR.proposed_api_contracts quedó vacío. "
                    "Esto indica pérdida de contexto o parseo incorrecto. Abortando para evitar degradación a /health."
                )
            pctx = contexto_normalizado.get("persistence")
            if isinstance(pctx, dict) and bool(pctx.get("required")) and not req_ir.persistence.required:
                raise ValueError(
                    "RequestIR inconsistente: contexto_normalizado.persistence.required=true pero RequestIR.persistence.required=false. "
                    "Abortando para evitar degradación a SPEC sin persistencia."
                )

        spec = build_spec_from_request_ir(req_ir)

        traceability_errors = _validate_request_ir_to_spec_traceability(req_ir, spec)
        if traceability_errors:
            _dump_debug_json(
                "spec_traceability_errors.json",
                _serialize_traceability_errors(traceability_errors),
            )
            raise ValueError(
                "Traceability validation failed: "
                + "; ".join(error.code for error in traceability_errors)
            )

        # DEBUG #3: SPEC base determinista
        _dump_debug_json("spec_base.json", spec)
    except Exception as e:
        errores_spec = [f"spec_determinista_error: {e}"]
        raise RuntimeError(f"No se pudo construir SPEC determinista: {e}") from e

    # ------------------------------------------------------------
    # Validación fuerte del SPEC (nuevo flujo determinista)
    # ------------------------------------------------------------
    if isinstance(spec, dict):
        # Persistimos siempre el spec inicial del builder para poder comparar repair vs base.
        _persistir_spec_debug(
            nombre_archivo=f"spec_base_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            spec=spec,
            descripcion_global=descripcion_global,
            contexto_normalizado=contexto_normalizado,
        )

        errors0 = _validate_spec(spec)
        fatals0 = [e for e in errors0 if e.severity == "fatal"]
        warns0 = [e for e in errors0 if e.severity == "warning"]

        if fatals0:
            # Attempt deterministic repair if there are fatals (repairable by design).
            spec_repaired, repair_changes = _repair_spec_deterministic(spec)

            _persistir_spec_debug(
                nombre_archivo=f"spec_repaired_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                spec=spec_repaired,
                descripcion_global=descripcion_global,
                contexto_normalizado=contexto_normalizado,
            )
            _dump_debug_json(
                "spec_repair_changes.json",
                _serialize_validation_errors(repair_changes),
            )

            errors1 = _validate_spec(spec_repaired)
            fatals1 = [e for e in errors1 if e.severity == "fatal"]
            warns1 = [e for e in errors1 if e.severity == "warning"]

            if fatals1:
                # Persist fatal errors and stop: do NOT continue to code generation.
                _dump_debug_json(
                    "spec_validation_errors.json",
                    _serialize_validation_errors(errors1),
                )
                spec_repaired["status"] = "degraded"
                return {"files": [], "spec": spec_repaired, "spec_errors": _serialize_validation_errors(errors1)}

            # no fatals after repair: keep repaired spec
            spec = spec_repaired
            if warns1:
                _dump_debug_json(
                    "spec_validation_warnings.json",
                    _serialize_validation_errors(warns1),
                )
            spec["status"] = "valid"
        else:
            if warns0:
                _dump_debug_json(
                    "spec_validation_warnings.json",
                    _serialize_validation_errors(warns0),
                )
            spec["status"] = "valid"

    return _generar_desde_spec_validado(
        spec=spec,
        contexto_normalizado=contexto_normalizado,
        intentos=intentos,
        descripcion_global=descripcion_global,
    )
