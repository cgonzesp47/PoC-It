from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path
from typing import Any, Dict

import poc_it.orquestacion.pytest_fixers as pytest_fixers
import poc_it.orquestacion.pytest_llm_repair as pytest_llm_repair
import poc_it.orquestacion.reparacion_runtime as reparacion_runtime
from poc_it.modulos.models import ProjectContext
from poc_it.orquestacion.llm_safe_repair import SafeRepairResult
from poc_it.runtime.poc_runtime_environment import PocRuntimeEnvironment


def _fake_ready_runtime_env(project_dir: str) -> PocRuntimeEnvironment:
    return PocRuntimeEnvironment(
        project_dir=Path(project_dir),
        venv_dir=Path(project_dir) / ".fake_venv",
        python_executable=Path(sys.executable),
        runtime_environment_status="ready",
        requirements_hash="deadbeef",
        venv_created=False,
        venv_recreated=False,
        venv_healthcheck_ok=True,
        install_attempted=False,
        install_exit_code=0,
        install_stdout_summary="",
        install_stderr_summary="",
        detail="fake ready env for test",
    )


# ---------------------------------------------------------------------------
# 6) No existe fallback module_name -> package con el mismo nombre.
# ---------------------------------------------------------------------------


def _code_lines(source: str) -> list[str]:
    """Líneas de código real, excluyendo comentarios de una línea (para no falsos-positivos con
    comentarios explicativos que citan literalmente el patrón prohibido como ejemplo negativo)."""
    return [line for line in source.splitlines() if not line.strip().startswith("#")]


def test_no_module_to_package_guessing_helpers_remain_in_reparacion_runtime() -> None:
    assert not hasattr(reparacion_runtime, "_requirements_contiene_paquete")
    assert not hasattr(reparacion_runtime, "_append_requirement")

    code_source = "\n".join(_code_lines(inspect.getsource(reparacion_runtime)))
    assert not re.search(r"\bpypi_pkg\s*=", code_source)


def test_extraer_modulo_faltante_only_extracts_does_not_guess_package() -> None:
    detail = "ModuleNotFoundError: No module named 'pydantic_settings'"
    assert reparacion_runtime._extraer_modulo_faltante(detail) == "pydantic_settings"
    assert reparacion_runtime._extraer_modulo_faltante("no traceback here") is None


# ---------------------------------------------------------------------------
# 7) Un ModuleNotFoundError persistente en runtime no muta requirements.txt/spec.
# ---------------------------------------------------------------------------


def test_persistent_module_not_found_error_never_mutates_requirements_or_spec(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        reparacion_runtime,
        "prepare_poc_runtime_environment",
        lambda project_dir: _fake_ready_runtime_env(project_dir),
    )

    persistent_detail = (
        "[runtime_verify] import app.main failed:\n"
        "Traceback ...\n"
        "ModuleNotFoundError: No module named 'google'\n"
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "runtime_verify_fastapi_project",
        lambda project_dir, spec, python_executable=None: (False, persistent_detail),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "run_llm_safe_repair",
        lambda **kwargs: SafeRepairResult(ok=False, patch={}, logs=[], allowlist=[]),
    )

    def fake_materializar(*, nombre_proyecto, estructura, limpiar_directorio=True, **kwargs):
        return list(estructura.keys())

    monkeypatch.setattr(reparacion_runtime, "materializar_proyecto", fake_materializar)
    monkeypatch.setattr(pytest_llm_repair, "materializar_proyecto", fake_materializar)

    original_requirements = "fastapi\nuvicorn\n"
    estructura: Dict[str, str] = {"requirements.txt": original_requirements}
    resultado: Dict[str, Any] = {"spec": {"dependencies": ["fastapi", "uvicorn"]}}
    archivos_creados: list[str] = []

    project_dir = str(tmp_path / "some_generated_poc")

    reparacion_runtime.ejecutar_reparacion_runtime(
        nombre_proyecto="some_generated_poc_test",
        descripcion_global="x",
        project_dir=project_dir,
        context=ProjectContext(),
        resultado=resultado,
        estructura=estructura,
        archivos_creados=archivos_creados,
        regenerar_tests=False,
        max_runtime_repairs=1,
    )

    # requirements.txt NO debe haberse "reparado" adivinando el paquete PyPI para el módulo
    # faltante ('google' != 'google-api-python-client').
    assert estructura["requirements.txt"] == original_requirements
    assert "google" not in estructura["requirements.txt"]

    # spec.dependencies tampoco debe haberse mutado silenciosamente a partir del traceback.
    assert resultado["spec"]["dependencies"] == ["fastapi", "uvicorn"]

    assert resultado.get("runtime_ok") is False


