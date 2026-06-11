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


def _canonical_project_dir(project_dir: str) -> str:
    """Canonicaliza project_dir para que el hash del venv sea estable (relativo vs absoluto)."""
    p = _dedupe_project_dir(project_dir)
    p = os.path.normpath(str(p or "")).strip()
    if not p:
        return p
    try:
        return str(Path(p).resolve())
    except Exception:
        return p


def _venv_python_from_root(venv_root: Path) -> Path:
    # Windows
    win = venv_root / "Scripts" / "python.exe"
    if win.exists():
        return win
    # POSIX
    return venv_root / "bin" / "python"


def _venv_exists_from_root(venv_root: Path) -> bool:
    return _venv_python_from_root(venv_root).exists()


def _state_path(project_dir: str) -> Path:
    # Guardamos el estado junto al proyecto si existe; si no, lo guardamos junto al venv_root
    p = Path(project_dir)
    if p.exists():
        return p / ".poc_it" / "venv_state.json"
    return _safe_venv_root_for_project(project_dir) / "venv_state.json"


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


def _dedupe_project_dir(project_dir: str) -> str:
    """Normaliza y deduplica paths tipo .../output/<name>/output/<name>.

    Este bug se ha observado en logs cuando `project_dir` se construye de forma incorrecta
    en algún caller y acaba concatenándose consigo mismo. Como `ensure_project_venv_ready`
    es un punto crítico (invocado antes de runtime probes), aplicamos esta defensa aquí
    para hacerlo imposible de reproducir.
    """
    p = os.path.normpath(str(project_dir or "")).strip()
    if not p:
        return p

    # Heurística robusta: si el path contiene dos veces seguidas el patrón output/<name>,
    # lo reducimos a una sola ocurrencia.
    #
    # Ejemplo:
    #   output/<name>/output/<name>/.poc_it/venv/...
    parts = Path(p).parts
    try:
        # Buscar la última ocurrencia de "output" y comprobar si inmediatamente antes había otra
        # secuencia "output/<same_name>".
        for i in range(len(parts) - 2):
            if parts[i].lower() == "output" and i + 1 < len(parts):
                name = parts[i + 1]
                # patrón duplicado en i..i+1 y i+2..i+3
                if (
                    i + 3 < len(parts)
                    and parts[i + 2].lower() == "output"
                    and parts[i + 3] == name
                ):
                    # reconstruir quitando el bloque duplicado (i+2, i+3)
                    new_parts = list(parts[: i + 2]) + list(parts[i + 4 :])
                    return os.path.normpath(str(Path(*new_parts)))
    except Exception:
        pass

    return p


def _safe_venv_root_for_project(project_dir: str) -> Path:
    """Calcula una ruta de venv segura en Windows aunque el project_dir tenga caracteres raros o sea largo.

    Motivo:
    - `python -m venv` puede fallar en Windows con WinError 267/123 cuando el path contiene
      combinaciones extrañas, longitud excesiva, o cuando el directorio ni siquiera existe.
    - En vez de crear el venv *dentro* del proyecto (output/<nombre>/.poc_it/venv),
      lo creamos en un directorio estable y corto, derivado por hash del project_dir.

    Ruta:
      ./.poc_it/venvs/<sha1(project_dir)>/
    """
    base = Path(".poc_it") / "venvs"
    base.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1((project_dir or "").encode("utf-8", errors="ignore")).hexdigest()[:16]
    return (base / h).resolve()


def ensure_project_venv_ready(*, project_dir: str, estructura: dict[str, str] | None, spec: Any) -> VenvReadyResult:
    """Garantiza best-effort que existe un venv usable con deps instaladas.

    NO lanza excepción: devuelve ok=False si no pudo prepararlo.
    """
    project_dir = _canonical_project_dir(project_dir)
    project = Path(project_dir)

    # Crear/validar el directorio del proyecto: en algunos fallos reales el path recibido
    # no existía (nombre "lógico" != carpeta materializada), lo que dispara WinError 267.
    try:
        project.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        # Si NO podemos crear el directorio del proyecto, de todas formas podemos preparar un venv global
        # para ejecutar probes/tests herméticos. Continuamos con venv_root seguro.
        pass

    # Venv root seguro (siempre corto/estable). No depende del nombre del proyecto en output/.
    # IMPORTANTE: usar exactamente el mismo project_dir canonicalizado para evitar hashes distintos.
    venv_root = _safe_venv_root_for_project(project_dir)

    # Directorio de estado del proyecto (seguimos guardando state junto al proyecto si es posible)
    poc_it_dir = project / ".poc_it"
    try:
        poc_it_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # fallback: si no se puede crear dentro del proyecto, guardamos state en el venv_root
        poc_it_dir = venv_root
        poc_it_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = _compute_fingerprint(project_dir, estructura=estructura, spec=spec)
    state = _load_state(project_dir)
    if state.get("fingerprint") == fingerprint and _venv_exists_from_root(venv_root):
        return VenvReadyResult(ok=True, detail="venv up-to-date")

    # 1) Crear venv si no existe (en venv_root seguro)
    if not _venv_exists_from_root(venv_root):
        venv_root.mkdir(parents=True, exist_ok=True)
        rc, out = _run(
            [sys.executable, "-m", "venv", str(venv_root)],
            cwd=str(project) if project.exists() else None,
        )
        if rc != 0:
            return VenvReadyResult(ok=False, detail=f"venv create failed: {out}")

    py = _venv_python_from_root(venv_root)
    if not py.exists():
        return VenvReadyResult(ok=False, detail=f"venv python not found after creation: {py}")

    # 2) Upgrade pip (best-effort)
    _run([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=str(project) if project.exists() else None)

    # 3) Instalar requirements si existe
    req_path = project / "requirements.txt"
    req_dev_path = project / "requirements-dev.txt"

    if req_path.exists():
        rc, out = _run([str(py), "-m", "pip", "install", "-r", str(req_path)], cwd=str(project) if project.exists() else None)
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
