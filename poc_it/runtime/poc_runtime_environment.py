from __future__ import annotations
"""
Entorno de ejecución aislado para la PoC generada.

Problema que resuelve
----------------------
Los runtime probes (import-time, OpenAPI smoke, request-time) y pytest ejecutaban Python
usando el intérprete de PoC-it (`sys.executable`) o el `python` del PATH. Si ese entorno no
tiene instaladas las dependencias declaradas en `requirements.txt` de la PoC generada, aparecen
falsos negativos como:

    ModuleNotFoundError: No module named 'google'

que no reflejan un defecto real de la PoC (que puede tener un `requirements.txt` perfectamente
correcto), sino que el entorno de ejecución no está preparado.

Este módulo es la ÚNICA fuente de verdad para resolver el entorno de ejecución de una PoC
generada:

    entorno de PoC-it  !=  entorno de la PoC generada

Todo lo que ejecuta código Python de la PoC (import probes, OpenAPI/runtime probes, pytest)
debe obtener su `python_executable` de `prepare_poc_runtime_environment(...)`, nunca de
`sys.executable` ni de `"python"` resuelto por PATH.

Ubicación del venv
-------------------
El venv NO se crea dentro de `<generated_project>/.poc_it/venv`. Con nombres de PoC largos
(frecuentes: descripciones humanas usadas como nombre de proyecto), esa ruta puede acercarse al
límite de longitud de path de Windows y `python -m venv` falla durante `ensurepip`. En su lugar,
el venv se guarda en una ubicación corta y estable, desacoplada del nombre humano del proyecto:

    <PoC-It root>/.poc_it/runtime_envs/<project_hash>/

donde `project_hash = runtime_env_id(project_dir)` es un hash determinista del path absoluto del
proyecto (no del nombre "bonito"), así que es estable entre ejecuciones para el mismo proyecto y
corto independientemente de lo largo que sea el nombre de la PoC.

Política explícita (no negociable)
-----------------------------------
- La ÚNICA fuente de instalación es `requirements.txt` (+ `requirements-dev.txt` opcional).
- NO hay auto-healing tipo `except ModuleNotFoundError: pip install missing_package`. Si falta
  una dependencia en requirements.txt, es un defecto de generación que debe repararse en el
  SPEC/requirements, no ocultarse instalando paquetes por fuera.
- Un venv parcialmente creado (p.ej. `python.exe` existe pero `ensurepip` falló y no hay `pip`)
  NUNCA se reutiliza: se valida con un healthcheck real (`python -m pip --version`) y, si no pasa,
  se borra y se recrea desde cero. Jamás se instala nada sobre un venv sospechoso.
- La secuencia es siempre: crear venv -> validar venv -> (solo si sano) instalar requirements.
  Un fallo de creación/validación NUNCA continúa a `pip install`.
- Un fallo de `pip install` (red, PyPI caído, etc.) o de creación/validación del venv se reporta
  como un `runtime_environment_status` específico y NUNCA se traduce automáticamente en
  `codegen_status="invalid"`: son ejes independientes.
- El entorno global (el Python que ejecuta PoC-it) nunca se modifica.
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

RuntimeEnvironmentStatus = Literal[
    "ready",
    "venv_create_failed",
    "venv_validation_failed",
    "dependency_install_failed",
    "not_prepared",
]

# Cuántos caracteres de stdout/stderr conservamos en el resumen estructurado de debug.
_OUTPUT_SUMMARY_MAX_CHARS = 2000

_REQUIREMENTS_FILENAME = "requirements.txt"
_REQUIREMENTS_DEV_FILENAME = "requirements-dev.txt"
_FINGERPRINT_RELPATH = os.path.join(".poc_it", "runtime_requirements.sha256")
_DEBUG_RELPATH = os.path.join(".poc_it", "runtime_environment.json")

_RUNTIME_ENVS_ROOT_ENV_VAR = "POC_IT_RUNTIME_ENVS_ROOT"


def _poc_it_repo_root() -> Path:
    # poc_it/runtime/poc_runtime_environment.py -> parents[2] == raíz del repo PoC-it.
    return Path(__file__).resolve().parents[2]


def _default_runtime_envs_root() -> Path:
    override = os.environ.get(_RUNTIME_ENVS_ROOT_ENV_VAR)
    if override:
        return Path(override)
    return _poc_it_repo_root() / ".poc_it" / "runtime_envs"


def runtime_env_id(project_dir: Path | str) -> str:
    """Hash determinista y corto del path absoluto del proyecto.

    Deliberadamente NO depende del nombre "humano" de la PoC (que puede ser muy largo), para que
    la ruta del venv sea siempre corta y estable en Windows.
    """
    raw = str(Path(project_dir).resolve()).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class PocRuntimeEnvironment:
    """Entorno de ejecución aislado y listo para una PoC generada concreta."""

    project_dir: Path
    venv_dir: Path
    python_executable: Path
    runtime_environment_status: RuntimeEnvironmentStatus
    requirements_hash: str
    venv_created: bool
    venv_recreated: bool
    venv_healthcheck_ok: bool
    install_attempted: bool
    install_exit_code: int | None
    install_stdout_summary: str
    install_stderr_summary: str
    detail: str

    @property
    def ready(self) -> bool:
        return self.runtime_environment_status == "ready"

    def to_debug_dict(self) -> dict:
        return {
            "runtime_environment_status": self.runtime_environment_status,
            "project_dir": str(self.project_dir),
            "venv_dir": str(self.venv_dir),
            "python_executable": str(self.python_executable),
            "requirements_hash": self.requirements_hash,
            "venv_created": self.venv_created,
            "venv_recreated": self.venv_recreated,
            "venv_healthcheck_ok": self.venv_healthcheck_ok,
            "install_attempted": self.install_attempted,
            "install_exit_code": self.install_exit_code,
            "install_stdout_summary": self.install_stdout_summary,
            "install_stderr_summary": self.install_stderr_summary,
            "detail": self.detail,
        }


def _venv_python_path(venv_dir: Path) -> Path:
    win = venv_dir / "Scripts" / "python.exe"
    if win.exists():
        return win
    posix = venv_dir / "bin" / "python"
    if posix.exists():
        return posix
    # Ruta esperada aunque aún no exista (para reportarla en detail/errores).
    return win if os.name == "nt" else posix


def _venv_dir_present(venv_dir: Path) -> bool:
    """El directorio del venv existe con AL MENOS el ejecutable python (no implica que esté sano)."""
    win = venv_dir / "Scripts" / "python.exe"
    posix = venv_dir / "bin" / "python"
    return win.exists() or posix.exists()


def _read_text(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass
    return ""


def _summarize(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _OUTPUT_SUMMARY_MAX_CHARS:
        return text
    head = text[: _OUTPUT_SUMMARY_MAX_CHARS // 2]
    tail = text[-_OUTPUT_SUMMARY_MAX_CHARS // 2 :]
    return f"{head}\n...[truncated {len(text) - _OUTPUT_SUMMARY_MAX_CHARS} chars]...\n{tail}"


def _compute_requirements_hash(project_dir: Path) -> str:
    req = _read_text(project_dir / _REQUIREMENTS_FILENAME)
    req_dev = _read_text(project_dir / _REQUIREMENTS_DEV_FILENAME)
    blob = "\n---\n".join([req.strip(), req_dev.strip()]).strip()
    return hashlib.sha256(blob.encode("utf-8", errors="ignore")).hexdigest()


def _fingerprint_path(project_dir: Path) -> Path:
    return project_dir / _FINGERPRINT_RELPATH


def _read_stored_fingerprint(project_dir: Path) -> str | None:
    fp = _fingerprint_path(project_dir)
    try:
        if fp.exists():
            data = json.loads(fp.read_text(encoding="utf-8", errors="ignore"))
            value = data.get("requirements_hash")
            return str(value) if value else None
    except Exception:
        return None
    return None


def _write_stored_fingerprint(project_dir: Path, requirements_hash: str) -> None:
    fp = _fingerprint_path(project_dir)
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(
            json.dumps({"requirements_hash": requirements_hash}, ensure_ascii=False, indent=2),
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        logger.warning("[RUNTIME_ENV] No se pudo persistir fingerprint en %s", fp)


def _persist_debug(project_dir: Path, env: "PocRuntimeEnvironment") -> None:
    dp = project_dir / _DEBUG_RELPATH
    try:
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(
            json.dumps(env.to_debug_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        logger.warning("[RUNTIME_ENV] No se pudo persistir debug en %s", dp)


def _run(cmd: list[str], *, cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as exc:
        return -1, "", f"{type(exc).__name__}: {exc}"


def _create_venv(venv_dir: Path) -> tuple[bool, str]:
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    # Usamos el intérprete de PoC-it solo para CREAR el venv (venv no tiene forma de "auto-crearse").
    # Esto NO modifica el entorno global de PoC-it: `python -m venv` solo escribe en `venv_dir`.
    rc, out, err = _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=venv_dir.parent)
    if rc != 0:
        return False, _summarize(out + "\n" + err)
    return True, ""


def _validate_venv(python_executable: Path, *, cwd: Path) -> bool:
    """Healthcheck real: un venv con `python.exe` pero sin `pip` (ensurepip falló) NO es sano."""
    if not python_executable.exists():
        return False
    rc, _out, _err = _run([str(python_executable), "-m", "pip", "--version"], cwd=cwd)
    return rc == 0


def prepare_poc_runtime_environment(
    project_dir: Path | str,
    *,
    runtime_envs_root: Path | str | None = None,
) -> PocRuntimeEnvironment:
    """Función autoritativa: garantiza un entorno aislado y listo para ejecutar la PoC generada.

    Flujo:
        crear venv si no existe (en una ruta corta, desacoplada del nombre de la PoC)
        -> validar el venv con un healthcheck real (`python -m pip --version`)
        -> si estaba corrupto, borrarlo y recrearlo desde cero (nunca reutilizar uno sospechoso)
        -> solo si el venv está sano: calcular hash de requirements.txt (+ requirements-dev.txt)
           e instalar SOLO si el hash cambió respecto al fingerprint guardado
        -> devolver PocRuntimeEnvironment con python_executable del venv

    No lanza excepciones: cualquier fallo se refleja en un `runtime_environment_status`
    específico (`venv_create_failed` / `venv_validation_failed` / `dependency_install_failed`)
    y en `detail`, para que el caller decida su propia política (nunca invalidar codegen por
    esto). Idempotente: se puede (y se debe) volver a llamar tras materializar
    `requirements-dev.txt`; el fingerprint decide si hace falta reinstalar.
    """
    project = Path(project_dir).resolve()
    envs_root = Path(runtime_envs_root).resolve() if runtime_envs_root else _default_runtime_envs_root()
    venv_dir = envs_root / runtime_env_id(project)

    try:
        project.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("[RUNTIME_ENV] No se pudo crear project_dir=%s: %s", project, exc)

    venv_created = False
    venv_recreated = False
    venv_healthcheck_ok = False

    # 1) Si el directorio del venv ya existe, validarlo. Si está corrupto (p.ej. `ensurepip`
    #    falló y quedó `python.exe` sin `pip`), NUNCA se reutiliza: se borra y se recrea.
    if _venv_dir_present(venv_dir):
        candidate_python = _venv_python_path(venv_dir)
        if _validate_venv(candidate_python, cwd=project):
            venv_healthcheck_ok = True
        else:
            logger.warning(
                "[RUNTIME_ENV] venv corrupto detectado (healthcheck falló) en %s; se elimina y recrea.",
                venv_dir,
            )
            shutil.rmtree(venv_dir, ignore_errors=True)
            venv_recreated = True

    # 2) Crear venv si no existe (porque nunca existió, o porque se acaba de borrar por corrupto).
    if not _venv_dir_present(venv_dir):
        created, create_detail = _create_venv(venv_dir)
        venv_created = True
        if not created:
            logger.error(
                "[RUNTIME_ENV] venv_create_failed project_dir=%s venv_dir=%s detail=%s",
                project,
                venv_dir,
                create_detail,
            )
            env = PocRuntimeEnvironment(
                project_dir=project,
                venv_dir=venv_dir,
                python_executable=_venv_python_path(venv_dir),
                runtime_environment_status="venv_create_failed",
                requirements_hash=_compute_requirements_hash(project),
                venv_created=venv_created,
                venv_recreated=venv_recreated,
                venv_healthcheck_ok=False,
                install_attempted=False,
                install_exit_code=None,
                install_stdout_summary="",
                install_stderr_summary=create_detail,
                detail=f"venv create failed: {create_detail}",
            )
            _persist_debug(project, env)
            return env

    python_executable = _venv_python_path(venv_dir)

    # 3) Validar salud del venv (recién creado o reutilizado) ANTES de instalar nada.
    #    Un venv con `ensurepip` fallido puede dejar `python.exe` presente pero sin `pip`.
    if not venv_healthcheck_ok:
        venv_healthcheck_ok = _validate_venv(python_executable, cwd=project)
    if not venv_healthcheck_ok:
        logger.error(
            "[RUNTIME_ENV] venv_validation_failed project_dir=%s venv_dir=%s python=%s",
            project,
            venv_dir,
            python_executable,
        )
        env = PocRuntimeEnvironment(
            project_dir=project,
            venv_dir=venv_dir,
            python_executable=python_executable,
            runtime_environment_status="venv_validation_failed",
            requirements_hash=_compute_requirements_hash(project),
            venv_created=venv_created,
            venv_recreated=venv_recreated,
            venv_healthcheck_ok=False,
            install_attempted=False,
            install_exit_code=None,
            install_stdout_summary="",
            install_stderr_summary="",
            detail="venv healthcheck failed (python -m pip --version did not succeed)",
        )
        _persist_debug(project, env)
        return env

    # 4) Fingerprint de requirements.txt (+ requirements-dev.txt). Solo llegamos aquí con un
    #    venv ya validado como sano: nunca se instala nada sobre un venv sospechoso.
    requirements_hash = _compute_requirements_hash(project)
    stored_hash = _read_stored_fingerprint(project)

    if stored_hash == requirements_hash:
        logger.debug(
            "[RUNTIME_ENV] up_to_date project_dir=%s venv_dir=%s python=%s requirements_hash=%s",
            project,
            venv_dir,
            python_executable,
            requirements_hash,
        )
        env = PocRuntimeEnvironment(
            project_dir=project,
            venv_dir=venv_dir,
            python_executable=python_executable,
            runtime_environment_status="ready",
            requirements_hash=requirements_hash,
            venv_created=venv_created,
            venv_recreated=venv_recreated,
            venv_healthcheck_ok=True,
            install_attempted=False,
            install_exit_code=0,
            install_stdout_summary="",
            install_stderr_summary="",
            detail="venv up-to-date; requirements hash unchanged, skipping reinstall",
        )
        _persist_debug(project, env)
        return env

    # 5) Instalar SOLO desde requirements.txt (+ requirements-dev.txt opcional). Fuente única de
    #    verdad: nunca se instalan paquetes sueltos fuera de estos ficheros.
    req_path = project / _REQUIREMENTS_FILENAME
    req_dev_path = project / _REQUIREMENTS_DEV_FILENAME

    install_attempted = False
    install_exit_code: int | None = None
    install_stdout = ""
    install_stderr = ""

    if req_path.exists():
        install_attempted = True
        rc, out, err = _run(
            [str(python_executable), "-m", "pip", "install", "-r", str(req_path)],
            cwd=project,
        )
        install_exit_code = rc
        install_stdout, install_stderr = out, err

        if rc != 0:
            logger.error(
                "[RUNTIME_ENV] dependency_install_failed project_dir=%s venv_dir=%s python=%s "
                "requirements_hash=%s install_exit_code=%s",
                project,
                venv_dir,
                python_executable,
                requirements_hash,
                rc,
            )
            logger.debug("[RUNTIME_ENV] pip stdout: %s", _summarize(out))
            logger.debug("[RUNTIME_ENV] pip stderr: %s", _summarize(err))
            env = PocRuntimeEnvironment(
                project_dir=project,
                venv_dir=venv_dir,
                python_executable=python_executable,
                runtime_environment_status="dependency_install_failed",
                requirements_hash=requirements_hash,
                venv_created=venv_created,
                venv_recreated=venv_recreated,
                venv_healthcheck_ok=True,
                install_attempted=True,
                install_exit_code=rc,
                install_stdout_summary=_summarize(out),
                install_stderr_summary=_summarize(err),
                detail=f"pip install -r requirements.txt failed (exit={rc})",
            )
            _persist_debug(project, env)
            return env

        # requirements-dev.txt es opcional (necesario para pytest en el proyecto generado).
        if req_dev_path.exists():
            rc_dev, out_dev, err_dev = _run(
                [str(python_executable), "-m", "pip", "install", "-r", str(req_dev_path)],
                cwd=project,
            )
            install_stdout += "\n" + out_dev
            install_stderr += "\n" + err_dev
            if rc_dev != 0:
                logger.error(
                    "[RUNTIME_ENV] dependency_install_failed(dev) project_dir=%s python=%s install_exit_code=%s",
                    project,
                    python_executable,
                    rc_dev,
                )
                env = PocRuntimeEnvironment(
                    project_dir=project,
                    venv_dir=venv_dir,
                    python_executable=python_executable,
                    runtime_environment_status="dependency_install_failed",
                    requirements_hash=requirements_hash,
                    venv_created=venv_created,
                    venv_recreated=venv_recreated,
                    venv_healthcheck_ok=True,
                    install_attempted=True,
                    install_exit_code=rc_dev,
                    install_stdout_summary=_summarize(install_stdout),
                    install_stderr_summary=_summarize(install_stderr),
                    detail=f"pip install -r requirements-dev.txt failed (exit={rc_dev})",
                )
                _persist_debug(project, env)
                return env
    else:
        logger.debug(
            "[RUNTIME_ENV] requirements.txt not found yet at %s; venv created without installing deps",
            req_path,
        )

    _write_stored_fingerprint(project, requirements_hash)

    logger.info(
        "[RUNTIME_ENV] ready project_dir=%s venv_dir=%s python=%s requirements_hash=%s "
        "venv_created=%s venv_recreated=%s install_attempted=%s install_exit_code=%s",
        project,
        venv_dir,
        python_executable,
        requirements_hash,
        venv_created,
        venv_recreated,
        install_attempted,
        install_exit_code,
    )

    env = PocRuntimeEnvironment(
        project_dir=project,
        venv_dir=venv_dir,
        python_executable=python_executable,
        runtime_environment_status="ready",
        requirements_hash=requirements_hash,
        venv_created=venv_created,
        venv_recreated=venv_recreated,
        venv_healthcheck_ok=True,
        install_attempted=install_attempted,
        install_exit_code=install_exit_code,
        install_stdout_summary=_summarize(install_stdout),
        install_stderr_summary=_summarize(install_stderr),
        detail="venv ready" if install_attempted else "venv ready (no requirements.txt to install yet)",
    )
    _persist_debug(project, env)
    return env
