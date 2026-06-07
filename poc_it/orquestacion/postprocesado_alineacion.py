from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from datetime import datetime
from typing import Any, Dict

from poc_it.materializador_archivos import materializar_proyecto
from poc_it.orquestacion.pytest_fixers import apply_first_matching_fixer
from poc_it.orquestacion.tests_sanitizer import sanitize_tests
from poc_it.postprocesador_alineacion import AlignmentIssue, postprocesar_alineacion_llm
from poc_it.runtime_contracts import load_runtime_contracts_from_structure

logger = logging.getLogger(__name__)


_SIG_HEX_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_SIG_ABS_PATH_RE = re.compile(r"[A-Za-z]:\\\\[^\\s]+")
_SIG_OBJ_AT_RE = re.compile(r"object at 0x[0-9a-fA-F]+", re.IGNORECASE)


def _pytest_signature(out: str, max_len: int = 1400) -> str:
    """Firma normalizada para detectar estancamiento sin depender de ruido (paths, direcciones, etc.)."""
    if not out:
        return ""
    s = out
    s = _SIG_OBJ_AT_RE.sub("object at <addr>", s)
    s = _SIG_HEX_ADDR_RE.sub("<addr>", s)
    s = _SIG_ABS_PATH_RE.sub("<path>", s)
    # Compactar whitespace y limitar tamaño.
    s = "\n".join(ln.rstrip() for ln in s.splitlines() if ln.strip())
    return s[:max_len]


def _is_code_like_pytest_failure(out: str) -> bool:
    """Heurística: detectar fallos de pytest que *casi seguro* requieren patch de código (no tests).

    Importante: en modo PARCIAL/“hermético” es muy común que los fallos aparezcan como
    AttributeError/TypeError dentro de app/**, pero su causa real sea un override/stub de tests
    incompatible (p.ej. dict-as-db-session). En esos casos, tratarlo como “code-like” hace que el LLM
    toque app/** cuando lo correcto es reparar tests/harness.

    Política:
    - primero descartamos patrones típicos de fallo por stubs/overrides de tests (tests-like)
    - después aplicamos señales fuertes de fallo de código
    """
    if not out:
        return False

    # 1) Señales fuertes de “tests-like”: overrides/stubs incompatibles.
    # (estos patrones suelen manifestarse como AttributeError en app/**, pero se solucionan en tests)
    tests_like_needles = [
        "TestClient.get() got an unexpected keyword argument 'json'",
        "AttributeError: 'dict' object has no attribute 'add'",
        "AttributeError: 'dict' object has no attribute 'execute'",
        "AttributeError: 'dict' object has no attribute 'delete'",
        "AttributeError: 'dict' object has no attribute 'commit'",
        "AttributeError: 'dict' object has no attribute 'refresh'",
        "AttributeError: 'MockService' object has no attribute",
    ]
    if any(n in out for n in tests_like_needles):
        return False

    # Caso especial: fallo de Settings por env vars ausentes -> tratar como tests-like en hermetic.
    if "ValidationError: 1 validation error for Settings" in out and "DATABASE_URL" in out:
        return False

    # 2) Señales de fallo de código (conservador)
    needles = [
        "NameError:",
        "SyntaxError:",
        "IndentationError:",
        "fastapi.exceptions.ResponseValidationError",
    ]
    if any(n in out for n in needles):
        return True

    # Import/ModuleNotFoundError puede ser dependencia missing (fixer) o bug de import.
    # Lo tratamos como code-like (reparación puede tocar requirements o imports), no como tests-only.
    if "ImportError:" in out or "ModuleNotFoundError:" in out:
        return True

    # AttributeError/TypeError: sólo code-like si no parece venir de stubs/overrides.
    if "AttributeError:" in out or "TypeError:" in out:
        return True

    # Errores de DB reales (sin hermeticidad) son code/infra-like.
    needles_db = ["sqlalchemy.exc.", "psycopg2.", "OperationalError", "ProgrammingError"]
    if any(n in out for n in needles_db):
        return True

    return False


