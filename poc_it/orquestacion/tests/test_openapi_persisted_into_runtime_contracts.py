from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import poc_it.orquestacion.reparacion_runtime as reparacion_runtime
from poc_it.modulos.models import ProjectContext
from poc_it.orquestacion.reparacion_runtime import _persist_openapi_into_runtime_contracts
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


# ---------------------------------------------------------------------------
# Unidad: _persist_openapi_into_runtime_contracts
# ---------------------------------------------------------------------------


def test_persist_openapi_writes_into_runtime_contracts_json():
    estructura = {RUNTIME_CONTRACTS_PATH: json.dumps({"endpoints": [{"path": "/health"}]})}
    openapi = {"paths": {"/health": {"get": {"responses": {"200": {}}}}}}

    changed = _persist_openapi_into_runtime_contracts(estructura=estructura, openapi=openapi)

    assert changed is True
    rc = json.loads(estructura[RUNTIME_CONTRACTS_PATH])
    assert rc["openapi"] == openapi
    assert rc["endpoints"] == [{"path": "/health"}]


def test_persist_openapi_is_idempotent_no_change_returns_false():
    openapi = {"paths": {}}
    estructura = {RUNTIME_CONTRACTS_PATH: json.dumps({"endpoints": [], "openapi": openapi})}

    changed = _persist_openapi_into_runtime_contracts(estructura=estructura, openapi=openapi)

    assert changed is False


def test_persist_openapi_no_op_when_openapi_is_none():
    estructura = {RUNTIME_CONTRACTS_PATH: json.dumps({"endpoints": []})}

    changed = _persist_openapi_into_runtime_contracts(estructura=estructura, openapi=None)

    assert changed is False
    assert "openapi" not in json.loads(estructura[RUNTIME_CONTRACTS_PATH])


# ---------------------------------------------------------------------------
# Integración: ejecutar_reparacion_runtime persiste el OpenAPI real tras un wiring verify OK.
# ---------------------------------------------------------------------------


def test_ejecutar_reparacion_runtime_persists_real_openapi_after_successful_wiring_verify(
    monkeypatch, tmp_path
):
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

    captured_openapi = {"paths": {"/health": {"get": {"responses": {"200": {}}}}}}
    monkeypatch.setattr(
        reparacion_runtime,
        "verify_wiring_against_runtime_contracts",
        lambda **kwargs: WiringVerifyResult(ok=True, detail="", missing_paths=[], openapi=captured_openapi),
    )

    from poc_it.orquestacion.runtime_probe import RuntimeProbeResult

    monkeypatch.setattr(
        reparacion_runtime,
        "run_runtime_probe",
        lambda **kwargs: RuntimeProbeResult(ok=True, detail=""),
    )

    materialized: list[dict] = []

    def fake_materializar(*, nombre_proyecto, estructura, limpiar_directorio=True, **kwargs):
        materialized.append(dict(estructura))
        return list(estructura.keys())

    monkeypatch.setattr(reparacion_runtime, "materializar_proyecto", fake_materializar)

    estructura: Dict[str, str] = {
        "requirements.txt": "fastapi\nuvicorn\n",
        RUNTIME_CONTRACTS_PATH: json.dumps({"endpoints": [{"path": "/health", "method": "GET"}]}),
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

    rc = json.loads(estructura[RUNTIME_CONTRACTS_PATH])
    assert rc.get("openapi") == captured_openapi
    assert any(RUNTIME_CONTRACTS_PATH in patch for patch in materialized)