# ---------------------------------------------------------------------------
# 9) La Fase B (generación de tests) ya NO se salta cuando code_ok=False.
# ---------------------------------------------------------------------------


def test_test_generation_runs_even_when_code_never_passes_runtime_verify(monkeypatch, tmp_path) -> None:
    """Regresión: antes, si el código nunca superaba import-time/wiring/probe tras agotar las
    reparaciones automáticas (code_ok=False), la Fase B (generación de tests) se saltaba por
    completo ("Skip tests: el código no es importable/wireable..."). Esto dejaba el mismo
    resultado final que el problema que este pipeline intenta evitar (código roto + solo tests
    superficiales), pero encima sin generar nunca un test profundo que mostrara el fallo real de
    forma visible. Ahora la Fase B se ejecuta siempre que `regenerar_tests=True`, aunque
    code_ok sea False."""
    monkeypatch.setattr(
        reparacion_runtime,
        "prepare_poc_runtime_environment",
        lambda project_dir: _fake_ready_runtime_env(project_dir),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "runtime_verify_fastapi_project",
        lambda project_dir, spec, python_executable=None: (False, "persistent failure"),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "run_llm_safe_repair",
        lambda **kwargs: SafeRepairResult(ok=False, patch={}, logs=[], allowlist=[]),
    )

    def fake_materializar(*, nombre_proyecto, estructura, limpiar_directorio=True, **kwargs):
        return list(estructura.keys())

    monkeypatch.setattr(reparacion_runtime, "materializar_proyecto", fake_materializar)
    monkeypatch.setattr(pytest_llm_repair, "materializar_proyecto", fake_materializar)
    monkeypatch.setattr(
        pytest_llm_repair,
        "_run_pytest",
        lambda project_dir, python_executable=None: (True, "", ""),
    )

    generar_tests_calls: list[str] = []
    monkeypatch.setattr(
        reparacion_runtime,
        "generar_tests_unitarios",
        lambda *args, **kwargs: generar_tests_calls.append("called"),
    )

    estructura: Dict[str, str] = {"requirements.txt": "fastapi\n"}
    resultado: Dict[str, Any] = {"spec": {"dependencies": ["fastapi"]}}
    archivos_creados: list[str] = []
    project_dir = str(tmp_path / "some_generated_poc")

    reparacion_runtime.ejecutar_reparacion_runtime(
        nombre_proyecto="some_generated_poc_test",
        descripcion_global="x",
        project_dir=project_dir,
        context=ProjectContext(),
        resultado=resultado,
        estructura=estructura,
        archivos_creados=archivos_creados,
        regenerar_tests=True,
        max_runtime_repairs=1,
    )

    assert generar_tests_calls == ["called"], (
        "la generación de tests (Fase B) debe ejecutarse aunque code_ok sea False"
    )


# ---------------------------------------------------------------------------
# 8) No existe hardcode específico de pydantic_settings (ni en reparacion_runtime ni en el
#    fixer determinista de pytest_fixers.py).
# ---------------------------------------------------------------------------


def test_fix_missing_pydantic_settings_fixer_was_removed() -> None:
    assert not hasattr(pytest_fixers, "fix_missing_pydantic_settings")
    assert not hasattr(pytest_fixers, "_PYDANTIC_SETTINGS_MISSING_RE")

    source = inspect.getsource(pytest_fixers)
    assert "pydantic_settings" not in source
    assert "pydantic-settings" not in source


# ---------------------------------------------------------------------------
# requirements-dev.txt (httpx/httpx2) se materializa ANTES del venv, para que los probes
# internos de PoC-it (TestClient) no fallen por falta de cliente HTTP en el primer intento.
# ---------------------------------------------------------------------------


