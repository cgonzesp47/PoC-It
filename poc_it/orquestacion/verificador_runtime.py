from __future__ import annotations

import subprocess
from typing import Final

PYTHON_EXECUTABLE: Final[str] = "python"
IMPORT_CHECK_CMD: Final[list[str]] = [PYTHON_EXECUTABLE, "-c", "import app.main; print('IMPORT_OK')"]


def runtime_verify_fastapi_project(project_dir: str) -> tuple[bool, str]:
    """
    Verificación runtime mínima (genérica) para proyectos FastAPI generados.

    Objetivo:
    - Confirmar que el entrypoint `app.main:app` es importable SIN configuración externa.

    Importante:
    - No valida endpoints concretos (p.ej. /health) porque no siempre existirán.
    - No valida integraciones externas (Drive, DB, etc.). Solo valida "arranque/import-time".
    - Devuelve detalles ricos (stdout/stderr + hints) para repair loop.
    """
    p1 = subprocess.run(
        IMPORT_CHECK_CMD,
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if p1.returncode != 0:
        out = (p1.stdout or "") + "\n" + (p1.stderr or "")
        hint = (
            "HINTS:\n"
            "- Si el error es ValidationError/BaseSettings: estás validando settings en import-time; usa lazy get_settings().\n"
            "- Si el error es TypeError missing positional arguments: estás instanciando un servicio/clase en import-time sin pasar args; crea el servicio dentro del endpoint o con Depends.\n"
            "- Si el error es ImportError: estás importando un símbolo que no existe o tienes imports circulares.\n"
            "- Si el error es ModuleNotFoundError: falta una dependencia en requirements.txt.\n"
        )
        return False, f"[runtime_verify] import app.main failed:\n{out}\n{hint}"

    return True, "IMPORT_OK"
