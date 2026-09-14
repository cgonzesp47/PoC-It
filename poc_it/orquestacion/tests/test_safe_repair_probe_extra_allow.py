from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import poc_it.orquestacion.reparacion_runtime as reparacion_runtime
from poc_it.modulos.models import ProjectContext
from poc_it.orquestacion.llm_safe_repair import SafeRepairResult
from poc_it.orquestacion.runtime_probe import RuntimeProbeResult
from poc_it.orquestacion.wiring_verifier import WiringVerifyResult
from poc_it.runtime.poc_runtime_environment import PocRuntimeEnvironment
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH


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


def test_rejected_patch_history_is_passed_to_the_next_outer_repair_attempt(monkeypatch, tmp_path):
    """Regresión: cada ronda EXTERIOR del bucle de reparación (import-time -> wiring -> probe,
    hasta `max_runtime_repairs` veces) llamaba a `run_llm_safe_repair` desde cero, sin decirle
    nunca qué parche ya se había aplicado de verdad en una ronda anterior y por qué la
    re-verificación externa lo había rechazado. Confirmamos que la 2ª ronda exterior SÍ recibe,
    vía `previous_attempts`, el parche y el motivo de rechazo de la 1ª ronda."""
    monkeypatch.setattr(
        reparacion_runtime,
        "prepare_poc_runtime_environment",
        lambda project_dir: _fake_ready_runtime_env(project_dir),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "runtime_verify_fastapi_project",
        lambda project_dir, spec, python_executable=None: (True, ""),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "run_runtime_probe",
        lambda **kwargs: RuntimeProbeResult(ok=False, detail="RUNTIME_REQUEST_PROBE_FAILED\n..."),
    )
    # Gate 2 (wiring) rechaza siempre, para forzar rollback + siguiente ronda exterior de forma
    # determinista (sin depender de una segunda llamada a runtime_verify_fastapi_project).
    monkeypatch.setattr(
        reparacion_runtime,
        "verify_wiring_against_runtime_contracts",
        lambda *, project_dir, estructura, python_executable=None: WiringVerifyResult(
            ok=False, detail="wiring still broken", missing_paths=[], openapi=None
        ),
    )
    monkeypatch.setattr(reparacion_runtime, "materializar_proyecto", lambda **kwargs: [])

    safe_repair_calls: list[Dict[str, Any]] = []

    def fake_safe_repair(*, previous_attempts=None, **kwargs):
        attempt_index = len(safe_repair_calls)
        # OJO: `previous_attempts` es la MISMA lista mutable que el caller sigue rellenando en
        # rondas posteriores — copiamos aquí para capturar el estado EXACTO en el momento de
        # esta llamada, no una referencia que reflejará también entradas futuras.
        safe_repair_calls.append(
            {"previous_attempts": list(previous_attempts) if previous_attempts is not None else None}
        )
        return SafeRepairResult(
            ok=True,
            patch={"app/api/endpoints/upload.py": f"# candidate attempt {attempt_index}\n"},
            logs=[],
            allowlist=["app/api/endpoints/upload.py"],
        )

    monkeypatch.setattr(reparacion_runtime, "run_llm_safe_repair", fake_safe_repair)

    estructura: Dict[str, str] = {
        RUNTIME_CONTRACTS_PATH: json.dumps({"endpoints": []}, ensure_ascii=False),
    }
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
        regenerar_tests=False,
        max_runtime_repairs=1,
    )

    assert len(safe_repair_calls) == 2
    # 1ª ronda exterior: todavía no hay historial de intentos rechazados.
    assert safe_repair_calls[0]["previous_attempts"] is None
    # 2ª ronda exterior: debe recibir el parche + motivo de rechazo de la 1ª ronda.
    second_call_history = safe_repair_calls[1]["previous_attempts"]
    assert second_call_history is not None
    assert len(second_call_history) == 1
    assert "app/api/endpoints/upload.py" in second_call_history[0]["patch"]
    assert "candidate attempt 0" in second_call_history[0]["patch"]["app/api/endpoints/upload.py"]
    assert "wiring still broken" in second_call_history[0]["reason"]


def test_resolve_dotted_import_to_repo_path_strips_trailing_symbol_segments():
    estructura = {"app/integrations/google_drive_api.py": "..."}
    assert (
        reparacion_runtime._resolve_dotted_import_to_repo_path(
            "app.integrations.google_drive_api.build_client", estructura
        )
        == "app/integrations/google_drive_api.py"
    )


def test_resolve_dotted_import_to_repo_path_returns_none_when_no_file_matches():
    estructura = {"app/main.py": "..."}
    assert (
        reparacion_runtime._resolve_dotted_import_to_repo_path(
            "app.integrations.google_drive_api.build_client", estructura
        )
        is None
    )


