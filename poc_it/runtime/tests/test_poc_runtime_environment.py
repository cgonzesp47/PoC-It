from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from poc_it.runtime import poc_runtime_environment as pre


def _fake_venv_creation(cmd: list[str], *, cwd: Path):
    """Simula `python -m venv <dir>`: crea el archivo python ejecutable esperado, sin instalar nada real."""
    # cmd = [sys.executable, "-m", "venv", str(venv_dir)]
    venv_dir = Path(cmd[-1])
    py_path = venv_dir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    py_path.parent.mkdir(parents=True, exist_ok=True)
    py_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    return 0, "", ""


def _patch_run(
    monkeypatch,
    *,
    pip_install_rc: int = 0,
    pip_install_out: str = "",
    pip_install_err: str = "",
    pip_version_rc: int = 0,
    venv_create_rc: int = 0,
    venv_create_err: str = "",
    record: list | None = None,
):
    """Sustituye `poc_runtime_environment._run` por un doble controlable.

    Reconoce tres tipos de comando:
    - `python -m venv <dir>`       -> crea (o no, según venv_create_rc) el ejecutable esperado.
    - `<python> -m pip --version`  -> healthcheck; controlado por pip_version_rc.
    - `<python> -m pip install ...` -> instalación; controlado por pip_install_rc/out/err.
    """
    calls = record if record is not None else []

    def fake_run(cmd: list[str], *, cwd: Path):
        calls.append(list(cmd))
        if len(cmd) >= 3 and cmd[1] == "-m" and cmd[2] == "venv":
            if venv_create_rc != 0:
                return venv_create_rc, "", venv_create_err
            return _fake_venv_creation(cmd, cwd=cwd)
        if "pip" in cmd and "--version" in cmd:
            return pip_version_rc, "pip 24.0" if pip_version_rc == 0 else "", "" if pip_version_rc == 0 else "no pip module"
        if "pip" in cmd and "install" in cmd:
            return pip_install_rc, pip_install_out, pip_install_err
        return 0, "", ""

    monkeypatch.setattr(pre, "_run", fake_run)
    return calls


@pytest.fixture
def envs_root(tmp_path: Path) -> Path:
    return tmp_path / "poc_it_runtime_envs"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "generated_poc_with_a_very_long_human_readable_name_that_would_be_unsafe_on_windows"
    p.mkdir()
    (p / "requirements.txt").write_text("fastapi==0.110.0\nuvicorn==0.29.0\n", encoding="utf-8")
    return p


def _prepare(project: Path, envs_root: Path, **kwargs):
    return pre.prepare_poc_runtime_environment(project, runtime_envs_root=envs_root, **kwargs)


# ---------------------------------------------------------------------------
# 1) Ubicación corta y estable del venv (runtime_env_id), desacoplada del nombre de la PoC
# ---------------------------------------------------------------------------


def test_venv_location_is_short_and_derived_from_project_hash_not_poc_name(project: Path, envs_root: Path, monkeypatch):
    _patch_run(monkeypatch)

    env = _prepare(project, envs_root)

    expected_hash = pre.runtime_env_id(project)
    assert env.venv_dir == (envs_root / expected_hash).resolve()
    # El nombre largo/humano de la PoC no debe aparecer en la ruta del venv.
    assert project.name not in str(env.venv_dir)
    assert len(expected_hash) == 16


def test_runtime_env_id_is_deterministic_and_generic(tmp_path: Path):
    a = tmp_path / "poc_a"
    b = tmp_path / "poc_b"
    a.mkdir()
    b.mkdir()

    assert pre.runtime_env_id(a) == pre.runtime_env_id(a)
    assert pre.runtime_env_id(a) != pre.runtime_env_id(b)


# ---------------------------------------------------------------------------
# creación / reutilización de venv sano
# ---------------------------------------------------------------------------