def test_requirements_dev_bootstrap_runs_before_venv_preparation(monkeypatch, tmp_path) -> None:
    call_order: list[str] = []

    def fake_prepare(project_dir):
        call_order.append("prepare_poc_runtime_environment")
        return _fake_ready_runtime_env(project_dir)

    monkeypatch.setattr(reparacion_runtime, "prepare_poc_runtime_environment", fake_prepare)
    monkeypatch.setattr(
        reparacion_runtime,
        "runtime_verify_fastapi_project",
        lambda project_dir, spec, python_executable=None: (True, "IMPORT_OK"),
    )

    from poc_it.orquestacion.wiring_verifier import WiringVerifyResult

    monkeypatch.setattr(
        reparacion_runtime,
        "verify_wiring_against_runtime_contracts",
        lambda **kwargs: WiringVerifyResult(ok=True, detail="", missing_paths=[]),
    )

    from poc_it.orquestacion.runtime_probe import RuntimeProbeResult

    monkeypatch.setattr(
        reparacion_runtime,
        "run_runtime_probe",
        lambda **kwargs: RuntimeProbeResult(ok=True, detail=""),
    )

    materialized: list[dict] = []

    def fake_materializar(*, nombre_proyecto, estructura, limpiar_directorio=True, **kwargs):
        if "requirements-dev.txt" in estructura:
            call_order.append("materialize_requirements_dev")
        materialized.append(dict(estructura))
        return list(estructura.keys())

    monkeypatch.setattr(reparacion_runtime, "materializar_proyecto", fake_materializar)

    estructura: Dict[str, Any] = {"requirements.txt": "fastapi\nuvicorn\n"}
    resultado: Dict[str, Any] = {"spec": {"dependencies": ["fastapi", "uvicorn"]}}
    archivos_creados: list[str] = []
    project_dir = str(tmp_path / "some_generated_poc")

    reparacion_runtime.ejecutar_reparacion_runtime(
        nombre_proyecto="some_generated_poc_test",
        descripcion_global="x",
        project_dir=project_dir,
        context=ProjectContext(),
        resultado=resultado,
        estructura=estructura,
        archivos_creados=archivos_creados,
        regenerar_tests=False,
        max_runtime_repairs=1,
    )

    assert call_order[0] == "materialize_requirements_dev"
    assert "prepare_poc_runtime_environment" in call_order
    assert call_order.index("materialize_requirements_dev") < call_order.index(
        "prepare_poc_runtime_environment"
    )

    assert "httpx" in estructura["requirements-dev.txt"]
    assert "httpx2" in estructura["requirements-dev.txt"]
    assert "pytest" in estructura["requirements-dev.txt"]


def test_requirements_dev_bootstrap_does_not_overwrite_existing_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        reparacion_runtime,
        "prepare_poc_runtime_environment",
        lambda project_dir: _fake_ready_runtime_env(project_dir),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "runtime_verify_fastapi_project",
        lambda project_dir, spec, python_executable=None: (True, "IMPORT_OK"),
    )

    from poc_it.orquestacion.wiring_verifier import WiringVerifyResult

    monkeypatch.setattr(
        reparacion_runtime,
        "verify_wiring_against_runtime_contracts",
        lambda **kwargs: WiringVerifyResult(ok=True, detail="", missing_paths=[]),
    )

    from poc_it.orquestacion.runtime_probe import RuntimeProbeResult

    monkeypatch.setattr(
        reparacion_runtime,
        "run_runtime_probe",
        lambda **kwargs: RuntimeProbeResult(ok=True, detail=""),
    )

    def fake_materializar(*, nombre_proyecto, estructura, limpiar_directorio=True, **kwargs):
        return list(estructura.keys())

    monkeypatch.setattr(reparacion_runtime, "materializar_proyecto", fake_materializar)

    preexisting = "pytest\ncustom-pinned-package==1.2.3\n"
    estructura: Dict[str, Any] = {
        "requirements.txt": "fastapi\nuvicorn\n",
        "requirements-dev.txt": preexisting,
    }
    resultado: Dict[str, Any] = {"spec": {"dependencies": ["fastapi", "uvicorn"]}}
    archivos_creados: list[str] = []
    project_dir = str(tmp_path / "some_generated_poc")

    reparacion_runtime.ejecutar_reparacion_runtime(
        nombre_proyecto="some_generated_poc_test",
        descripcion_global="x",
        project_dir=project_dir,
        context=ProjectContext(),
        resultado=resultado,
        estructura=estructura,
        archivos_creados=archivos_creados,
        regenerar_tests=False,
        max_runtime_repairs=1,
    )

    assert estructura["requirements-dev.txt"] == preexisting