def test_probe_failure_seeds_extra_allow_with_endpoint_module_paths(monkeypatch, tmp_path):
    """Regresión: cuando el request-time probe detecta un fallo real de aplicación (p.ej. un
    500 con `detail: "[Errno 2] No such file or directory: 'x'"`), ese `detail` es el cuerpo
    JSON de la respuesta HTTP -no un traceback de Python con "File ..., line ..."-, así que
    `_build_allowlist` no podía extraer ningún path y el safe repair se rendía de inmediato
    ("allowlist vacío"), sin ni siquiera intentar diagnosticar. Ahora se le pasan explícitamente
    los `module_path` de los endpoints conocidos vía `extra_allow_paths`."""
    monkeypatch.setattr(
        reparacion_runtime,
        "prepare_poc_runtime_environment",
        lambda project_dir: _fake_ready_runtime_env(project_dir),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "runtime_verify_fastapi_project",
        lambda project_dir, spec, python_executable=None: (True, ""),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "verify_wiring_against_runtime_contracts",
        lambda *, project_dir, estructura, python_executable=None: WiringVerifyResult(
            ok=True, detail="", missing_paths=[], openapi={"paths": {}}
        ),
    )

    probe_detail = (
        "OPENAPI_STATUS: 200\n"
        "EP_STATUS: POST /upload 500\n"
        'ENDPOINT_ERRORS_JSON: {"errors": [{"method": "POST", "path": "/upload", '
        '"status_code": 500, "detail": "{\\"detail\\":\\"[Errno 2] No such file or directory: '
        "'x'\\\"}\"}]}"
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "run_runtime_probe",
        lambda **kwargs: RuntimeProbeResult(ok=False, detail=probe_detail),
    )

    captured_kwargs: Dict[str, Any] = {}

    def fake_safe_repair(**kwargs):
        captured_kwargs.update(kwargs)
        return SafeRepairResult(ok=False, patch={}, logs=[], allowlist=[])

    monkeypatch.setattr(reparacion_runtime, "run_llm_safe_repair", fake_safe_repair)

    def fake_materializar(*, nombre_proyecto, estructura, limpiar_directorio=True, **kwargs):
        return list(estructura.keys())

    monkeypatch.setattr(reparacion_runtime, "materializar_proyecto", fake_materializar)

    runtime_contracts = {
        "endpoints": [
            {"path": "/health", "method": "GET", "module_path": "app/api/endpoints/health.py"},
            {
                "path": "/upload",
                "method": "POST",
                "module_path": "app/api/endpoints/upload.py",
                "depends_imports": ["app.integrations.google_drive.build_client"],
            },
        ],
        "allowed_dependency_overrides": ["app.integrations.google_drive.build_client"],
    }
    estructura: Dict[str, str] = {
        RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False),
    }
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
        regenerar_tests=False,
        max_runtime_repairs=1,
    )

    assert captured_kwargs.get("extra_allow_paths") is not None
    assert "app/api/endpoints/upload.py" in captured_kwargs["extra_allow_paths"]
    assert "app/api/endpoints/health.py" in captured_kwargs["extra_allow_paths"]