def test_creates_venv_and_installs_from_requirements(project: Path, envs_root: Path, monkeypatch):
    calls = _patch_run(monkeypatch)

    env = _prepare(project, envs_root)

    assert env.runtime_environment_status == "ready"
    assert env.venv_created is True
    assert env.venv_recreated is False
    assert env.venv_healthcheck_ok is True
    assert env.python_executable.exists()
    assert env.install_attempted is True
    assert env.install_exit_code == 0

    venv_create_calls = [c for c in calls if len(c) >= 3 and c[1] == "-m" and c[2] == "venv"]
    install_calls = [c for c in calls if "pip" in c and "install" in c]
    assert len(venv_create_calls) == 1
    assert len(install_calls) == 1
    assert str(project / "requirements.txt") in install_calls[0]

    fp_path = project / ".poc_it" / "runtime_requirements.sha256"
    assert fp_path.exists()

    # Debug estructurado persistido con los campos requeridos.
    debug_path = project / ".poc_it" / "runtime_environment.json"
    assert debug_path.exists()
    import json

    debug = json.loads(debug_path.read_text(encoding="utf-8"))
    for key in (
        "runtime_environment_status",
        "project_dir",
        "venv_dir",
        "python_executable",
        "requirements_hash",
        "venv_created",
        "venv_recreated",
        "venv_healthcheck_ok",
        "install_attempted",
        "install_exit_code",
    ):
        assert key in debug
    assert debug["runtime_environment_status"] == "ready"


def test_healthy_venv_is_reused_without_recreating(project: Path, envs_root: Path, monkeypatch):
    _patch_run(monkeypatch)
    env1 = _prepare(project, envs_root)
    assert env1.venv_created is True

    calls = _patch_run(monkeypatch)
    env2 = _prepare(project, envs_root)

    assert env2.runtime_environment_status == "ready"
    assert env2.venv_created is False
    assert env2.venv_recreated is False
    assert env2.venv_healthcheck_ok is True
    assert env2.python_executable == env1.python_executable

    # Se valida (pip --version) pero NO se recrea el venv ni se reinstala (fingerprint sin cambios).
    venv_create_calls = [c for c in calls if len(c) >= 3 and c[1] == "-m" and c[2] == "venv"]
    install_calls = [c for c in calls if "pip" in c and "install" in c]
    assert venv_create_calls == []
    assert install_calls == []


# ---------------------------------------------------------------------------
# venv corrupto: python.exe presente pero sin pip
# ---------------------------------------------------------------------------


def test_venv_with_python_but_no_pip_is_detected_as_corrupt(project: Path, envs_root: Path, monkeypatch):
    # 1) Crear un venv "corrupto" a mano: solo el ejecutable, sin que pip funcione.
    venv_dir = envs_root / pre.runtime_env_id(project)
    py_path = venv_dir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    py_path.parent.mkdir(parents=True, exist_ok=True)
    py_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")

    healthcheck_calls = []

    def fake_run(cmd, *, cwd):
        healthcheck_calls.append(list(cmd))
        if "pip" in cmd and "--version" in cmd:
            return 1, "", "No module named pip"
        if len(cmd) >= 3 and cmd[1] == "-m" and cmd[2] == "venv":
            return _fake_venv_creation(cmd, cwd=cwd)
        if "pip" in cmd and "install" in cmd:
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(pre, "_run", fake_run)

    env = _prepare(project, envs_root)

    # El primer healthcheck (sobre el venv corrupto) debe haberse ejecutado y fallado.
    version_calls = [c for c in healthcheck_calls if "pip" in c and "--version" in c]
    assert len(version_calls) >= 1


def test_corrupt_venv_is_deleted_and_recreated(project: Path, envs_root: Path, monkeypatch):
    venv_dir = envs_root / pre.runtime_env_id(project)
    py_path = venv_dir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    py_path.parent.mkdir(parents=True, exist_ok=True)
    py_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    # marcador para comprobar que el directorio original se borra de verdad
    marker = venv_dir / "CORRUPT_MARKER.txt"
    marker.write_text("corrupt", encoding="utf-8")

    call_log = []

    def fake_run(cmd, *, cwd):
        call_log.append(list(cmd))
        if "pip" in cmd and "--version" in cmd:
            # Solo la PRIMERA validación (sobre el venv corrupto) falla; tras recrear, sana.
            already_recreated = any(
                len(c) >= 3 and c[1] == "-m" and c[2] == "venv" for c in call_log[:-1]
            )
            return (0, "pip 24.0", "") if already_recreated else (1, "", "No module named pip")
        if len(cmd) >= 3 and cmd[1] == "-m" and cmd[2] == "venv":
            return _fake_venv_creation(cmd, cwd=cwd)
        if "pip" in cmd and "install" in cmd:
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(pre, "_run", fake_run)

    env = _prepare(project, envs_root)

    assert not marker.exists(), "el venv corrupto debería haberse eliminado (shutil.rmtree)"
    assert env.venv_recreated is True
    assert env.venv_created is True
    assert env.venv_healthcheck_ok is True
    assert env.runtime_environment_status == "ready"


