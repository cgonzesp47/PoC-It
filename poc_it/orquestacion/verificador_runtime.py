from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Final

# IMPORTANTE: usar el MISMO intérprete que ejecuta PoC-it.
# En Windows, invocar "python" puede resolver a otro Python/venv por PATH/launcher.
PYTHON_EXECUTABLE: Final[str] = sys.executable
IMPORT_MAIN_CMD: Final[list[str]] = [PYTHON_EXECUTABLE, "-c", "import app.main; print('IMPORT_MAIN_OK')"]

# Smoke básico: importa app.main y ejecuta un request mínimo a /openapi.json.
# Esto captura bugs típicos de wiring que NO aparecen en import-time (p.ej. usar métodos de instancia como estáticos,
# Depends mal cableados, etc.).
SMOKE_OPENAPI_CMD: Final[list[str]] = [
    PYTHON_EXECUTABLE,
    "-c",
    "from fastapi.testclient import TestClient; import app.main; c=TestClient(app.main.app); r=c.get('/openapi.json'); print('SMOKE_OPENAPI_OK', r.status_code); assert r.status_code < 500",
]

_MISSING_MODULE_RE: Final[re.Pattern[str]] = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")
_REQUIREMENTS_PKG_RE: Final[re.Pattern[str]] = re.compile(r"^([a-zA-Z0-9_.-]+)")


