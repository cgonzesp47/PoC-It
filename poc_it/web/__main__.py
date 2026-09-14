"""Punto de entrada: `python -m poc_it.web`.

Si el frontend (`ui/`) no está compilado todavía, se intenta compilar aquí
mismo (`npm install` + `npm run build`) para que este sea, de verdad, el
único comando necesario para levantar la aplicación completa.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "ui"
UI_DIST = UI_DIR / "dist"

HOST = os.getenv("POCIT_WEB_HOST", "127.0.0.1")
PORT = int(os.getenv("POCIT_WEB_PORT", "8000"))


def _ensure_frontend_built() -> None:
    if UI_DIST.exists() or not UI_DIR.exists():
        return

    npm = shutil.which("npm")
    if npm is None:
        print("[poc_it.web] 'npm' no está en el PATH: no se puede compilar 'ui/' automáticamente.")
        print("[poc_it.web] Instala Node.js o compílalo a mano: cd ui && npm install && npm run build")
        return

    if not (UI_DIR / "node_modules").exists():
        print("[poc_it.web] Instalando dependencias del frontend (primera vez)...")
        subprocess.run([npm, "install"], cwd=str(UI_DIR), check=True)

    print("[poc_it.web] Compilando frontend...")
    subprocess.run([npm, "run", "build"], cwd=str(UI_DIR), check=True)


def _open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    try:
        _ensure_frontend_built()
    except subprocess.CalledProcessError as exc:
        print(f"[poc_it.web] No se pudo compilar el frontend automáticamente: {exc}")
        print("[poc_it.web] El servidor arrancará igualmente exponiendo solo la API.")

    threading.Timer(1.5, _open_browser).start()
    uvicorn.run("poc_it.web.server:app", host=HOST, port=PORT, reload=False)