# ---------------------------------------------------------------------------
# Secuencia estricta: create -> validate -> (solo si sano) install
# ---------------------------------------------------------------------------


def test_venv_create_failure_never_proceeds_to_pip_install(project: Path, envs_root: Path, monkeypatch):
    calls = _patch_run(monkeypatch, venv_create_rc=1, venv_create_err="WinError 267 / ensurepip failed")

    env = _prepare(project, envs_root)

    assert env.runtime_environment_status == "venv_create_failed"
    assert env.install_attempted is False
    install_calls = [c for c in calls if "pip" in c and "install" in c]
    assert install_calls == [], "no debe intentarse pip install si la creación del venv falló"


def test_venv_validation_failure_never_proceeds_to_pip_install(project: Path, envs_root: Path, monkeypatch):
    # venv "se crea" (rc=0) pero el healthcheck de pip siempre falla -> nunca queda sano.
    calls = _patch_run(monkeypatch, pip_version_rc=1)

    env = _prepare(project, envs_root)

    assert env.runtime_environment_status == "venv_validation_failed"
    assert env.install_attempted is False
    install_calls = [c for c in calls if "pip" in c and "install" in c]
    assert install_calls == [], "no debe intentarse pip install si la validación del venv falló"


def test_failed_pip_install_yields_dependency_install_failed_without_raising(project: Path, envs_root: Path, monkeypatch):
    _patch_run(
        monkeypatch,
        pip_install_rc=1,
        pip_install_out="Collecting fastapi==0.110.0",
        pip_install_err="ERROR: Could not find a version that satisfies the requirement",
    )

    env = _prepare(project, envs_root)

    assert env.runtime_environment_status == "dependency_install_failed"
    assert env.install_attempted is True
    assert env.install_exit_code == 1
    assert "Could not find a version" in env.install_stderr_summary


# ---------------------------------------------------------------------------
# fingerprint: reinstalar solo cuando cambian requirements.txt / requirements-dev.txt
# ---------------------------------------------------------------------------


def test_reinstalls_when_requirements_change(project: Path, envs_root: Path, monkeypatch):
    _patch_run(monkeypatch)
    env1 = _prepare(project, envs_root)
    hash1 = env1.requirements_hash

    (project / "requirements.txt").write_text("fastapi==0.111.0\n", encoding="utf-8")

    calls = _patch_run(monkeypatch)
    env2 = _prepare(project, envs_root)

    assert env2.requirements_hash != hash1
    assert env2.install_attempted is True
    install_calls = [c for c in calls if "pip" in c and "install" in c]
    assert len(install_calls) == 1
    venv_create_calls = [c for c in calls if len(c) >= 3 and c[1] == "-m" and c[2] == "venv"]
    assert venv_create_calls == [], "el venv ya existía y era sano: no debe recrearse solo por cambiar requirements"


def test_requirements_dev_appearing_later_bumps_fingerprint_and_reinstalls(project: Path, envs_root: Path, monkeypatch):
    """Simula el flujo real: requirements.txt se instala primero; luego se generan tests y
    aparece requirements-dev.txt; una segunda llamada a prepare_poc_runtime_environment debe
    detectar el cambio de hash e instalar las deps de test harness."""
    _patch_run(monkeypatch)
    env1 = _prepare(project, envs_root)
    assert env1.install_attempted is True
    hash1 = env1.requirements_hash

    # "Fase B": se genera requirements-dev.txt después de preparar el entorno.
    (project / "requirements-dev.txt").write_text("pytest\npytest-mock\nhttpx\npytest-json-report\n", encoding="utf-8")

    calls = _patch_run(monkeypatch)
    env2 = _prepare(project, envs_root)

    assert env2.requirements_hash != hash1
    assert env2.install_attempted is True
    install_calls = [c for c in calls if "pip" in c and "install" in c]
    # requirements.txt + requirements-dev.txt
    assert len(install_calls) == 2
    assert any(str(project / "requirements.txt") in c for c in install_calls)
    assert any(str(project / "requirements-dev.txt") in c for c in install_calls)


