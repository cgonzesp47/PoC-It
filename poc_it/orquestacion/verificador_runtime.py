from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

from poc_it.runtime.poc_runtime_environment import prepare_poc_runtime_environment

# IMPORTANTE:
# No podemos depender del intérprete que ejecuta PoC-it, porque el proyecto generado debe
# verificarse en un entorno hermético (otra máquina / runner limpio).
#
# Política:
# - `python_executable` debe venir del entorno aislado de la PoC (`.poc_it/venv`), resuelto por
#   `prepare_poc_runtime_environment` (poc_it.runtime.poc_runtime_environment), NUNCA de
#   `sys.executable`. Si un caller no lo pasa explícitamente, lo resolvemos aquí como fallback
#   best-effort para no romper compatibilidad, pero el camino recomendado es que el orquestador
#   prepare el entorno UNA VEZ y lo pase explícitamente a todas las fases runtime/tests.
PYTHON_EXECUTABLE: Final[str] = sys.executable


def _venv_python(project_dir: str) -> str:
    """Resuelve el python a usar para verificar el proyecto generado (fallback best-effort).

    Preferido: los callers deben pasar `python_executable` explícitamente (obtenido de
    `prepare_poc_runtime_environment`). Esta función solo cubre el caso en que no se pasó nada.
    """
    try:
        env = prepare_poc_runtime_environment(project_dir)
        if env.python_executable.exists():
            return str(env.python_executable)
    except Exception:
        pass
    return PYTHON_EXECUTABLE


def _cmd_import_main(project_dir: str, python_executable: str | None) -> list[str]:
    # IMPORTANTE: el script se ejecuta con cwd=project_dir, así que la ruta debe ser RELATIVA.
    # Si usamos absoluta aquí, en Windows se ha observado duplicación de segmentos al invocar python.
    py = python_executable or _venv_python(project_dir)
    return [py, r".poc_it\_poc_it_runtime_import_main.py"]


def _cmd_smoke_openapi(project_dir: str, python_executable: str | None) -> list[str]:
    # Igual que arriba: ruta relativa al cwd del subprocess.
    py = python_executable or _venv_python(project_dir)
    return [py, r".poc_it\_poc_it_runtime_smoke_openapi.py"]

_MISSING_MODULE_RE: Final[re.Pattern[str]] = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")
_REQUIREMENTS_PKG_RE: Final[re.Pattern[str]] = re.compile(r"^([a-zA-Z0-9_.-]+)")


def _run_cmd(cmd: list[str], *, cwd: str) -> tuple[bool, str]:
    # Al ejecutar scripts desde `.poc_it/`, Python pone ese directorio en sys.path[0].
    # Si no forzamos PYTHONPATH, `import app.main` puede fallar con:
    #   ModuleNotFoundError: No module named 'app'
    # porque el root del proyecto (cwd) no siempre se inyecta en sys.path.
    #
    # Solución: inyectar el project root como PYTHONPATH de forma explícita.
    env = dict(os.environ)
    current_pp = env.get("PYTHONPATH", "")
    # En Windows, PYTHONPATH necesita rutas absolutas para que el import sea robusto.
    # Usar cwd tal cual (relativo) no funciona cuando el proceso se lanza desde otro directorio.
    abs_cwd = str(Path(cwd).resolve())
    env["PYTHONPATH"] = (abs_cwd + (os.pathsep + current_pp if current_pp else "")).strip()

    # IMPORTANT: pasar solo el basename del script cuando ya usamos cwd=project_dir.
    # Si pasamos un path absoluto o relativo con prefijo project_dir, Python puede intentar
    # resolverlo como <cwd>/<path>, duplicando segmentos (observado en Windows):
    #   ...\\output\\<poc>\\output\\<poc>\\.poc_it\\_poc_it_runtime_import_main.py
    normalized_cmd = list(cmd)
    try:
        if len(normalized_cmd) >= 2 and isinstance(normalized_cmd[1], str):
            script = normalized_cmd[1]
            # si es un path y está dentro del cwd, reducimos a ruta relativa POSIX-safe
            try:
                script_path = Path(script)
                cwd_path = Path(cwd)
                if script_path.is_absolute():
                    try:
                        script_rel = script_path.relative_to(cwd_path)
                        normalized_cmd[1] = str(script_rel)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        normalized_cmd = cmd

    p = subprocess.run(normalized_cmd, cwd=cwd, capture_output=True, text=True, env=env)
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


def _ensure_probe_scripts(project_dir: str) -> None:
    """
    Crea/actualiza scripts de probe runtime dentro del proyecto generado.

    Importante:
    - Evitamos `python -c` porque el quoting puede romperse (especialmente en Windows + PowerShell/VSCode wrappers),
      causando falsos negativos.
    - Estos scripts se ejecutan con el python del venv del proyecto generado (si existe).
    - Se escriben bajo `.poc_it/` para no contaminar el proyecto publicado.
    """
    p = Path(project_dir)
    probe_dir = p / ".poc_it"
    probe_dir.mkdir(parents=True, exist_ok=True)

    (probe_dir / "_poc_it_runtime_import_main.py").write_text(
        "import app.main\nprint('IMPORT_MAIN_OK')\n",
        encoding="utf-8",
        errors="ignore",
    )

    (probe_dir / "_poc_it_runtime_smoke_openapi.py").write_text(
        "from fastapi.testclient import TestClient\n"
        "import app.main\n"
        "c = TestClient(app.main.app)\n"
        "r = c.get('/openapi.json')\n"
        "print('SMOKE_OPENAPI_OK', r.status_code)\n"
        "assert r.status_code < 500\n",
        encoding="utf-8",
        errors="ignore",
    )


def runtime_verify_fastapi_project(
    project_dir: str, spec: dict | None = None, *, python_executable: str | None = None
) -> tuple[bool, str]:
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
    - `python_executable`: intérprete del entorno aislado de la PoC (ver
      `poc_it.runtime.poc_runtime_environment.prepare_poc_runtime_environment`). Si no se pasa,
      se resuelve aquí best-effort, pero el caller debería prepararlo una única vez y pasarlo
      explícitamente a todas las fases runtime/tests.
    """
    _ensure_probe_scripts(project_dir)

    ok_main, out_main = _run_cmd(_cmd_import_main(project_dir, python_executable), cwd=project_dir)
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
        # Igual que arriba: evitamos -c, generando un script temporal.
        p = Path(project_dir) / ".poc_it" / "_poc_it_runtime_import_endpoints.py"
        p.write_text(
            "import app.main\n"
            + "\n".join(f"import {m}" for m in endpoint_modules)
            + "\nprint('IMPORT_ENDPOINTS_OK')\n",
            encoding="utf-8",
            errors="ignore",
        )
        cmd = [python_executable or _venv_python(project_dir), r".poc_it\_poc_it_runtime_import_endpoints.py"]
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
    ok_smoke, out_smoke = _run_cmd(_cmd_smoke_openapi(project_dir, python_executable), cwd=project_dir)
    if not ok_smoke:
        hint = (
            "HINTS:\n"
            "- Si el error es TypeError missing positional arguments: probablemente estás llamando un método de instancia como clase.\n"
            "- Si el error es AttributeError en sesión/cliente: Depends está inyectando el tipo incorrecto.\n"
            "- Si el error es ValidationError: el modelo request/response no coincide con el handler.\n"
        )
        return False, f"[runtime_verify] smoke openapi request failed:\n{out_smoke}\n{hint}"

    return True, "IMPORT_OK"