def test_probe_failure_seeds_extra_allow_with_depends_imports_module_of_injected_dependency(monkeypatch, tmp_path):
    """Regresión concreta (E2E real, PParcialGoogle2_1): el bug real vivía en
    `app/integrations/google_drive_api.py::upload_to_google_drive` (un mismo parámetro
    `filename` se usaba a la vez como nombre para Drive y como path local a abrir), no en
    `app/api/endpoints/upload.py`. El endpoint es el único módulo cuyo `module_path` entraba en
    `extra_allow_paths`, así que 4 rondas de Safe LLM Repair intentaron arreglarlo una y otra
    vez tocando solo el endpoint, sin poder tocar jamás el módulo de integración donde estaba el
    bug -aunque lo hubiese diagnosticado bien-, porque el patch step exige que
    `files_to_change` sea subconjunto del allowlist. Ahora `depends_imports` (los símbolos
    inyectados vía Depends(), p.ej. "app.integrations.google_drive_api.build_client") también se
    resuelven a un path real del proyecto y se añaden al allowlist."""
    monkeypatch.setattr(
        reparacion_runtime,
        "prepare_poc_runtime_environment",
        lambda project_dir: _fake_ready_runtime_env(project_dir),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "runtime_verify_fastapi_project",
        lambda project_dir, spec, python_executable=None: (True, ""),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "verify_wiring_against_runtime_contracts",
        lambda *, project_dir, estructura, python_executable=None: WiringVerifyResult(
            ok=True, detail="", missing_paths=[], openapi={"paths": {}}
        ),
    )

    probe_detail = (
        "OPENAPI_STATUS: 200\n"
        "EP_STATUS: POST /upload 500\n"
        'ENDPOINT_ERRORS_JSON: {"errors": [{"method": "POST", "path": "/upload", '
        '"status_code": 500, "detail": "{\\"detail\\":\\"[Errno 2] No such file or directory: '
        "'x'\\\"}\"}]}"
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "run_runtime_probe",
        lambda **kwargs: RuntimeProbeResult(ok=False, detail=probe_detail),
    )

    captured_kwargs: Dict[str, Any] = {}

    def fake_safe_repair(**kwargs):
        captured_kwargs.update(kwargs)
        return SafeRepairResult(ok=False, patch={}, logs=[], allowlist=[])

    monkeypatch.setattr(reparacion_runtime, "run_llm_safe_repair", fake_safe_repair)

    def fake_materializar(*, nombre_proyecto, estructura, limpiar_directorio=True, **kwargs):
        return list(estructura.keys())

    monkeypatch.setattr(reparacion_runtime, "materializar_proyecto", fake_materializar)

    runtime_contracts = {
        "endpoints": [
            {"path": "/health", "method": "GET", "module_path": "app/api/endpoints/health.py"},
            {
                "path": "/upload",
                "method": "POST",
                "module_path": "app/api/endpoints/upload.py",
                "depends_imports": ["app.integrations.google_drive_api.build_client"],
            },
        ],
        "allowed_dependency_overrides": ["app.integrations.google_drive_api.build_client"],
    }
    estructura: Dict[str, str] = {
        RUNTIME_CONTRACTS_PATH: json.dumps(runtime_contracts, ensure_ascii=False),
        "app/integrations/google_drive_api.py": "def build_client():\n    ...\n",
    }
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
        regenerar_tests=False,
        max_runtime_repairs=1,
    )

    assert captured_kwargs.get("extra_allow_paths") is not None
    assert "app/integrations/google_drive_api.py" in captured_kwargs["extra_allow_paths"]
    assert "app/api/endpoints/upload.py" in captured_kwargs["extra_allow_paths"]


def test_applying_a_safe_repair_patch_never_wipes_the_project_directory(monkeypatch, tmp_path):
    """Regresión GRAVE: al materializar el parche que devuelve `run_llm_safe_repair`, varias
    llamadas a `materializar_proyecto(nombre_proyecto=..., estructura=patch)` en
    reparacion_runtime.py no pasaban `limpiar_directorio=False`, así que se usaba el valor por
    defecto (`True`) — BORRANDO TODO el proyecto (`app/main.py`, `app/core/config.py`, etc.) y
    dejando solo los 1-2 ficheros del propio parche. Esto explicaba por qué, tras una reparación
    'exitosa' que luego fallaba la re-verificación, ya no quedaba ni `app/` en disco: el borrado
    ya había ocurrido antes de intentar el rollback."""
    monkeypatch.setattr(
        reparacion_runtime,
        "prepare_poc_runtime_environment",
        lambda project_dir: _fake_ready_runtime_env(project_dir),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "runtime_verify_fastapi_project",
        lambda project_dir, spec, python_executable=None: (True, ""),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "verify_wiring_against_runtime_contracts",
        lambda *, project_dir, estructura, python_executable=None: WiringVerifyResult(
            ok=True, detail="", missing_paths=[], openapi={"paths": {}}
        ),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "run_runtime_probe",
        lambda **kwargs: RuntimeProbeResult(ok=False, detail="RUNTIME_REQUEST_PROBE_FAILED\n..."),
    )
    monkeypatch.setattr(
        reparacion_runtime,
        "run_llm_safe_repair",
        lambda **kwargs: SafeRepairResult(
            ok=True,
            patch={"app/api/endpoints/upload.py": "# patched content\n"},
            logs=[],
            allowlist=["app/api/endpoints/upload.py"],
        ),
    )

    materialize_calls: list[Dict[str, Any]] = []

    def recording_materializar(*, nombre_proyecto, estructura, limpiar_directorio=True, **kwargs):
        materialize_calls.append({"estructura": dict(estructura), "limpiar_directorio": limpiar_directorio})
        return list(estructura.keys())

    monkeypatch.setattr(reparacion_runtime, "materializar_proyecto", recording_materializar)

    estructura: Dict[str, str] = {
        RUNTIME_CONTRACTS_PATH: json.dumps({"endpoints": []}, ensure_ascii=False),
        "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        "app/core/config.py": "x = 1\n",
    }
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
        regenerar_tests=False,
        max_runtime_repairs=1,
    )

    # Ninguna materialización de un parche parcial debe haber borrado el directorio.
    patch_only_calls = [
        call for call in materialize_calls if set(call["estructura"].keys()) == {"app/api/endpoints/upload.py"}
    ]
    assert patch_only_calls, "se esperaba al menos una materialización del parche del safe repair"
    for call in patch_only_calls:
        assert call["limpiar_directorio"] is False, (
            "materializar el parche con limpiar_directorio=True (o por defecto) borraría "
            "app/main.py y el resto del proyecto"
        )