# ---------------------------------------------------------------------------
# probes / pytest deben usar el python del venv aislado
# ---------------------------------------------------------------------------


def test_probes_use_venv_python_not_sys_executable(project: Path, envs_root: Path, monkeypatch):
    _patch_run(monkeypatch)
    env = _prepare(project, envs_root)

    assert str(env.python_executable) != sys.executable
    assert env.python_executable.is_relative_to(envs_root)

    from poc_it.orquestacion.verificador_runtime import _cmd_import_main

    cmd = _cmd_import_main(str(project), str(env.python_executable))
    assert cmd[0] == str(env.python_executable)


def test_pytest_runner_uses_explicit_python_executable(project: Path, monkeypatch):
    from poc_it.testing.execution import pytest_runner

    captured_commands: list[tuple] = []

    class FakeCompleted:
        def __init__(self):
            self.stdout = "collected 0 items"
            self.stderr = ""
            self.returncode = 0

    def fake_subprocess_run(cmd, **kwargs):
        captured_commands.append(tuple(cmd))
        return FakeCompleted()

    monkeypatch.setattr(pytest_runner.subprocess, "run", fake_subprocess_run)

    fake_python = str(project / "fake_venv_python")
    pytest_runner.run_pytest_in_project(project, python_executable=fake_python)

    assert captured_commands, "expected pytest to be invoked"
    for cmd in captured_commands:
        assert cmd[0] == fake_python
        assert cmd[0] != sys.executable


# ---------------------------------------------------------------------------
# hermeticidad: entorno global intacto, solo requirements.txt/-dev.txt como fuente de instalación
# ---------------------------------------------------------------------------


def test_global_environment_is_never_modified(project: Path, envs_root: Path, monkeypatch):
    calls = _patch_run(monkeypatch)
    env = _prepare(project, envs_root)

    install_calls = [c for c in calls if "pip" in c and "install" in c]
    assert install_calls, "expected at least one pip install call"
    for cmd in install_calls:
        assert cmd[0] == str(env.python_executable)
        assert cmd[0] != sys.executable


def test_only_requirements_files_are_installed_never_ad_hoc_packages(project: Path, envs_root: Path, monkeypatch):
    calls = _patch_run(monkeypatch)
    _prepare(project, envs_root)

    install_calls = [c for c in calls if "pip" in c and "install" in c]
    for cmd in install_calls:
        assert "-r" in cmd, f"pip install call did not use -r (ad-hoc package install?): {cmd}"


def test_no_requirements_file_creates_venv_without_installing(tmp_path: Path, envs_root: Path, monkeypatch):
    project = tmp_path / "poc_without_requirements"
    project.mkdir()
    calls = _patch_run(monkeypatch)

    env = _prepare(project, envs_root)

    assert env.runtime_environment_status == "ready"
    assert env.install_attempted is False
    install_calls = [c for c in calls if "pip" in c and "install" in c]
    assert install_calls == []


# ---------------------------------------------------------------------------
# requirements-dev.txt genérico: python-multipart NUNCA se añade de forma universal
# ---------------------------------------------------------------------------


def test_generic_requirements_dev_never_hardcodes_python_multipart():
    from poc_it.testing.test_generation_service import TestGenerationService

    service = TestGenerationService()
    estructura: dict[str, str] = {}
    service._asegurar_requirements_dev(estructura, {"spec": {"dev_dependencies": []}}, runtime_contracts=None)

    content = estructura["requirements-dev.txt"]
    assert "python-multipart" not in content
    for base in ("pytest", "pytest-mock", "httpx", "pytest-json-report"):
        assert base in content


def test_requirements_dev_adds_multipart_only_when_file_params_detected():
    from poc_it.testing.test_generation_service import TestGenerationService

    service = TestGenerationService()
    estructura: dict[str, str] = {}
    runtime_contracts = {
        "endpoints": [
            {"path": "/upload", "method": "POST", "file_params_required": ["file"]},
        ]
    }
    service._asegurar_requirements_dev(
        estructura, {"spec": {"dev_dependencies": []}}, runtime_contracts=runtime_contracts
    )

    content = estructura["requirements-dev.txt"]
    assert "python-multipart" in content
