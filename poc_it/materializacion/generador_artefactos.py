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
from poc_it.generador.spec_alignment import (
    alinear_spec_con_contexto as _alinear_spec_con_contexto,
)
from poc_it.generador.spec_validation import (
    SpecValidationError as _SpecValidationError,
    completar_inits_en_files as _completar_inits_en_files,
    normalizar_paths as _normalizar_paths,
    persistir_spec_debug as _persistir_spec_debug,
    repair_spec_deterministic as _repair_spec_deterministic,
    validate_spec as _validate_spec,
    validar_spec as _validar_spec,
)
from poc_it.generador.prompts_lotes import (
    build_prompt_lote as _build_prompt_lote,
    build_prompt_lote_fix_errors as _build_prompt_lote_fix_errors,
    build_prompt_lote_missing as _build_prompt_lote_missing,
)
from poc_it.generador.prompts_spec import (
    build_prompt_spec as _build_prompt_spec,
    reparar_spec_desde_spec as _reparar_spec_desde_spec,
    reparar_spec_prompt as _reparar_spec_prompt,
)
from poc_it.generador.prompts_guardrails import (
    build_repair_prompt_por_restriccion as _build_repair_prompt_por_restriccion,
)
from poc_it.generador.validators import (
    validar_imports_internos as _validar_imports_internos,
    validar_paths_generados as _validar_paths_generados,
    validar_proyecto as _validar_proyecto,
)
from poc_it.entrada.demo_progress import demo_progress, is_demo_mode
from poc_it.infraestructura.llm_client import chat_completion_json, solicitarJSONEstructurado


# ==========================================================
# AGRUPACIÓN DE LOTES
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

        prompt_lote = _build_prompt_lote(spec=spec, lote=lote)

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
        if not is_demo_mode():
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


def _generar_desde_spec_validado(
    *,
    spec: dict,
    contexto_normalizado: dict | None,
    intentos: int,
    files_iniciales: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Ejecuta Fase 2 (generación por lotes) + Fase 3 (validación final) dado un SPEC ya válido.

    No genera ni alinea el SPEC: eso es responsabilidad del caller.
    """
    files_plan = _completar_inits_en_files(spec.get("files", []))
    spec["files"] = files_plan
    allowed_paths = set(files_plan)

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
        ):
            # Importante: si el SPEC es válido pero la fase final no converge, devolvemos el SPEC
            # para permitir reintentos aguas arriba (orquestador) reutilizando la “fuente de verdad”.
            return {"files": [], "spec": spec}

        return {"files": files_generados, "spec": spec}

    lotes = _agrupar_lotes(files_plan, spec=spec)
    lotes = sorted(
        lotes,
        key=lambda lote: 1
        if any(p in ("app/main.py", "app/__init__.py") or p.startswith("app/config/") for p in lote)
        else 0,
    )

    files_generados = _generar_archivos_por_lotes(spec=spec, lotes=lotes, intentos=intentos)
    if not files_generados:
        return {"files": []}

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
        if not is_demo_mode():
            print("[DEBUG] Fase final de validación/repair no convergió.")
        # Importante: si el SPEC es válido pero la fase final no converge, devolvemos el SPEC
        # para permitir reintentos aguas arriba (orquestador) reutilizando la “fuente de verdad”.
        return {"files": [], "spec": spec}

    return {"files": files_generados, "spec": spec}


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

    force_legacy = os.getenv("POC_IT_SPEC_LEGACY_LLM", "0").strip() in ("1", "true", "True", "yes", "YES")

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

    if not force_legacy:
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
                        "Esto indica pérdida de contexto o parseo incorrecto. Abortando para evitar fallback silencioso a /health."
                    )
                pctx = contexto_normalizado.get("persistence")
                if isinstance(pctx, dict) and bool(pctx.get("required")) and not req_ir.persistence.required:
                    raise ValueError(
                        "RequestIR inconsistente: contexto_normalizado.persistence.required=true pero RequestIR.persistence.required=false. "
                        "Abortando para evitar degradación a SPEC sin persistencia."
                    )

            spec = build_spec_from_request_ir(req_ir)

            # DEBUG #3: SPEC base determinista (antes de LLM/alineación)
            _dump_debug_json("spec_base.json", spec)
        except Exception as e:
            # No cortamos aquí: permitimos fallback a legacy para mantener ejecutabilidad,
            # pero el objetivo del refactor es que este camino sea raro.
            errores_spec = [f"spec_determinista_error: {e}"]
            spec = None

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

    # ------------------------------------------------------------
    # LEGACY fallback (LLM) - opcional / desaconsejado
    # ------------------------------------------------------------
    if not isinstance(spec, dict) or not spec:
        prompt_spec = _build_prompt_spec(
            descripcion_global=descripcion_global,
            contexto_normalizado=contexto_normalizado,
        )
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
                        _persistir_spec_debug(
                            nombre_archivo=f"spec_ok_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                            spec=spec,
                            descripcion_global=descripcion_global,
                            contexto_normalizado=contexto_normalizado,
                        )
                        break

                    errores_spec = (audit_errors or []) + errores2
                    prompt_spec = _reparar_spec_prompt(
                        prompt_spec_base=prompt_spec_base,
                        raw_resp=resp,
                        errores=errores_spec,
                    )
                    spec = None
                    continue

                errores_spec = audit_errors or ["auditor: no pudo alinear SPEC con contexto"]
                prompt_spec = _reparar_spec_prompt(
                    prompt_spec_base=prompt_spec_base,
                    raw_resp=resp,
                    errores=errores_spec,
                )
                spec = None
                continue

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

    return _generar_desde_spec_validado(
        spec=spec,
        contexto_normalizado=contexto_normalizado,
        intentos=intentos,
    )