def _append_post_debug_event(
    *,
    project_dir: str,
    attempt: int,
    max_repairs: int,
    hermetic: bool,
    repair_goal: str,
    pytest_output: str,
    patched_files: Dict[str, str],
    llm_raw: str,
) -> None:
    """Persistir artefacto de debug por iteración de postprocesado.
    No debe romper el flujo: best-effort.
    """
    try:
        debug_dir = os.path.join(project_dir, "_debug")
        os.makedirs(debug_dir, exist_ok=True)

        event = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "attempt": attempt,
            "max_repairs": max_repairs,
            "hermetic": hermetic,
            "repair_goal": repair_goal,
            "pytest_signature": (pytest_output or "").strip()[:1200],
            "pytest_output": (pytest_output or "")[:12000],
            "patched_paths": sorted(list((patched_files or {}).keys())),
        }

        fname = os.path.join(debug_dir, f"postprocess_pytest_attempt_{attempt:02d}.json")
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(event, f, ensure_ascii=False, indent=2)

        if llm_raw:
            with open(
                os.path.join(debug_dir, f"postprocess_pytest_attempt_{attempt:02d}_llm_raw.json"),
                "w",
                encoding="utf-8",
            ) as f:
                # llm_raw suele ser JSON string; lo guardamos tal cual para auditoría
                f.write(llm_raw)

        if patched_files:
            with open(
                os.path.join(debug_dir, f"postprocess_pytest_attempt_{attempt:02d}_patched_files.json"),
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(patched_files, f, ensure_ascii=False, indent=2)
    except Exception:
        # Nunca romper el flujo de generación por logging de debug
        return
    finally:
        return


def postprocesar_alineacion_por_pytest(
    *,
    nombre_proyecto: str,
    project_dir: str,
    resultado: Dict[str, Any],
    estructura: Dict[str, str],
    archivos_creados: list[str],
) -> None:
    """Ejecuta un post-procesado de alineación basado en fallos de pytest.

    Mantiene comportamiento del orquestador:
    - Si `tests/` existe y pytest falla, crea un AlignmentIssue con el output.
    - Lanza hasta `POSTPROCESADO_MAX_REPAIRS` reparaciones usando el LLM.
    - Nunca rompe el flujo: ante excepción, loggea y continúa.
    """
    try:
        # Política: en modo PARCIAL el "canonical loop" es pytest_llm_repair (solo tests, gates+rollback).
        # Este post-procesado (legacy) tiende a duplicar estrategias y empeorar la convergencia.
        #
        # Para PARCIAL:
        # - no ejecutamos este loop salvo que se fuerce explícitamente por env var.
        forced = str(os.getenv("POC_IT_ENABLE_LEGACY_POSTPROCESS_PYTEST", "")).strip() in (
            "1",
            "true",
            "True",
            "yes",
            "YES",
        )

        try:
            contracts0 = load_runtime_contracts_from_structure(estructura)
            gen_mode = str((contracts0 or {}).get("generation_mode") or "").upper() if isinstance(contracts0, dict) else ""
        except Exception:
            try:
                gen_mode = str(getattr(load_runtime_contracts_from_structure(estructura), "generation_mode", "") or "").upper()
            except Exception:
                gen_mode = ""

        if gen_mode == "PARCIAL" and not forced:
            logger.info(
                "[POST] Skip postprocesado_alineacion_por_pytest en PARCIAL (canonical: pytest_llm_repair). "
                "Set POC_IT_ENABLE_LEGACY_POSTPROCESS_PYTEST=1 para forzar."
            )
            return

        # Default: 4 intentos (antes 2). Se puede overridear por env var.
        max_repairs = int(os.getenv("POSTPROCESADO_MAX_REPAIRS", "4"))

        last_signature: str = ""
        stagnant_count: int = 0
        last_llm_patch_paths: str = ""
        last_llm_patch_fingerprint: str = ""
        last_llm_had_patch: bool = False

        for attempt in range(max_repairs + 1):
            issues: list[AlignmentIssue] = []
            out = ""

            try:
                tests_dir = os.path.join(project_dir, "tests")
                if os.path.isdir(tests_dir):
                    p = subprocess.run(
                        ["python", "-m", "pytest", "-q"],
                        cwd=project_dir,
                        capture_output=True,
                        text=True,
                    )
                    if p.returncode != 0:
                        out = (p.stdout or "") + "\n" + (p.stderr or "")

                        contracts = load_runtime_contracts_from_structure(estructura)
                        hermetic = bool(contracts.hermetic) if contracts else False

                        # 1) FIXERS deterministas (antes de LLM): cambios pequeños, observables y baratos.
                        # Nota: no buscamos resolver TODO con fixers; solo eliminar “footguns” recurrentes
                        # (TestClient json en GET, dict-as-db, dependencias faltantes, etc.) para que el LLM
                        # se centre en los casos inciertos.
                        fx = apply_first_matching_fixer(out, estructura)
                        if fx and fx.patched_files:
                            logger.info("[POST] Pytest fixer determinista aplicado: %s", fx.message)

                            materializar_proyecto(
                                nombre_proyecto=nombre_proyecto,
                                estructura=fx.patched_files,
                                limpiar_directorio=False,
                            )
                            estructura.update(fx.patched_files)
                            archivos_creados.extend(
                                [os.path.join(project_dir, p.replace("/", os.sep)) for p in fx.patched_files.keys()]
                            )

                            sr = sanitize_tests(estructura=estructura)
                            if sr.patched_files:
                                materializar_proyecto(
                                    nombre_proyecto=nombre_proyecto,
                                    estructura=sr.patched_files,
                                    limpiar_directorio=False,
                                )
                                estructura.update(sr.patched_files)
                                archivos_creados.extend(
                                    [os.path.join(project_dir, p.replace("/", os.sep)) for p in sr.patched_files.keys()]
                                )

                            try:
                                c = subprocess.run(
                                    ["python", "-m", "compileall", "-q", "tests"],
                                    cwd=project_dir,
                                    capture_output=True,
                                    text=True,
                                )
                                if c.returncode != 0:
                                    logger.info("[POST] compileall falló tras fixer; se corta para evitar loops.")
                                    break
                            except Exception:
                                pass

                            # No consumimos LLM; re-ejecutar pytest en siguiente iteración.
                            continue

                        # 2) Circuit breaker con firma normalizada + estancamiento.
                        signature = _pytest_signature(out)
                        if signature and signature == last_signature:
                            stagnant_count += 1
                        else:
                            stagnant_count = 0
                        last_signature = signature

                        # Si hay estancamiento repetido, no abortamos inmediatamente:
                        # - aún permitimos a LLM “desatascar” una vez
                        # - pero evitamos loops infinitos si el modelo devuelve vacío/no-op.
                        if stagnant_count >= 2:
                            logger.info(
                                "[POST] Pytest sigue fallando con firma estable (x%s); se corta post-procesado para evitar loop.",
                                stagnant_count + 1,
                            )
                            break

                        issues.append(
                            AlignmentIssue(
                                code="PYTEST_FAILURE",
                                severity="error",
                                file="tests",
                                message="Errores residuales: pytest falla; alinear tests/handlers/modelos.",
                                hint=out[:8000],
                            )
                        )
            except Exception as exc:
                logger.info("[POST] Aviso: pytest no ejecutable: %s", exc)

            if not issues:
                break

            if attempt >= max_repairs:
                logger.info("[POST] Reparación por pytest agotada; se continúa sin bloquear.")
                break

            logger.info(
                "[POST] Pytest falló; ejecutando post-procesado (attempt %s/%s)",
                attempt + 1,
                max_repairs,
            )

            spec_dict = resultado.get("spec") if isinstance(resultado, dict) else None
            contracts = load_runtime_contracts_from_structure(estructura)
            hermetic = bool(contracts.hermetic) if contracts else False

            code_like = _is_code_like_pytest_failure(out)

            # En modo hermético:
            # - si code-like: permitimos LLM reparar CÓDIGO (evita PoCs rotas por bugs triviales).
            # - si test-like: LLM solo toca TESTS, buscando hermeticidad (sin DB/red).
            if hermetic and code_like:
                logger.info("[POST] runtime_contracts.hermetic=True y fallo code-like; se permite reparación LLM de código.")
            if hermetic and not code_like:
                logger.info(
                    "[POST] runtime_contracts.hermetic=True y fallo test-like; se permite reparación LLM de tests (scope acotado)."
                )

            # Estrategia por intento: 4 intentos “diferentes por diseño”.
            # No forzamos determinísticamente; esto sirve como hint (los fixers ya corren antes).
            # - attempt=0: bootstrapping/hermeticidad
            # - attempt=1: stubs/statefulness
            # - attempt=2: contract alignment
            # - attempt=3: cleanup/final
            strategy_hint = {
                0: "BOOTSTRAP_HERMETIC",
                1: "STATEFUL_DOUBLES",
                2: "CONTRACT_ALIGNMENT",
                3: "CLEANUP_FINAL",
            }.get(attempt, "GENERAL")

            repair_goal = "code" if code_like else "tests"
            pp = postprocesar_alineacion_llm(
                estructura=estructura,
                spec=spec_dict if isinstance(spec_dict, dict) else None,
                issues=issues,
                runtime_contracts=contracts.to_dict() if contracts else None,
                max_files=6,
                repair_goal=repair_goal,
                strategy_hint=strategy_hint,
            )
            _append_post_debug_event(
                project_dir=project_dir,
                attempt=attempt + 1,
                max_repairs=max_repairs,
                hermetic=hermetic,
                repair_goal=repair_goal,
                pytest_output=out,
                patched_files=pp.patched_files,
                llm_raw=pp.llm_raw,
            )

            if not pp.patched_files:
                # Si el modelo devuelve vacío, lo tratamos como señal de estancamiento:
                # - reintentamos, pero no indefinidamente
                # - si ya devolvió vacío en el intento anterior para una firma similar, cortamos
                if last_llm_had_patch is False and stagnant_count >= 1:
                    logger.info("[POST] LLM devolvió patch vacío en estancamiento; se corta para evitar loop.")
                    break
                last_llm_had_patch = False
                logger.info("[POST] El modelo no devolvió patch; se reintenta mientras pytest siga fallando.")
                continue

            # Guard: evitar repetir el mismo patch en estancamiento.
            #
            # Importante: antes comparábamos solo "paths" y eso cortaba demasiado pronto:
            # es normal necesitar 2+ iteraciones sobre el mismo archivo para converger.
            # Ahora cortamos solo si el CONTENIDO del patch es idéntico (fingerprint).
            patch_paths = ",".join(sorted(pp.patched_files.keys()))
            h = hashlib.sha256()
            for p in sorted(pp.patched_files.keys()):
                h.update(p.encode("utf-8", errors="ignore"))
                h.update(b"\n")
                h.update((pp.patched_files[p] or "").encode("utf-8", errors="ignore"))
                h.update(b"\n---\n")
            patch_fingerprint = h.hexdigest()

            if (
                patch_fingerprint
                and patch_fingerprint == last_llm_patch_fingerprint
                and stagnant_count >= 1
            ):
                logger.info("[POST] LLM repite patch idéntico en estancamiento; se corta.")
                break

            # Seguimos guardando paths solo para debug/telemetría.
            last_llm_patch_paths = patch_paths
            last_llm_patch_fingerprint = patch_fingerprint
            last_llm_had_patch = True

            materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura=pp.patched_files,
                limpiar_directorio=False,
            )
            estructura.update(pp.patched_files)
            archivos_creados.extend(
                [os.path.join(project_dir, p.replace("/", os.sep)) for p in pp.patched_files.keys()]
            )

            # Sanitizar tests tras patch del LLM (si aplica).
            sr = sanitize_tests(estructura=estructura)
            if sr.patched_files:
                materializar_proyecto(
                    nombre_proyecto=nombre_proyecto,
                    estructura=sr.patched_files,
                    limpiar_directorio=False,
                )
                estructura.update(sr.patched_files)
                archivos_creados.extend(
                    [os.path.join(project_dir, p.replace("/", os.sep)) for p in sr.patched_files.keys()]
                )

            # Compile gate
            try:
                c = subprocess.run(
                    ["python", "-m", "compileall", "-q", "tests"],
                    cwd=project_dir,
                    capture_output=True,
                    text=True,
                )
                if c.returncode != 0:
                    logger.info("[POST] compileall falló tras patch LLM; se corta para evitar loops.")
                    break
            except Exception as _:
                pass
    except Exception as exc:
        logger.info("[POST] Aviso: post-procesado de alineación falló (se continúa): %s", exc)
