from __future__ import annotations
"""
Venv manager para proyectos generados.

Problema que resuelve
--------------------
Los verificadores runtime (import-time/openapi probe/request-time probe) ejecutan Python
sobre el proyecto generado. Si ese Python es el mismo que ejecuta PoC-it, en una máquina
limpia puede no tener dependencias del proyecto (p.ej. fastapi), provocando falsos negativos:

    ModuleNotFoundError: No module named 'fastapi'

Este módulo crea un venv dentro del proyecto generado (./.poc_it/venv) e instala las
dependencias declaradas (requirements.txt / requirements-dev.txt) de forma best-effort.

Diseño
------
- Hermético por proyecto: venv por PoC.
- Cacheado por fingerprint de requirements para evitar reinstalar siempre.
- Best-effort: si no se puede crear/instalar (sin red/pip bloqueado), no rompe el pipeline.
"""

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VenvReadyResult:
    ok: bool
    detail: str


def _read_text(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass
    return ""


def _sha256(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8", errors="ignore")).hexdigest()


def _venv_python(project_dir: str) -> Path:
    p = Path(project_dir) / ".poc_it" / "venv"
    # Windows
    win = p / "Scripts" / "python.exe"
    if win.exists():
        return win
    # POSIX
    posix = p / "bin" / "python"
    return posix


def _venv_exists(project_dir: str) -> bool:
    py = _venv_python(project_dir)
    return py.exists()


def _state_path(project_dir: str) -> Path:
    return Path(project_dir) / ".poc_it" / "venv_state.json"


def _compute_fingerprint(project_dir: str, *, estructura: dict[str, str] | None, spec: Any) -> str:
    # Fuente de verdad: requirements.txt del proyecto (en disco o estructura en memoria)
    req = ""
    req_dev = ""

    # 1) in-memory estructura (más fresca durante generación)
    if isinstance(estructura, dict):
        req = str(estructura.get("requirements.txt") or "")
        req_dev = str(estructura.get("requirements-dev.txt") or "")

    # 2) disco (fallback)
    p = Path(project_dir)
    if not req.strip():
        req = _read_text(p / "requirements.txt")
    if not req_dev.strip():
        req_dev = _read_text(p / "requirements-dev.txt")

    # 3) spec.dependencies (fallback final): útil si requirements aún no está materializado
    deps = ""
    try:
        if isinstance(spec, dict):
            d = spec.get("dependencies")
            if isinstance(d, list):
                deps = "\n".join(sorted({str(x).strip() for x in d if str(x).strip()}))
    except Exception:
        deps = ""

    blob = "\n---\n".join([req.strip(), req_dev.strip(), deps.strip()]).strip()
    return _sha256(blob)


def _load_state(project_dir: str) -> dict:
    sp = _state_path(project_dir)
    try:
        if sp.exists():
            return json.loads(sp.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        pass
    return {}


def _save_state(project_dir: str, state: dict) -> None:
    sp = _state_path(project_dir)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8", errors="ignore")


def _run(cmd: list[str], *, cwd: str | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
    return p.returncode, out


def ensure_project_venv_ready(*, project_dir: str, estructura: dict[str, str] | None, spec: Any) -> VenvReadyResult:
    """Garantiza best-effort que existe un venv usable con deps instaladas.

    NO lanza excepción: devuelve ok=False si no pudo prepararlo.
    """
    project = Path(project_dir)
    poc_it_dir = project / ".poc_it"
    poc_it_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = _compute_fingerprint(project_dir, estructura=estructura, spec=spec)
    state = _load_state(project_dir)
    if state.get("fingerprint") == fingerprint and _venv_exists(project_dir):
        return VenvReadyResult(ok=True, detail="venv up-to-date")

    # 1) Crear venv si no existe
    if not _venv_exists(project_dir):
        rc, out = _run([sys.executable, "-m", "venv", str(poc_it_dir / "venv")], cwd=project_dir)
        if rc != 0:
            return VenvReadyResult(ok=False, detail=f"venv create failed: {out}")

    py = _venv_python(project_dir)
    if not py.exists():
        return VenvReadyResult(ok=False, detail="venv python not found after creation")

    # 2) Upgrade pip (best-effort)
    _run([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=project_dir)

    # 3) Instalar requirements si existe
    req_path = project / "requirements.txt"
    req_dev_path = project / "requirements-dev.txt"

    if req_path.exists():
        rc, out = _run([str(py), "-m", "pip", "install", "-r", str(req_path)], cwd=project_dir)
        if rc != 0:
            return VenvReadyResult(ok=False, detail=f"pip install requirements.txt failed: {out}")
    else:
        # fallback: si requirements no existe aún, intentar instalar desde spec.dependencies
        try:
            if isinstance(spec, dict):
                deps = spec.get("dependencies")
                if isinstance(deps, list):
                    pkgs = [str(x).strip() for x in deps if str(x).strip()]
                    if pkgs:
                        rc, out = _run([str(py), "-m", "pip", "install", *pkgs], cwd=project_dir)
                        if rc != 0:
                            return VenvReadyResult(ok=False, detail=f"pip install spec.dependencies failed: {out}")
        except Exception:
            pass

    # requirements-dev es opcional; instalar solo si existe (para pytest en proyecto generado)
    if req_dev_path.exists():
        _run([str(py), "-m", "pip", "install", "-r", str(req_dev_path)], cwd=project_dir)

    # Guardar estado
    state = {
        "fingerprint": fingerprint,
        "python": str(py),
        "project_dir": str(project),
    }
    _save_state(project_dir, state)
    return VenvReadyResult(ok=True, detail="venv ready")
