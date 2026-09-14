"""
Arranque/parada automática del proxy LiteLLM para la interfaz web.

Reemplaza `start_litellm_proxy.ps1` por una gestión cross-platform desde
Python: al levantar el servidor web se comprueba si el proxy ya responde
en `LITELLM_BASE_URL` y, si no, se lanza como subproceso.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
LITELLM_CONFIG_PATH = REPO_ROOT / "litellm_config.yaml"

_default_proxy_url = "http://127.0.0.1:4000" if os.name == "nt" else "http://localhost:4000"
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", _default_proxy_url).strip().rstrip("/")
LITELLM_PORT = LITELLM_BASE_URL.rsplit(":", 1)[-1]

_process: Optional[subprocess.Popen] = None


def _litellm_executable() -> str:
    """Resuelve el ejecutable `litellm` (script de consola instalado por `litellm[proxy]`).

    `python -m litellm` no funciona: el paquete no tiene `__main__.py`, solo
    expone un entry-point de consola (`litellm.exe` / `litellm` junto al propio
    intérprete del venv).
    """
    scripts_dir = Path(sys.executable).parent
    candidate = scripts_dir / ("litellm.exe" if os.name == "nt" else "litellm")
    if candidate.exists():
        return str(candidate)
    return "litellm"  # fallback: confiar en PATH


def is_running() -> bool:
    try:
        resp = requests.get(f"{LITELLM_BASE_URL}/health/liveliness", timeout=1.5)
        return resp.status_code < 500
    except requests.RequestException:
        return False


def status() -> dict:
    return {
        "base_url": LITELLM_BASE_URL,
        "running": is_running(),
        "managed_by_pocit": _process is not None and _process.poll() is None,
    }


def start() -> dict:
    global _process

    if is_running():
        return status()

    if not LITELLM_CONFIG_PATH.exists():
        raise RuntimeError(f"No se encontró {LITELLM_CONFIG_PATH}")

    # LiteLLM imprime un banner con caracteres Unicode al arrancar. En Windows,
    # sin forzar UTF-8, Python usa la codificación de consola (cp1252) para los
    # streams del subproceso y el banner revienta con UnicodeEncodeError,
    # tumbando el startup del proxy incluso con stdout/stderr redirigidos.
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    _process = subprocess.Popen(
        [_litellm_executable(), "--config", str(LITELLM_CONFIG_PATH), "--port", LITELLM_PORT],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return status()


def stop() -> dict:
    global _process

    if _process is not None and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = None
    return status()