def _run_cmd(cmd: list[str], *, cwd: str) -> tuple[bool, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode == 0:
        return True, (p.stdout or "").strip() or "OK"

    out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
    return False, out


def _extract_missing_module(runtime_detail: str) -> str | None:
    if not runtime_detail:
        return None
    m = _MISSING_MODULE_RE.search(runtime_detail)
    if not m:
        return None
    return (m.group(1) or "").strip() or None


def _requirements_declares_module(requirements_txt: str, module_name: str) -> bool:
    """
    Heurística pragmática: si falta un módulo, aceptamos que esté "declarado"
    si requirements contiene un paquete cuyo nombre base coincide.
    (No resolvemos mapping módulo->paquete; sería demasiado costoso y frágil.)
    """
    if not requirements_txt or not module_name:
        return False
    base = module_name.strip().lower()
    for line in requirements_txt.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _REQUIREMENTS_PKG_RE.match(s)
        if not m:
            continue
        if m.group(1).strip().lower() == base:
            return True
    return False


def _spec_declares_module(spec: dict | None, module_name: str) -> bool:
    if not isinstance(spec, dict) or not module_name:
        return False
    deps = spec.get("dependencies")
    if not isinstance(deps, list):
        return False
    base = module_name.strip().lower()
    return any(str(d).strip().lower() == base for d in deps if str(d).strip())


def _extract_endpoint_modules_from_spec(spec: dict | None) -> list[str]:
    """
    Extrae módulos a importar a partir del SPEC.
    - Por cada endpoint, tomamos endpoints[*].file y lo convertimos a módulo Python.
    - Solo consideramos ficheros Python bajo app/.
    """
    if not isinstance(spec, dict):
        return []

    eps = spec.get("endpoints")
    if not isinstance(eps, list):
        return []

    modules: list[str] = []
    for ep in eps:
        if not isinstance(ep, dict):
            continue
        file_path = str(ep.get("file") or "").replace("\\", "/").strip()
        if not file_path.endswith(".py") or not file_path.startswith("app/"):
            continue
        mod = file_path[:-3].replace("/", ".")  # app/api/products.py -> app.api.products
        modules.append(mod)

    # uniq + orden estable
    seen: set[str] = set()
    out: list[str] = []
    for m in modules:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def runtime_verify_fastapi_project(project_dir: str, spec: dict | None = None) -> tuple[bool, str]:
    """
    Verificación runtime mínima (genérica) para proyectos FastAPI generados.

    Objetivo:
    - Confirmar que el entrypoint `app.main:app` es importable SIN configuración externa.
    - Evitar bypass: si el SPEC declara endpoints, sus módulos deben ser importables.
      (si no, fallos como `logger`/`Session` indefinidos pueden quedar ocultos comentando routers).

    Importante:
    - No valida endpoints concretos (p.ej. /health) porque no siempre existirán.
    - No valida integraciones externas (Drive, DB, etc.). Solo valida "arranque/import-time".
    - Devuelve detalles ricos (stdout/stderr + hints) para repair loop.
    """
    ok_main, out_main = _run_cmd(IMPORT_MAIN_CMD, cwd=project_dir)
    if not ok_main:
        missing = _extract_missing_module(out_main)
        if missing:
            # El pipeline actual NO instala dependencias antes de verificar import-time.
            # Para evitar falsos negativos, toleramos ModuleNotFoundError si la dependencia
            # está declarada en requirements.txt o en spec.dependencies.
            req_txt = ""
            try:
                req_path = Path(project_dir) / "requirements.txt"
                if req_path.exists():
                    req_txt = req_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                req_txt = ""

            if _requirements_declares_module(req_txt, missing) or _spec_declares_module(spec, missing):
                return True, f"IMPORT_OK_WITH_MISSING_DEP:{missing}"

        hint = (
            "HINTS:\n"
            "- Si el error es ValidationError/BaseSettings: estás validando settings en import-time; usa lazy get_settings().\n"
            "- Si el error es TypeError missing positional arguments: estás instanciando un servicio/clase en import-time sin pasar args; crea el servicio dentro del endpoint o con Depends.\n"
            "- Si el error es ImportError: estás importando un símbolo que no existe o tienes imports circulares.\n"
            "- Si el error es ModuleNotFoundError: falta una dependencia en requirements.txt.\n"
        )
        return False, f"[runtime_verify] import app.main failed:\n{out_main}\n{hint}"

    # Extra: si el SPEC declara endpoints, forzamos import de sus módulos para no esconder errores.
    endpoint_modules = _extract_endpoint_modules_from_spec(spec)
    if endpoint_modules:
        # importamos todos en un solo intérprete para obtener un único traceback accionable
        imports = "; ".join(f"import {m}" for m in endpoint_modules)
        cmd = [PYTHON_EXECUTABLE, "-c", f"import app.main; {imports}; print('IMPORT_ENDPOINTS_OK')"]
        ok_eps, out_eps = _run_cmd(cmd, cwd=project_dir)
        if not ok_eps:
            missing = _extract_missing_module(out_eps)
            if missing:
                req_txt = ""
                try:
                    req_path = Path(project_dir) / "requirements.txt"
                    if req_path.exists():
                        req_txt = req_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    req_txt = ""

                if _requirements_declares_module(req_txt, missing) or _spec_declares_module(spec, missing):
                    return True, f"IMPORT_OK_WITH_MISSING_DEP:{missing}"

            hint = (
                "HINTS:\n"
                "- Si el error es NameError (p.ej. logger/Session no definidos): falta un import o una inicialización.\n"
                "- Si el error es ImportError: from-import apunta a un símbolo inexistente.\n"
                "- Si el error es ModuleNotFoundError: falta dependencia o __init__.py.\n"
                "- Evita comentar routers/imports en main para 'pasar' el check: los endpoints declarados deben ser importables.\n"
            )
            return False, f"[runtime_verify] endpoints modules import failed:\nModules={endpoint_modules}\n{out_eps}\n{hint}"

    # Smoke runtime adicional: /openapi.json con TestClient.
    ok_smoke, out_smoke = _run_cmd(SMOKE_OPENAPI_CMD, cwd=project_dir)
    if not ok_smoke:
        hint = (
            "HINTS:\n"
            "- Si el error es TypeError missing positional arguments: probablemente estás llamando un método de instancia como clase.\n"
            "- Si el error es AttributeError en sesión/cliente: Depends está inyectando el tipo incorrecto.\n"
            "- Si el error es ValidationError: el modelo request/response no coincide con el handler.\n"
        )
        return False, f"[runtime_verify] smoke openapi request failed:\n{out_smoke}\n{hint}"

    return True, "IMPORT_OK"
