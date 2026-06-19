from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from poc_it.generador_tests_unitarios_hermetic import (
    generar_tests_hermeticos_parcial,
    generar_tests_spec_no_parcial,
    generar_tests_unitarios_minimos,
)
from poc_it.materializacion.materializador_archivos import materializar_proyecto
from poc_it.orquestacion.contract_test_renderer import render_tests_from_test_plan
from poc_it.orquestacion.stub_gen_llm import generate_conftest_with_llm
from poc_it.orquestacion.test_plan import build_test_plan, persist_test_plan
from poc_it.orquestacion.tests_harness import render_conftest_py
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH

logger = logging.getLogger(__name__)


def _normalizar_reqs(lines: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for ln in lines:
        s = (ln or "").strip()
        if not s or s.startswith("#"):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _asegurar_requirements_dev(estructura: Dict[str, str], resultado: Dict[str, Any]) -> None:
    """Asegura un requirements-dev.txt coherente cuando se han generado tests."""
    spec = resultado.get("spec") if isinstance(resultado, dict) else None
    dev_deps = []
    if isinstance(spec, dict):
        dd = spec.get("dev_dependencies")
        if isinstance(dd, list):
            dev_deps = [str(x) for x in dd if str(x).strip()]

    if not dev_deps:
        # deps base de testing. Nota: pytest-json-report se usa por el repair loop
        # para capturar resultados estructurados (sin parsear stdout).
        dev_deps = ["pytest", "pytest-mock", "httpx", "pytest-json-report"]

    # Invariante: el repair loop requiere pytest-json-report. Aseguramos que siempre esté.
    dev_deps.append("pytest-json-report")
    dev_deps = _normalizar_reqs(dev_deps)

    existing = (estructura.get("requirements-dev.txt") or "").strip()
    if not existing:
        estructura["requirements-dev.txt"] = "\n".join(dev_deps) + "\n"


def _is_parcial(resultado: Dict[str, Any], modo_generacion: str | None = None) -> bool:
    if modo_generacion and str(modo_generacion).upper() == "PARCIAL":
        return True
    spec = resultado.get("spec") if isinstance(resultado, dict) else None
    if isinstance(spec, dict):
        modo = str(spec.get("modo") or spec.get("mode") or "").upper()
        if modo == "PARCIAL":
            return True
    modo2 = str((resultado or {}).get("modo") or (resultado or {}).get("mode") or "").upper()
    return modo2 == "PARCIAL"


def _patch_requirements_for_collection(estructura: Dict[str, str]) -> Dict[str, str]:
    """Asegura deps mínimas para que `import app.main` funcione en collection."""
    patched: Dict[str, str] = {}
    req_dev = estructura.get("requirements-dev.txt") or ""
    req = estructura.get("requirements.txt") or ""

    def ensure_line(content: str, line: str) -> str:
        line = line.strip()
        if not line:
            return content
        existing = {ln.strip().lower() for ln in (content or "").splitlines() if ln.strip() and not ln.strip().startswith("#")}
        if line.lower() in existing:
            return content
        out = (content or "").rstrip("\n")
        if out:
            out += "\n"
        out += line + "\n"
        return out

    # pydantic-settings: import común en PoCs con config/env
    req_dev2 = ensure_line(req_dev, "pydantic-settings")
    if req_dev2 != req_dev:
        patched["requirements-dev.txt"] = req_dev2

    if "requirements-dev.txt" not in estructura and "requirements-dev.txt" not in patched:
        req2 = ensure_line(req, "pydantic-settings")
        if req2 != req:
            patched["requirements.txt"] = req2

    return patched


def _hermetic_suite_from_llm(nombre_proyecto: str, resultado: Dict[str, Any], estructura_generada: Dict[str, str]) -> Dict[str, str]:
    """Genera suite hermética (PARCIAL) usando LLM, restringida por runtime_facts/runtime_contracts.

    Nota importante:
    - `tests/conftest.py` se genera de forma determinista por el sistema (ver tests_harness.py).
      El LLM solo debe generar tests (p. ej. test_endpoints_hermetic.py, test_openapi.py).
    """
    import json as _json
    import os as _os

    spec_dict = resultado.get("spec") if isinstance(resultado, dict) else None
    spec_dict = spec_dict if isinstance(spec_dict, dict) else None

    # Solo pasamos al LLM los artefactos necesarios + un bundle de código real (dinámico) para evitar alucinaciones.
    # Estrategia pragmática:
    # - incluir SIEMPRE runtime_{facts,contracts} y app/main.py
    # - incluir SOLO los módulos mencionados por runtime_contracts.endpoints[*].module_path
    # - incluir además los módulos importados por esos endpoints (imports internos app.*) via regex
    minimal_structure: Dict[str, str] = {}
    for k in (
        "app/main.py",
        "app/__init__.py",
        ".poc_it/runtime_facts.json",
        ".poc_it/runtime_contracts.json",
    ):
        if k in estructura_generada:
            minimal_structure[k] = estructura_generada.get(k) or ""

    # Bundle dinámico: módulos por endpoint (source of truth)
    try:
        rc_raw0 = estructura_generada.get(RUNTIME_CONTRACTS_PATH) or ""
        rc0 = _json.loads(rc_raw0) if isinstance(rc_raw0, str) and rc_raw0.strip() else {}
        eps0 = (rc0.get("endpoints") or []) if isinstance(rc0, dict) else []
        for ep in eps0:
            if not isinstance(ep, dict):
                continue
            mp = str(ep.get("module_path") or "").replace("\\", "/").strip()
            if mp and mp in estructura_generada:
                minimal_structure[mp] = estructura_generada.get(mp) or ""
    except Exception:
        pass

    # Bundle dinámico: follow imports internos `from app...` / `import app...` en los módulos ya incluidos.
    try:
        import re as _re

        def _extract_app_imports(py: str) -> List[str]:
            out: List[str] = []
            for m in _re.finditer(r"^\\s*from\\s+(app(?:\\.[a-zA-Z0-9_]+)+)\\s+import\\s+([a-zA-Z0-9_,\\s]+)", py, flags=_re.M):
                mod = m.group(1)
                names = [x.strip() for x in (m.group(2) or "").split(",") if x.strip()]
                # si importan un módulo, añadimos mod; si importan símbolos, añadimos mod (best-effort)
                if mod:
                    out.append(mod)
                for nm in names:
                    # potencial submódulo (raro): app.x.y import z
                    if nm and nm[0].islower():
                        out.append(f"{mod}.{nm}")
            for m in _re.finditer(r"^\\s*import\\s+(app(?:\\.[a-zA-Z0-9_]+)+)", py, flags=_re.M):
                out.append(m.group(1))
            return out

        def _mod_to_path(mod: str) -> str:
            return mod.replace(".", "/") + ".py"

        # iteramos hasta fixpoint pero con límite para evitar loops
        for _ in range(3):
            added = 0
            current_items = list(minimal_structure.items())
            for path, content in current_items:
                if not path.startswith("app/") or not path.endswith(".py"):
                    continue
                for mod in _extract_app_imports(content or ""):
                    pth = _mod_to_path(mod)
                    if pth in estructura_generada and pth not in minimal_structure:
                        minimal_structure[pth] = estructura_generada.get(pth) or ""
                        added += 1
            if added == 0:
                break
    except Exception:
        pass

    # Además añadimos los módulos de endpoints detectados en runtime_contracts (si existen) para que el LLM
    # pueda alinear rutas/Depends sin inventar.
    #
    # NUEVO: filtrado determinista de endpoints herméticos. Solo pasamos al LLM endpoints con depends_imports
    # no vacío y overrideables. Los demás deben degradar a OpenAPI-only.
    try:
        from poc_it.orquestacion.endpoint_filter import bucket_endpoints_for_tests

        rc_raw = estructura_generada.get(RUNTIME_CONTRACTS_PATH) or ""
        rc = _json.loads(rc_raw) if isinstance(rc_raw, str) and rc_raw.strip() else {}
        buckets = bucket_endpoints_for_tests(rc if isinstance(rc, dict) else None)

        # Reducimos runtime_contracts a SOLO los endpoints ejecutables herméticos si hay señal suficiente.
        # Si buckets.hermetic_callable está vacío (probe pobre / clasificación fallida), degradamos a usar
        # rc["endpoints"] completos para no quedarnos sin tests por endpoint. En ese caso, el prompt del LLM
        # debe exigir hermeticidad igualmente (conftest determinista + overrides), pero aceptará degradación
        # contract-lite en endpoints no stubbeables.
        selected_eps = buckets.hermetic_callable or (rc.get("endpoints") or [])
        if isinstance(rc, dict):
            rc2 = dict(rc)
            rc2["endpoints"] = selected_eps
            minimal_structure[".poc_it/runtime_contracts.json"] = _json.dumps(rc2, ensure_ascii=False, indent=2)

        # Aun así añadimos módulos de endpoints (solo de los seleccionados) para minimizar el prompt.
        for ep in selected_eps:
            if isinstance(ep, dict):
                mp = str(ep.get("module_path") or "").strip()
                if mp and mp in estructura_generada:
                    minimal_structure[mp] = estructura_generada.get(mp) or ""
    except Exception:
        pass

    tests_result = generar_tests_unitarios_minimos(
        nombre_proyecto=nombre_proyecto,
        spec=spec_dict,
        estructura_generada=minimal_structure,
        intentos=2,
        modo="PARCIAL",
    )

    # Si LLM falla, caemos a suite mínima (smoke+openapi) desde el propio generador.
    # Nota: el fallback en generar_tests_unitarios_minimos ya devuelve smoke.
    if not tests_result.estructura_tests:
        return {}

    # Política PARCIAL (conftest):
    # 1) Siempre generar un fallback determinista (render_conftest_py).
    # 2) Si existe stub_signatures, priorizar SIEMPRE conftest via LLM (con verificación).
    #    - Si falla por timeout/429 o excepción: reintentar 1 vez con backoff.
    #    - Si falla verificación: guardar el conftest del LLM como debug y caer al determinista.
    try:
        import json as _json
        import os as _os
        import time as _time

        rc_raw = estructura_generada.get(RUNTIME_CONTRACTS_PATH) or ""
        rc = _json.loads(rc_raw) if isinstance(rc_raw, str) and rc_raw.strip() else {}

        deterministic_conftest = render_conftest_py(rc if isinstance(rc, dict) else {})
        tests_result.estructura_tests["tests/conftest.py"] = deterministic_conftest

        ss_raw = estructura_generada.get(".poc_it/stub_signatures.json") or ""
        stub_sigs = _json.loads(ss_raw) if isinstance(ss_raw, str) and ss_raw.strip() else None

        def _dump_debug(name: str, content: str) -> None:
            try:
                dbg_dir = _os.path.join("output", "_debug")
                _os.makedirs(dbg_dir, exist_ok=True)
                ts = _os.getenv("POC_IT_TS") or ""
                # nombre incluye el proyecto para correlación
                fn = f"{name}_{nombre_proyecto}.py"
                with open(_os.path.join(dbg_dir, fn), "w", encoding="utf-8") as f:
                    f.write(content or "")
            except Exception:
                pass

        if isinstance(stub_sigs, dict) and isinstance(rc, dict):
            last = None
            for attempt in range(2):
                try:
                    gen = generate_conftest_with_llm(
                        runtime_contracts=rc,
                        stub_signatures=stub_sigs,
                        estructura_generada=estructura_generada,
                    )
                    last = gen
                    if gen.ok and gen.conftest_py.strip():
                        tests_result.estructura_tests["tests/conftest.py"] = gen.conftest_py
                        break
                    # falló verificación: guardamos debug y no reintentamos (mismo input, mismo verificador)
                    if gen.conftest_py.strip():
                        _dump_debug("conftest_llm_invalid", gen.conftest_py)
                    break
                except Exception as exc:
                    # retry 1 vez: pensado para 429/timeout
                    if attempt == 0:
                        _time.sleep(2.0)
                        continue
                    break
    except Exception:
        # Si todo falla, nos quedamos con el determinista.
        pass

    # Forzamos pytest.ini a recoger solo hermetic suite para evitar residuales.
    # `test_endpoints_spec.py` es legacy (modo no PARCIAL) y no debe ejecutarse en suite hermética.
    pytest_ini = (
        "[pytest]\n"
        "addopts = -q\n"
        "testpaths = tests\n"
        "python_files = test_smoke_import.py test_openapi.py test_endpoints_hermetic.py\n"
        "asyncio_mode = auto\n"
        "markers =\n"
        "    hermetic\n"
        "    openapi\n"
        "    smoke\n"
    )
    tests_result.estructura_tests["pytest.ini"] = pytest_ini

    # Seguridad: si el LLM generó conftest, lo ignoramos a favor del determinista.
    # (render_conftest_py ya lo habrá establecido si pudo)
    return tests_result.estructura_tests


def _build_test_validation_report(
    *,
    nombre_proyecto: str,
    plan: Any,
    runtime_contracts: Optional[dict],
    tests_patch: Dict[str, str],
) -> Dict[str, Any]:
    """
    Artefacto de diagnóstico estable para el usuario:
    - Cuántos endpoints se testean herméticamente vs degradados a OpenAPI-only.
    - Qué overrides están disponibles (allowlist) y si falta get_db/get_settings.
    - Qué required_response_keys/expected_status se están usando por endpoint.
    """
    endpoints = []
    try:
        for e in getattr(plan, "endpoints", []) or []:
            endpoints.append(
                {
                    "path": getattr(e, "path", None),
                    "method": getattr(e, "method", None),
                    "level": getattr(e, "level", None),
                    "reason": getattr(e, "reason", None),
                    "expected_status": getattr(e, "expected_status", None),
                    "allowed_statuses": getattr(e, "allowed_statuses", None),
                    "required_response_keys": getattr(e, "required_response_keys", None),
                    "response_media_type": getattr(e, "response_media_type", None),
                    "hermetic": getattr(e, "hermetic", None),
                }
            )
    except Exception:
        endpoints = []

    allow = []
    try:
        allow = list((runtime_contracts or {}).get("allowed_dependency_overrides") or [])
    except Exception:
        allow = []

    has_db_override = any(str(x).strip().endswith(".get_db") for x in allow)
    has_settings_override = any(str(x).strip().endswith(".get_settings") for x in allow) or any(
        str(x).strip().endswith(".get_config") for x in allow
    )

    totals = {}
    try:
        totals = getattr(plan, "totals", {}) or {}
    except Exception:
        totals = {}

    return {
        "project": nombre_proyecto,
        "strategy": "contract-first",
        "test_plan": {
            "mode": getattr(plan, "mode", None),
            "totals": totals,
            "endpoints": endpoints,
        },
        "overrides": {
            "allowed_dependency_overrides": allow,
            "has_get_db_override": bool(has_db_override),
            "has_get_settings_override": bool(has_settings_override),
        },
        "rendered_tests": {
            "files": sorted(list((tests_patch or {}).keys())),
            "has_conftest": bool("tests/conftest.py" in (tests_patch or {})),
        },
    }


def generar_tests_unitarios(
    nombre_proyecto: str,
    resultado: Dict[str, Any],
    estructura: Dict[str, str],
    archivos_creados: list[str],
    modo_generacion: str | None = None,
) -> bool:
    """Genera y materializa tests unitarios.

    Política nueva (contract-first):
    1) Construir TestPlan determinista desde runtime_contracts (+ hints del probe).
    2) Persistir `.poc_it/test_plan.json`.
    3) Renderizar tests deterministas desde el TestPlan (sin LLM).
    4) Usar LLM solo como fallback opcional para stubs/overrides cuando el harness determinista
       no sea suficiente (mantenemos compat con pipeline actual).

    Nota: el rol del bucle de repair posterior cambia: ya no debe \"arreglar asserts\"
    cuando hay 500, sino degradar endpoints en el plan.
    """
    try:
        estructura_generada = dict(estructura)

        # Cargar runtime_contracts desde disco si no está en estructura (best-effort)
        if RUNTIME_CONTRACTS_PATH not in estructura_generada:
            try:
                import os

                project_dir = os.path.join("output", nombre_proyecto)
                rc_file = os.path.join(project_dir, RUNTIME_CONTRACTS_PATH.replace("/", os.sep))
                if os.path.exists(rc_file):
                    with open(rc_file, encoding="utf-8") as f:
                        estructura_generada[RUNTIME_CONTRACTS_PATH] = f.read()
            except Exception:
                pass

        # Limpieza dura de residuales: evitamos que tests viejos contaminen la suite nueva.
        try:
            import os

            tests_dir = os.path.join("output", nombre_proyecto, "tests")
            if os.path.isdir(tests_dir):
                for fn in os.listdir(tests_dir):
                    if fn.endswith(".py"):
                        try:
                            os.remove(os.path.join(tests_dir, fn))
                        except Exception:
                            pass
        except Exception:
            pass

        # ----------------------------
        # Fase B nueva: TestPlan + render determinista
        # ----------------------------
        spec_dict = resultado.get("spec") if isinstance(resultado, dict) else None
        plan = build_test_plan(
            structure=estructura_generada,
            spec=spec_dict if isinstance(spec_dict, dict) else None,
            mode=str(modo_generacion or (resultado or {}).get("clasificacion") or (resultado or {}).get("modo") or "").strip() or ("PARCIAL" if _is_parcial(resultado, modo_generacion) else "COMPLETO"),
        )
        estructura_generada = persist_test_plan(structure=estructura_generada, plan=plan)

        # materializar el plan dentro del proyecto (artefacto)
        try:
            materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura={".poc_it/test_plan.json": estructura_generada.get(".poc_it/test_plan.json", "")},
                limpiar_directorio=False,
            )
        except Exception:
            pass

        # Render tests deterministas desde el plan
        rc_obj = None
        try:
            import json as _json

            rc_raw = estructura_generada.get(RUNTIME_CONTRACTS_PATH) or ""
            rc_obj = _json.loads(rc_raw) if isinstance(rc_raw, str) and rc_raw.strip() else None
        except Exception:
            rc_obj = None

        tests_patch = render_tests_from_test_plan(
            structure=estructura_generada,
            runtime_contracts=rc_obj if isinstance(rc_obj, dict) else None,
            runtime_facts=None,
        )

        # ----------------------------
        # Artefacto nuevo: test_validation_report.json
        # ----------------------------
        try:
            import json as _json

            report = _build_test_validation_report(
                nombre_proyecto=nombre_proyecto,
                plan=plan,
                runtime_contracts=rc_obj if isinstance(rc_obj, dict) else None,
                tests_patch=tests_patch,
            )
            report_txt = _json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            estructura_generada[".poc_it/test_validation_report.json"] = report_txt
            materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura={".poc_it/test_validation_report.json": report_txt},
                limpiar_directorio=False,
            )
        except Exception:
            # No debe romper generación de tests.
            pass

        # Mantener compat: si el plan marca invocables pero no hay conftest (falló render),
        # aplicamos el conftest determinista legacy como fallback.
        if "tests/conftest.py" not in tests_patch and any(
            e.level in ("HERMETIC_ENDPOINT_CONTRACT", "SEMANTIC_STATEFUL") for e in getattr(plan, "endpoints", [])
        ):
            try:
                tests_patch["tests/conftest.py"] = render_conftest_py(rc_obj if isinstance(rc_obj, dict) else {})
            except Exception:
                pass

        tests_result = type("Tmp", (), {})()
        tests_result.estructura_tests = tests_patch
        tests_result.errores = []

        if tests_result.errores:
            joined = "\n".join([str(e) for e in (tests_result.errores or [])])

            # Invariante: la suite legacy spec NO debe ser requisito en PARCIAL.
            if _is_parcial(resultado, modo_generacion):
                # En PARCIAL, estos warnings pueden aparecer por validaciones legacy internas.
                # No debemos abortar ni caer a fallback mínimo por ello: simplemente ignoramos el warning,
                # porque la suite hermética no genera `test_endpoints_spec.py` a propósito.
                if "endpoints_spec" in joined or "test_endpoints_spec.py" in joined or "SPEC incluye endpoints" in joined:
                    logger.info(
                        "[TESTS] Ignorando warnings legacy de spec-suite en PARCIAL (no aplican): %s",
                        tests_result.errores,
                    )
                else:
                    logger.info("[TESTS] Aviso: generación de tests con warnings: %s", tests_result.errores)
            else:
                # En NO-PARCIAL, el warning sí es señal de inconsistencia.
                if "endpoints_spec" in joined or "test_endpoints_spec.py" in joined or "SPEC incluye endpoints" in joined:
                    raise RuntimeError(
                        "BUG: Se detectaron warnings asociados a spec-suite (legacy) en modo NO PARCIAL.\n"
                        f"Warnings:\n{joined}\n"
                        f"Archivos generados: {sorted(list(tests_result.estructura_tests.keys()))}\n"
                        f"modo_generacion={modo_generacion} _is_parcial={_is_parcial(resultado, modo_generacion)}"
                    )
                logger.info("[TESTS] Aviso: generación de tests con warnings: %s", tests_result.errores)

        if not tests_result.estructura_tests:
            logger.info("[TESTS] Aviso: 0 archivos de tests. Se aplicará fallback mínimo.")
            tests_result = generar_tests_unitarios_minimos(
                nombre_proyecto=nombre_proyecto,
                spec=None,
                estructura_generada={},
                intentos=0,
                modo="PARCIAL" if _is_parcial(resultado, modo_generacion) else None,
            )

        archivos_tests = materializar_proyecto(
            nombre_proyecto=nombre_proyecto,
            estructura=tests_result.estructura_tests,
            limpiar_directorio=False,
        )
        archivos_creados.extend(archivos_tests)
        estructura.update(tests_result.estructura_tests)

        _asegurar_requirements_dev(estructura, resultado)

        req_files_to_materialize: Dict[str, str] = {}
        if "requirements-dev.txt" in estructura:
            req_files_to_materialize["requirements-dev.txt"] = estructura["requirements-dev.txt"]
        if "requirements.txt" in estructura:
            req_files_to_materialize["requirements.txt"] = estructura["requirements.txt"]

        if req_files_to_materialize:
            archivos_req = materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura=req_files_to_materialize,
                limpiar_directorio=False,
            )
            archivos_creados.extend(archivos_req)

        return True

    except Exception as exc:
        logger.info("[TESTS] Error generando/materializando tests: %s. Se aplicará fallback mínimo.", exc)
        try:
            fallback = generar_tests_unitarios_minimos(
                nombre_proyecto=nombre_proyecto,
                spec=None,
                estructura_generada={},
                intentos=0,
                modo="PARCIAL" if _is_parcial(resultado, modo_generacion) else None,
            )
            archivos_tests = materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura=fallback.estructura_tests,
                limpiar_directorio=False,
            )
            archivos_creados.extend(archivos_tests)
            estructura.update(fallback.estructura_tests)
        except Exception as exc2:
            logger.info("[TESTS] Error aplicando fallback mínimo de tests: %s", exc2)
        return True
