from __future__ import annotations
"""
Bucle de reparación guiado por pytest + LLM (iterativo).

Objetivo
--------
Tras generar tests, ejecutar pytest en el proyecto materializado. Si falla:
- recopilar el output (stdout+stderr)
- pedir al LLM un patch SOLO sobre archivos de tests (tests/*.py, pytest.ini)
- aplicar patch
- repetir hasta que pytest pase o se alcance un máximo de intentos

Este bucle es intencionalmente conservador:
- Nunca modifica código de la app (app/**/*.py)
- Incluye compuerta de compilación (compileall tests) para evitar dejar la suite rota.
- Puede combinarse con fixers deterministas previos (pytest_fixers.py) si el caller lo desea.
"""

import json
import logging
import os
import re
import subprocess
import inspect
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from poc_it.generador.json_utils import extraer_json_tolerante
from poc_it.llm_client import chat_completion_json
from poc_it.materializador_archivos import materializar_proyecto
from poc_it.orquestacion.pytest_failure_classifier import classify_pytest_failure
# Nota: fixers deterministas desactivados por estrategia.
# La fase C debe ser principalmente LLM-guided repair sobre tests; los fixers deterministas
# se mantienen solo como legacy (se eliminarán tras limpieza controlada).
try:
    from poc_it.orquestacion.pytest_fixers import apply_first_matching_fixer  # legacy
except Exception:  # pragma: no cover
    apply_first_matching_fixer = None  # type: ignore
from poc_it.orquestacion.pytest_llm_prompts import build_prompt_c1_harness_repair, build_prompt_c2_asserts_repair
from poc_it.orquestacion.repair_trace import TraceEvent, append_trace_event, now_ts
from poc_it.orquestacion.test_repair_context import TEST_REPAIR_CONTEXT_PATH, build_test_repair_context
from poc_it.orquestacion.override_repair_llm import generate_or_repair_conftest_overrides_with_llm

logger = logging.getLogger(__name__)

_PYTEST_SUMMARY_RE = re.compile(
    r"(?:(?P<passed>\d+)\s+passed[,\s]*)?"
    r"(?:(?P<failed>\d+)\s+failed[,\s]*)?"
    r"(?:(?P<errors>\d+)\s+error(?:s)?[,\s]*)?"
    r"(?:(?P<skipped>\d+)\s+skipped[,\s]*)?"
    r"(?:(?P<xfailed>\d+)\s+xfailed[,\s]*)?"
    r"(?:(?P<xpassed>\d+)\s+xpassed[,\s]*)?"
    r"(?:in\s+\d+\.\d+s)?",
    re.IGNORECASE,
)


def _extract_pytest_counts(pytest_output: str) -> Tuple[int, int]:
    """Extrae (errors, failed) best-effort de la salida de pytest.

    Requisitos del loop
    -------------------
    - Nunca devolver (0,0) si hay FAILURES/ERRORS reales.
    - Contar E y F de forma independiente (p.ej. `EEFF.. [100%]` => (2,2)).
    - Funcionar aunque el resumen final no aparezca o esté truncado.

    Estrategia
    ----------
    1) Resumen estándar (tail).
    2) Token de progreso tipo `EEFF.. [100%]` / `F.E.` en cualquier línea.
    3) Heurística por secciones/líneas "FAILED"/"ERROR" como último recurso.
    """
    if not pytest_output:
        return 999, 999

    txt = pytest_output or ""
    lines = txt.splitlines()
    tail = "\n".join(lines[-120:])

    # (1) Resumen estándar
    m = None
    for mm in _PYTEST_SUMMARY_RE.finditer(tail):
        m = mm
    if m:

        def to_i(x: Optional[str]) -> int:
            try:
                return int(x) if x is not None and str(x).strip() else 0
            except Exception:
                return 0

        errors = to_i(m.group("errors"))
        failed = to_i(m.group("failed"))

        # Safety: si el summary está roto pero hay secciones, no devolver (0,0)
        if errors == 0 and failed == 0:
            has_fail = "FAILURES" in txt
            has_err = ("ERRORS" in txt) or ("ERROR at setup" in txt) or ("ERROR collecting" in txt)
            if has_fail or has_err:
                return (1 if has_err else 0), (1 if has_fail else 0)

        return errors, failed

    # (2) Token compacto de progreso (cuenta E y F independientemente)
    #
    # Nota: en `-q` el token puede venir seguido de muchos espacios ANTES de "[100%]":
    #   "EEEEFF                                                                   [100%]"
    # Por eso:
    # - primero capturamos un grupo inicial SOLO de [E/F/.]
    # - ignoramos el resto de la línea
    for ln in lines[:600]:
        s = (ln or "").strip()
        if not s:
            continue

        mprog = re.match(r"^(?P<tok>[EF\.]{2,})\b", s)
        if not mprog:
            continue

        tok = (mprog.group("tok") or "").strip()
        if tok:
            return tok.count("E"), tok.count("F")

    # (3) Heurística: evitar (0,0) cuando hay FAILURES/ERRORS
    has_fail = "FAILURES" in txt
    has_err = ("ERRORS" in txt) or ("ERROR at setup" in txt) or ("ERROR collecting" in txt)

    failed_lines = len(re.findall(r"^FAILED\s+", txt, re.MULTILINE))
    error_lines = len(re.findall(r"^ERROR\s+", txt, re.MULTILINE))

    errors = error_lines if error_lines > 0 else (1 if has_err else 0)
    failed = failed_lines if failed_lines > 0 else (1 if has_fail else 0)

    if errors == 0 and failed == 0 and (has_fail or has_err):
        errors = 1 if has_err else 0
        failed = 1 if has_fail else 0

    if errors or failed:
        return errors, failed

    return 999, 999


def _degrade_to_contract_lite(*, nombre_proyecto: str, estructura: Dict[str, str]) -> Dict[str, str]:
    """Degrada a suite mínima que debe pasar siempre: smoke import + OpenAPI.

    Política:
    - Usar exactamente el mismo "contract-lite" para cualquier modo cuando se decide degradar.
    - Eliminar tests potencialmente frágiles (spec/hermetic) para evitar contaminación del run.
    """
    import os

    # Limpieza dura: eliminar todos los tests existentes (evita que pytest recoja residuales).
    try:
        tests_dir = os.path.join("output", nombre_proyecto, "tests")
        if os.path.isdir(tests_dir):
            for fn in os.listdir(tests_dir):
                if fn.endswith(".py") or fn.endswith(".pyc"):
                    try:
                        os.remove(os.path.join(tests_dir, fn))
                    except Exception:
                        pass
    except Exception:
        pass

    pytest_ini = """[pytest]
addopts = -q
testpaths = tests
python_files = test_smoke_import.py test_openapi.py
"""
    smoke_import = """def test_import_app_main():
    import importlib
    mod = importlib.import_module("app.main")
    assert mod is not None
"""
    openapi_test = """from fastapi.testclient import TestClient
from app.main import app


def test_openapi_json_available():
    with TestClient(app) as client:
        resp = client.get("/openapi.json")
    assert resp.status_code < 500
    data = resp.json()
    assert "paths" in data
"""
    patch = {
        "pytest.ini": pytest_ini,
        "tests/__init__.py": "",
        "tests/test_smoke_import.py": smoke_import,
        "tests/test_openapi.py": openapi_test,
    }
    materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch, limpiar_directorio=False)
    estructura.update(patch)
    return patch


@dataclass(frozen=True)
class PytestRepairResult:
    ok: bool
    attempts: int
    last_output: str
    patched_files: Dict[str, str]

    # Señales estructuradas (fuente de verdad para estado/publish)
    degraded: bool = False
    degrade_type: Optional[str] = None

    # Artefactos (paths relativos dentro del proyecto) útiles para diagnóstico
    artifacts: Dict[str, str] = field(default_factory=dict)


def _run_pytest(project_dir: str) -> Tuple[bool, str, str]:
    """
    Ejecuta pytest y genera un reporte estructurado mediante JUnit XML (built-in) + JSON report (pytest-json-report).

    Importante (Windows):
    - Hemos observado fallos en `pytest_sessionfinish` (plugin junitxml) cuando el path del XML
      se resuelve mal (rutas duplicadas / relativas raras), provocando:
        FileNotFoundError ... _pytest/junitxml.py
      Eso NO es un fallo de tests ni de app, sino de instrumentación.
    - Para hacerlo robusto:
      1) usamos ruta ABSOLUTA normalizada para --junitxml
      2) si aun así falla por junitxml, reintentamos SIN junitxml (fallback) para no bloquear el loop
    """
    report_dir = os.path.abspath(os.path.join(project_dir, ".poc_it"))
    os.makedirs(report_dir, exist_ok=True)
    junit_path = os.path.normpath(os.path.join(report_dir, "pytest_junit.xml"))
    json_path = os.path.normpath(os.path.join(report_dir, "pytest_report.json"))

    # Intento 1: con junitxml + json-report (preferido)
    base_cmd = [
        "python",
        "-m",
        "pytest",
        "-q",
        f"--junitxml={junit_path}",
        "--json-report",
        f"--json-report-file={json_path}",
    ]
    p = subprocess.run(
        base_cmd,
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    out = (p.stdout or "") + "\n" + (p.stderr or "")

    if p.returncode == 0:
        return True, out, junit_path

    # Fallback: si junitxml rompe en sessionfinish, reintentar sin junitxml.
    out_low = (out or "").lower()
    if ("junitxml.py" in out_low or "pytest_sessionfinish" in out_low) and ("filenotfounderror" in out_low or "winerror 3" in out_low):
        p2 = subprocess.run(
            ["python", "-m", "pytest", "-q", "--json-report", f"--json-report-file={json_path}"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        out2 = (p2.stdout or "") + "\n" + (p2.stderr or "")
        return p2.returncode == 0, out2, ""

    return False, out, junit_path


def _read_pytest_junit_xml(project_dir: str) -> Optional[str]:
    try:
        rp = os.path.join(project_dir, ".poc_it", "pytest_junit.xml")
        if not os.path.exists(rp):
            return None
        with open(rp, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _extract_counts_from_junit_xml(xml_text: Optional[str]) -> Optional[Tuple[int, int, int, int, int]]:
    """
    Devuelve (errors, failed, passed, skipped, total) a partir de JUnit XML.

    Implementación:
    - Primero intenta leer atributos agregados de <testsuite errors=".." failures=".." tests=".." skipped="..">.
    - Si no están, cae a conteo por <testcase> (presence of <failure>/<error>/<skipped>).
    """
    if not isinstance(xml_text, str) or not xml_text.strip():
        return None

    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)

        def _to_i(v: object) -> int:
            try:
                return int(v)
            except Exception:
                return 0

        # pytest suele emitir <testsuites> root con 1..N <testsuite>
        suites: List[ET.Element] = []
        if root.tag.endswith("testsuite"):
            suites = [root]
        else:
            suites = [el for el in root.findall(".//testsuite")]

        if suites:
            # agregación por atributos (si existen)
            tests = sum(_to_i(s.get("tests")) for s in suites)
            failures = sum(_to_i(s.get("failures")) for s in suites)
            errors = sum(_to_i(s.get("errors")) for s in suites)
            skipped = sum(_to_i(s.get("skipped")) for s in suites)

            # Si no hay tests agregados, hacemos conteo por testcase
            if tests == 0:
                cases = root.findall(".//testcase")
                tests = len(cases)
                failures = 0
                errors = 0
                skipped = 0
                for tc in cases:
                    if tc.find("failure") is not None:
                        failures += 1
                    elif tc.find("error") is not None:
                        errors += 1
                    elif tc.find("skipped") is not None:
                        skipped += 1

            passed = max(0, tests - failures - errors - skipped)
            return errors, failures, passed, skipped, tests

        return None
    except Exception:
        return None


def _compile_tests(project_dir: str) -> bool:
    try:
        c = subprocess.run(
            ["python", "-m", "compileall", "-q", "tests"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        return c.returncode == 0
    except Exception:
        return False


def _normalize_patch_files(files: object) -> Dict[str, str]:
    if not isinstance(files, list):
        return {}
    out: Dict[str, str] = {}
    for it in files:
        if not isinstance(it, dict):
            continue
        path = str(it.get("path") or "").replace("\\", "/").strip()
        content = it.get("content")
        if not path or not isinstance(content, str):
            continue
        # Permitimos que el LLM parche el harness cuando está roto (C1).
        # Aún así, restringimos a tests/ + pytest.ini + requirements-dev.txt.
        if path in ("pytest.ini", "requirements-dev.txt"):
            out[path] = content
            continue
        if not path.startswith("tests/"):
            continue
        # permitir conftest/stubs (pero su uso se restringe adicionalmente por fase + escape hatch)
        if path in ("tests/conftest.py", "tests/stubs.py"):
            out[path] = content
            continue
        # permitir solo archivos de test (evita que el LLM \"re-arquitecte\" el harness)
        if not path.startswith("tests/test_") or not path.endswith(".py"):
            continue
        out[path] = content
    return out


_DEP_OVERRIDES_SYMBOL_RE = re.compile(
    r"dependency_overrides\s*\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\.",
    re.MULTILINE,
)
_IMPORTS_SYMBOL_RE = re.compile(
    r"^(?:from\s+[\w\.]+\s+import\s+|import\s+)(?P<sym>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)

_IMPORT_CALLABLE_FQN_RE = re.compile(r"_import_callable\(\s*['\"]([^'\"]+)['\"]\s*\)", re.MULTILINE)
_IMPORTLIB_IMPORT_MODULE_RE = re.compile(r"import_module\(\s*['\"]([^'\"]+)['\"]\s*\)", re.MULTILINE)
_FROM_IMPORT_RE = re.compile(r"^\s*from\s+([A-Za-z_][\w\.]*)\s+import\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)

_CALLED_FIXTURE_DIRECTLY_RE = re.compile(r'Fixture "([^"]+)" called directly', re.MULTILINE)


def _gate_patch_tests_no_undefined_override_symbols(patch: Dict[str, str]) -> Tuple[bool, str]:
    """Gate pragmático anti-alucinación.

    Detecta el caso típico: `app.dependency_overrides[Foo.bar] = ...` pero
    `Foo` no está importado en el módulo de test.

    No resuelve Python en general; solo evita un fallo frecuente y transversal.
    """
    for path, content in (patch or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/") or not path.endswith(".py"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        # symbols usados como X.<attr> dentro de dependency_overrides[...]
        used = set(_DEP_OVERRIDES_SYMBOL_RE.findall(content) or [])
        if not used:
            continue

        imported = set()
        for m in _IMPORTS_SYMBOL_RE.finditer(content[:4000]):
            sym = (m.group("sym") or "").strip()
            if sym:
                imported.add(sym)

        missing = sorted(s for s in used if s not in imported)
        if missing:
            return False, f"Undefined dependency_overrides symbol(s) not imported in {path}: {missing}"

    return True, "ok"


def _gate_patch_tests_override_fqns_in_allowlist(
    patch: Dict[str, str],
    *,
    runtime_contracts: Optional[dict],
    test_ctx: dict,
) -> Tuple[bool, str]:
    """Hard constraint: evita wiring inventado.

    Enforce:
    - cualquier FQN usado en `_import_callable("...")` debe estar en allowed_dependency_overrides.
    - cualquier `import_module("...")` debe ser un módulo permitido (app.main o módulo de un FQN permitido).
    """
    allow: set[str] = set()
    try:
        for x in (runtime_contracts or {}).get("allowed_dependency_overrides") or []:
            s = str(x).strip()
            if s:
                allow.add(s)
    except Exception:
        allow = set()

    if not allow:
        try:
            for x in (test_ctx or {}).get("allowed_dependency_overrides") or []:
                s = str(x).strip()
                if s:
                    allow.add(s)
        except Exception:
            allow = set()

    if not allow:
        return True, "ok"

    allow_modules = {a.rsplit(".", 1)[0] for a in allow}
    allow_modules |= {"app", "app.main"}

    bad: List[str] = []

    for path, content in (patch or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/") or not path.endswith(".py"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        for fqn in _IMPORT_CALLABLE_FQN_RE.findall(content):
            fqn = str(fqn).strip()
            if fqn and fqn not in allow:
                bad.append(f"{path}:_import_callable({fqn})")

        for mod in _IMPORTLIB_IMPORT_MODULE_RE.findall(content):
            mod = str(mod).strip()
            if not mod:
                continue
            if mod not in allow_modules and not mod.startswith("tests."):
                bad.append(f"{path}:import_module({mod})")

        for m in _FROM_IMPORT_RE.finditer(content):
            mod = (m.group(1) or "").strip()
            sym = (m.group(2) or "").strip()
            if not mod or not sym:
                continue
            fqn = f"{mod}.{sym}"
            if mod.startswith("app.") and any(k in mod for k in (".db", ".database", ".dependencies", ".deps", ".services")):
                if fqn not in allow and mod not in allow_modules:
                    bad.append(f"{path}:from {mod} import {sym}")

    if bad:
        return False, f"Override/import FQNs not in allowed_dependency_overrides: {bad[:8]}"

    return True, "ok"


def _is_async_callable(obj: object) -> bool:
    try:
        return inspect.iscoroutinefunction(obj) or inspect.isasyncgenfunction(obj)
    except Exception:
        return False


def _gate_patch_tests_override_asyncness_matches_runtime(
    patch: Dict[str, str],
    *,
    runtime_contracts: Optional[dict],
) -> Tuple[bool, str]:
    """Hard-ish constraint: si un overridea un dep async, el override debe ser async; si es sync, debe ser sync.

    Best-effort: buscamos patrones en conftest:
      dep = _import_callable("a.b.c")
      async def _override_*(): ...
      app.dependency_overrides[dep] = _override_*
    """
    if not runtime_contracts:
        return True, "ok"

    try:
        deps = (runtime_contracts or {}).get("dependencies") or {}
    except Exception:
        deps = {}

    # dependencies puede no existir; fallback a no gatear.
    if not isinstance(deps, dict) or not deps:
        return True, "ok"

    # solo miramos conftest/stubs para evitar falsos positivos
    for path, content in (patch or {}).items():
        if path not in ("tests/conftest.py", "tests/stubs.py"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        # map local var -> fqn
        local_dep_vars: Dict[str, str] = {}
        for m in re.finditer(r"^\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*_import_callable\(\s*['\"](?P<fqn>[^'\"]+)['\"]\s*\)\s*$", content, re.MULTILINE):
            local_dep_vars[m.group("var")] = m.group("fqn")

        # map override fn name -> async?
        override_async: Dict[str, bool] = {}
        for m in re.finditer(r"^\s*(async\s+def|def)\s+(?P<fn>[A-Za-z_][A-Za-z0-9_]*)\s*\(", content, re.MULTILINE):
            is_async = m.group(1).startswith("async")
            override_async[m.group("fn")] = is_async

        # check assignments
        for m in re.finditer(
            r"dependency_overrides\s*\[\s*(?P<depvar>[A-Za-z_][A-Za-z0-9_]*)\s*\]\s*=\s*(?P<fn>[A-Za-z_][A-Za-z0-9_]*)",
            content,
            re.MULTILINE,
        ):
            depvar = m.group("depvar")
            fn = m.group("fn")
            fqn = local_dep_vars.get(depvar)
            if not fqn:
                continue
            dep_info = deps.get(fqn)
            if not isinstance(dep_info, dict):
                continue
            dep_is_async = bool(dep_info.get("is_async"))
            fn_is_async = bool(override_async.get(fn))
            if dep_is_async != fn_is_async:
                return (
                    False,
                    f"Override asyncness mismatch for {fqn}: runtime is_async={dep_is_async} but override `{fn}` is_async={fn_is_async}.",
                )

    return True, "ok"


def _fingerprint_from_pytest_output(pytest_output: str) -> str:
    """Hash estable del primer error/failure para detectar progreso aunque E/F no cambie.

    En F-only, el token `FFFFF` no cambia; por eso añadimos señal del primer failure (header + primera línea E   ...).
    """
    if not pytest_output:
        return ""
    head = (pytest_output or "").splitlines()

    pick: List[str] = []
    basis = "unknown"

    # (A) Progress token: `EEEEE. [100%]` / `FFFFF [100%]` / `F.E.`
    for ln in head[:300]:
        s = (ln or "").strip()
        if not s:
            continue
        token = re.sub(r"\s*\[.*?\]\s*$", "", s).strip()
        token = re.sub(r"\s+", "", token)
        if token and re.fullmatch(r"[EF\.]{2,}", token):
            pick.append(f"PROGRESS={token}")
            basis = "progress_token"
            break

    # (B) Header del primer fallo (nodeid-ish)
    for ln in head[:600]:
        s = (ln or "").strip()
        if not s:
            continue
        if s.startswith("________________") and "test_" in s:
            pick.append(f"HDR={s[:220]}")
            if basis == "unknown":
                basis = "section_header"
            break

    # (C) Primera línea E   ...
    for ln in head[:1200]:
        s = (ln or "").rstrip()
        if not s:
            continue
        if s.strip().startswith("E   "):
            pick.append(s.strip()[:280])
            basis = "first_E_line"
            break
        if "ERROR at setup" in s or "FAILURES" in s or "ERRORS" in s:
            pick.append(s.strip()[:200])
            if basis == "unknown":
                basis = "section_header"

    blob = ("\n".join(pick) + f"\nBASIS={basis}")[:1400]
    return hashlib.sha1(blob.encode("utf-8", errors="ignore")).hexdigest()


_JSON_EXACT_EQUALITY_RE = re.compile(r"assert\s+response\.json\(\)\s*==", re.MULTILINE)
_ASYNC_TEST_DEF_RE = re.compile(r"^\s*async\s+def\s+test_", re.MULTILINE)
_AWAIT_CLIENT_CALL_RE = re.compile(r"await\s+client\.(get|post|put|patch|delete)\s*\(", re.MULTILINE)
_TESTCLIENT_IMPORT_RE = re.compile(r"from\s+fastapi\.testclient\s+import\s+TestClient", re.MULTILINE)
_CLIENT_GET_JSON_RE = re.compile(r"\bclient\.get\s*\([^)]*?\bjson\s*=", re.MULTILINE | re.DOTALL)

# Anti-permisividad: bloquear asserts de status_code con sets demasiado amplios.
# Ejemplos prohibidos:
# - `assert response.status_code in (200, 201, 404, 405, 422)`
# - `assert response.status_code in {200,201,404,405,422}`
# - `assert response.status_code in [200, 201, 404, 405, 422]`
_STATUSCODE_IN_WIDE_SET_RE = re.compile(
    r"assert\s+response\.status_code\s+in\s*[\(\[\{]\s*([0-9\s,]+)\s*[\)\]\}]",
    re.MULTILINE,
)

# Best-effort: detectar returns de dict literal en stubs (Mock/Fake services)
_RETURN_DICT_RE = re.compile(r"return\s+\{([^}]+)\}", re.MULTILINE | re.DOTALL)
_DICT_KEY_RE = re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*:", re.MULTILINE)


def _gate_patch_tests_no_async_tests_with_testclient(patch: Dict[str, str]) -> Tuple[bool, str]:
    # Regla: si hay TestClient, los tests deben ser sync y sin `await client.*`
    for path, content in (patch or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/") or not path.endswith(".py"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if _TESTCLIENT_IMPORT_RE.search(content):
            if _ASYNC_TEST_DEF_RE.search(content):
                return False, f"Async tests with TestClient are forbidden in {path} (remove async from test_*)."
            if _AWAIT_CLIENT_CALL_RE.search(content):
                return False, f"Awaiting TestClient requests is forbidden in {path} (remove await; tests must be sync)."
    return True, "ok"


def _gate_patch_tests_no_client_get_json_kw(patch: Dict[str, str]) -> Tuple[bool, str]:
    for path, content in (patch or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/") or not path.endswith(".py"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if _CLIENT_GET_JSON_RE.search(content):
            return False, f"Starlette TestClient.get(json=...) is forbidden in {path} (use params= or client.request('GET', ..., json=...))."
    return True, "ok"


def _gate_patch_tests_no_exact_response_json_equality(
    patch: Dict[str, str],
    *,
    allow_in_conftest: bool = True,
) -> Tuple[bool, str]:
    """Evita el antipatrón `assert response.json() == {...}`.

    Nota: este gate está pensado para evitar que el LLM congele contratos por igualdad exacta,
    pero NO debe impedir arreglar el harness. Por eso, opcionalmente ignoramos conftest.
    """
    for path, content in (patch or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/") or not path.endswith(".py"):
            continue
        if allow_in_conftest and path == "tests/conftest.py":
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if _JSON_EXACT_EQUALITY_RE.search(content):
            return False, f"Fragile assert detected in {path}: `assert response.json() == ...` (use subset asserts)"
    return True, "ok"


def _gate_patch_tests_no_wide_status_code_sets(
    patch: Dict[str, str],
    *,
    phase: str,
) -> Tuple[bool, str]:
    """En C2 prohíbe que el LLM 'arregle' rompiendo el valor del test.

    No intentamos derivar el código correcto aquí (eso lo manda OpenAPI/policy del prompt),
    solo bloqueamos el antipatrón de "aceptar casi cualquier cosa".
    """
    if phase != "C2":
        return True, "ok"

    for path, content in (patch or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/") or not path.endswith(".py"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        for m in _STATUSCODE_IN_WIDE_SET_RE.finditer(content):
            nums = m.group(1) or ""
            codes = []
            for x in nums.split(","):
                x = x.strip()
                if not x:
                    continue
                try:
                    codes.append(int(x))
                except Exception:
                    continue
            # Wide-set heuristic: >= 4 códigos distintos => demasiado permisivo
            if len(set(codes)) >= 4:
                return False, f"Overly permissive status_code set in {path}: {sorted(set(codes))} (forbidden in C2)."

    return True, "ok"


def _gate_patch_tests_stub_returns_required_keys(
    patch: Dict[str, str],
    *,
    test_ctx: dict,
) -> Tuple[bool, str]:
    # Enforce: si el contexto sabe required keys del response_model, los stubs no pueden devolver dict incompleto.
    # Best-effort (no AST): buscamos "return { ... }" en tests y comparamos keys.
    required_union = set()
    try:
        for ep in (test_ctx or {}).get("endpoints") or []:
            if not isinstance(ep, dict):
                continue
            for k in ep.get("response_json_required_keys") or []:
                s = str(k).strip()
                if s:
                    required_union.add(s)
    except Exception:
        required_union = set()

    if not required_union:
        return True, "ok"

    # reducimos a claves típicas para evitar falsos positivos con respuestas no-json
    # (id/nombre/precio/disponible/descripcion suelen ser las relevantes)
    focus = {k for k in required_union if k in {"id", "nombre", "precio", "disponible", "descripcion", "name", "price", "available", "description"}}
    if not focus:
        focus = required_union

    for path, content in (patch or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/") or not path.endswith(".py"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        for m in _RETURN_DICT_RE.finditer(content):
            body = m.group(1) or ""
            keys = set(_DICT_KEY_RE.findall(body) or [])
            # Si devuelve id/nombre pero falta alguna clave clave del response_model, probablemente romperá response_model
            if ("id" in keys or "nombre" in keys or "name" in keys) and (focus - keys):
                missing = sorted(list(focus - keys))[:10]
                return (
                    False,
                    f"Stub return dict in {path} seems missing required response keys {missing}. Ensure stubs return all required fields of response_model to avoid ResponseValidationError.",
                )

    return True, "ok"


def _extract_called_fixture_name(pytest_output: str) -> Optional[str]:
    if not pytest_output:
        return None
    m = _CALLED_FIXTURE_DIRECTLY_RE.search(pytest_output)
    if not m:
        return None
    name = (m.group(1) or "").strip()
    return name or None


def _build_prompt_repair_tests(
    *,
    pytest_output: str,
    current_tests: Dict[str, str],
    runtime_contracts: Optional[dict] = None,
    runtime_facts: Optional[dict] = None,
) -> str:
    # Limitar tokens: pasamos solo tests actuales (no la app) y el output del fallo.
    # Importante: conftest.py es la fuente de verdad del harness, y debe estar visible.
    files = [{"path": p, "content": c} for p, c in sorted(current_tests.items())]

    conftest_txt = current_tests.get("tests/conftest.py") or ""
    if conftest_txt and not any(f.get("path") == "tests/conftest.py" for f in files):
        files.insert(0, {"path": "tests/conftest.py", "content": conftest_txt})

    fixture_hygiene_addendum = ""
    called_fixture = _extract_called_fixture_name(pytest_output or "")
    out_low = (pytest_output or "").lower()
    if ("fixture" in out_low and "called directly" in out_low) or called_fixture:
        fixture_hygiene_addendum = f"""
REGLAS ADICIONALES (DETECTADO ERROR DE FIXTURES: called directly)
- Pytest detectó que una fixture está siendo LLAMADA como función (antipatrón).
- Fix concreto:
  - Nunca hagas `{called_fixture or "fixture_name"}()` dentro de overrides.
  - Si necesitas el valor de una fixture dentro de `client`, debes recibirla como parámetro de la fixture `client` (inyección de pytest),
    y luego capturar el valor en un closure:
      - `def override(): return {called_fixture or "value"}`
- Regla general: `app.dependency_overrides[...] = override_fn` donde override_fn es una función normal (sync/async) que retorna/yielda el mock.
- Evita side-effects en top-level del módulo de test (no mutar `app.dependency_overrides` fuera de fixtures).
- No mezcles `async def test_*` con `TestClient` sync: si usas TestClient, tests deben ser sync.
""".strip()

    return f"""
TAREA
Pytest está fallando. Corrige SOLO los tests (tests/*.py), pytest.ini y (si es imprescindible) requirements-dev.txt para que la suite pase.

{fixture_hygiene_addendum}

OBJETIVO PRINCIPAL (NO NEGOCIABLE): TESTS 100% HERMÉTICOS
- Los tests NO pueden depender de variables de entorno (os.getenv), ficheros locales, red, credenciales,
  SDKs cloud, ni ninguna integración externa (DBs, Google APIs, AWS, etc.).
- Si el código de la app intenta acceder a integraciones externas en tiempo de test, el test DEBE aislarlo
  mediante dependency_overrides/monkeypatch para que NUNCA se ejecute el código real.

IMPORTANTE
- No puedes modificar código de la app (PROHIBIDO tocar app/**/*.py).
- Debes responder con un JSON con un patch de archivos COMPLETOS (path+content).
- El patch debe ser mínimo: cambia lo imprescindible para que pase.
- Puedes modificar requirements-dev.txt SOLO si el error es por dependencia de tests ausente (ModuleNotFoundError) y no existe alternativa hermética sin esa dependencia.

PYTEST OUTPUT (truncado a 12000 chars)
{(pytest_output or "")[:12000]}

RUNTIME_CONTRACTS (si disponible; JSON)
{json.dumps(runtime_contracts, ensure_ascii=False)}

RUNTIME_FACTS (si disponible; JSON)
{json.dumps(runtime_facts, ensure_ascii=False)}

TESTS ACTUALES (JSON; archivos completos)
- Nota: `tests/conftest.py` es el harness; úsalo como “fuente de verdad” para cómo se construye el fixture `client`,
  qué overrides existen, y qué stubs están disponibles. Evita re-inventar wiring en cada test file.
{json.dumps(files, ensure_ascii=False)}

SALIDA (JSON)
{{"files": [{{"path": "tests/...", "content": "..." }}, {{"path": "pytest.ini", "content": "..." }}, {{"path": "requirements-dev.txt", "content": "..."}}]}}

REGLAS (OBLIGATORIAS)
- Devuelve SOLO archivos bajo tests/, pytest.ini y/o requirements-dev.txt.
- No inventes imports que no existan en el proyecto.

REGLA NUEVA (OBLIGATORIA): ModuleNotFoundError / deps de tests
- Si el output contiene `ModuleNotFoundError: No module named 'X'`:
  1) Primero reescribe los tests para NO depender de X (preferido), manteniendo hermeticidad (sin I/O real).
  2) Solo si X es una dependencia de testing razonable (pytest/httpx/requests-mock/etc.) y es estrictamente necesaria,
     entonces añade X a requirements-dev.txt.
  3) Está PROHIBIDO añadir drivers/SDKs de integraciones externas (DB drivers, cloud SDKs) a requirements-dev.txt
     para “arreglar” el test. En esos casos debes eliminar esa estrategia y degradar a contract-lite.

REGLA NUEVA (OBLIGATORIA): Allowlist de imports (derivada de requirements)
- Los tests SOLO pueden importar:
  - stdlib
  - app.*
  - paquetes presentes en requirements.txt / requirements-dev.txt
- Si detectas un import fuera de allowlist, debes eliminarlo/reemplazarlo por un stub/hermetic approach.

REGLA NUEVA (OBLIGATORIA): 422 y FUENTE de parámetros (query/body/form)
- Si pytest muestra 422 "field required" o "missing":
  - Revisa `runtime_contracts.endpoints[*]` para el path/method afectado y decide la fuente:
    - si `query_params_required` no está vacío: el test debe enviar `params={...}` o añadir querystring.
    - si `request_body_param`/`request_model` existe: el test debe enviar `json={...}`.
    - si `form_params_required`/`file_params_required` existe: el test debe usar `data=`/`files=`.
  - Está PROHIBIDO "inventar" required_fields desde SPEC si runtime_contracts no lo confirma.

REGLAS CRÍTICAS (OBLIGATORIAS) PARA DEPENDENCY OVERRIDES / STUBS (PARCIAL)
Objetivo: aislamiento total de integraciones externas y evitar el patrón erróneo "MockService como si fuera db/session".

REGLA NUEVA (OBLIGATORIA): SINGLETON OVERRIDE PARA STUBS STATEFUL
- Si el stub/mocking introduce estado (p.ej. `self.items = {dict}`, `self.store = {dict}`, `self.data = [list]`) O si el test hace una secuencia
  tipo POST -> GET/PUT/DELETE esperando ver lo creado, entonces el override DEBE devolver SIEMPRE la MISMA instancia durante el scope del TestClient/fixture.
- Está PROHIBIDO: `app.dependency_overrides[get_service] = lambda: FakeService()` cuando FakeService es stateful o hay secuencias CRUD.
- Patrón correcto (sync dependency):
    - `fake = FakeService()`
    - `def override(): return fake`
    - `app.dependency_overrides[get_service] = override`
- Patrón correcto (async dependency):
    - `fake = FakeService()`
    - `async def override(): return fake`
    - `app.dependency_overrides[get_service] = override`
- Si necesitas resetear estado entre tests, crea la instancia dentro del fixture `client()` (una por test) y NO a nivel módulo.

0) REGLA HERMETICIDAD (OBLIGATORIA)
- Si el traceback menciona `os.getenv`, `GOOGLE_APPLICATION_CREDENTIALS`, `DATABASE_URL`, `requests`, `httpx`,
  `google.auth`, `boto3`, `sqlalchemy`, sockets, etc. => el test está ejecutando integración externa.
  Debes evitarlo SIEMPRE mediante overrides/monkeypatch.
- Los tests deben poder correr en CI sin ninguna variable de entorno.

1) Solo overridear dependencias REALES
- Para decidir qué overridear usa SOLO `runtime_contracts.allowed_dependency_overrides` (si existe) y/o `runtime_contracts.endpoints[*].depends_imports`.
- Está PROHIBIDO overridear símbolos que no estén listados ahí.
- Ejemplo correcto:
  - si allowed_dependency_overrides incluye "app.database.get_db":
    - `from app.database import get_db`
    - `app.dependency_overrides[get_db] = override_get_db`

2) Overrides/monkeypatch ASYNC-SAFE (OBLIGATORIO)
- Si el símbolo original es `async def` (p.ej. `@classmethod async def get_service(...)`):
  - el stub/override DEBE ser `async def` también.
  - Está PROHIBIDO monkeypatchear un async def con `lambda: ...` porque FastAPI/AnyIO lo intentará await-ear.
  - Patrón correcto:
    - `async def fake_get_service(*args, **kwargs): return FakeService()`
    - `monkeypatch.setattr("mod.Clase.metodo", fake_get_service)`

3) No devolver tipos incompatibles
- Si overrideas `get_db`, el objeto que yields/returns DEBE comportarse como la sesión/cliente que el código espera.
- Señal: si el traceback contiene `db.add(...)`, `db.execute(...)`, `db.commit(...)`, `db.refresh(...)` o `db.delete(...)`:
  - tu override_get_db debe yield un objeto que implemente esos métodos (puede ser FakeSession in-memory).
  - Está PROHIBIDO: `yield MockProductService()` o cualquier Mock*Service si el parámetro se llama `db`/`session` y el código hace `db.add/execute/...`.
- Si el código usa una capa service (evidencia: handler usa Depends(get_service) y luego llama `await svc.method(...)`):
  - entonces overridea ESA dependencia de servicio con un stub stateful, y NO overridees get_db.

4) Stubs stateful cuando el test lo requiere
- Si un test hace POST y luego GET/PUT/DELETE usando el id creado:
  - el stub debe ser stateful (misma instancia durante la vida del TestClient fixture)
  - y debe devolver siempre los required fields del response_model (p.ej. `id`) para evitar ResponseValidationError.

5) Si no puedes stubear correctamente, degrada el test (pero no lo rompas)
- Evita asserts rígidos de 200/201 si el endpoint depende de integración externa no hermetizada.
- Acepta 404 en GET/PUT/DELETE por id cuando el recurso no exista.
- Nunca uses "expected_status=200" en parametrización si no hay aislamiento correcto.

6) Regla Starlette TestClient
- PROHIBIDO: `client.get(..., json=...)`
- Si necesitas body en GET: `client.request("GET", url, json=...)`

OTRAS REGLAS DE REPARACIÓN
- Si el fallo es por rutas no registradas (404):
  - No esperes 200/405/422 en esas rutas.
  - Ajusta tests para degradar a contract-lite (p.ej. comprobar /openapi.json o aceptar 404), sin tocar la app.
- Si el fallo es por asserts demasiado estrictos:
  - Relaja aserciones (status_code < 500, membership, claves mínimas) manteniendo valor diagnóstico.
""".strip()


def _persist_test_repair_context(project_dir: str, ctx: dict) -> None:
    try:
        os.makedirs(os.path.join(project_dir, ".poc_it"), exist_ok=True)
        with open(os.path.join(project_dir, TEST_REPAIR_CONTEXT_PATH), "w", encoding="utf-8") as f:
            json.dump(ctx, f, ensure_ascii=False, indent=2)
    except Exception:
        return


def _ensure_base_conftest(
    *,
    nombre_proyecto: str,
    project_dir: str,
    estructura: Dict[str, str],
    runtime_contracts: Optional[dict],
    runtime_facts: Optional[dict],
) -> None:
    # Si no existe conftest, lo provisionamos con el harness estándar. Esto reduce
    # drásticamente errores de async/sync y wiring al dejar de “reinventar” el fixture client
    # en cada archivo de test.
    if "tests/conftest.py" in (estructura or {}):
        return
    try:
        from poc_it.orquestacion.tests_harness import render_conftest_py

        # Compatibilidad: `render_conftest_py` puede variar de firma según versión.
        # Intentamos primero con ambos argumentos y hacemos fallback a solo runtime_contracts.
        try:
            content = render_conftest_py(runtime_contracts=runtime_contracts, runtime_facts=runtime_facts)
        except TypeError:
            content = render_conftest_py(runtime_contracts=runtime_contracts)

        patch = {"tests/conftest.py": content}
        materializar_proyecto(nombre_proyecto=nombre_proyecto, estructura=patch, limpiar_directorio=False)
        estructura.update(patch)
    except Exception:
        logger.exception("[PYTEST-REPAIR] No se pudo provisionar tests/conftest.py base; se continúa.")


def _maybe_repair_overrides_with_llm(
    *,
    nombre_proyecto: str,
    project_dir: str,
    estructura: Dict[str, str],
    current_tests: Dict[str, str],
    pytest_output: str,
    runtime_contracts: Optional[dict],
) -> bool:
    """Intenta reparar el harness de overrides (tests/conftest.py) con una llamada LLM dedicada.

    Esto NO es "arreglar asserts": es corregir wiring para que:
    - dependency_overrides use callables (no instancias)
    - get_db sea generator/async generator
    - stubs sean stateful
    - hermeticidad

    Retorna True si aplicó un patch (aunque luego pytest siga fallando).
    """
    try:
        # stub_signatures es opcional; si existe ayuda a awaited/sync + chains.
        stub_signatures = None
        ss_path = os.path.join(project_dir, ".poc_it", "stub_signatures.json")
        if os.path.exists(ss_path):
            with open(ss_path, "r", encoding="utf-8") as f:
                stub_signatures = json.load(f)

        res = generate_or_repair_conftest_overrides_with_llm(
            runtime_contracts=runtime_contracts or {},
            stub_signatures=stub_signatures,
            estructura=estructura or {},
            pytest_output=pytest_output or "",
            current_tests=current_tests or {},
        )
        if not res.patched_files:
            logger.info("[PYTEST-REPAIR][OVERRIDES] LLM no devolvió conftest.py reparado.")
            return False

        if not res.ok:
            logger.info("[PYTEST-REPAIR][OVERRIDES] conftest generado no pasó verificador: %s", res.errors[:5])
            return False

        materializar_proyecto(
            nombre_proyecto=nombre_proyecto,
            estructura=res.patched_files,
            limpiar_directorio=False,
        )
        estructura.update(res.patched_files)
        current_tests.update(res.patched_files)
        logger.info("[PYTEST-REPAIR][OVERRIDES] conftest.py reparado/aplicado por LLM.")
        append_trace_event(
            project_dir,
            TraceEvent(
                ts=now_ts(),
                phase="C-OVERRIDES",
                attempt=-1,
                action="llm_patch",
                files_changed=list(res.patched_files.keys()),
            ),
        )
        return True
    except Exception:
        logger.exception("[PYTEST-REPAIR][OVERRIDES] Error inesperado; se continúa sin overrides repair.")
        return False


def _filter_patch_for_phase(
    patch: Dict[str, str],
    *,
    phase: str,
    allow_harness_files: Optional[Sequence[str]] = None,
    allow_escape_hatch_harness_in_c2: bool = False,
) -> Dict[str, str]:
    # Política por fase:
    # - C1 (harness): por defecto solo permitimos conftest/pytest.ini/requirements-dev.txt (y opcionalmente stubs.py)
    #   PERO: hay un caso real donde el harness está embebido en el propio test file (fixture `client` definida en tests/test_*.py).
    #   En ese caso, permitimos editar SOLO ese archivo concreto (allow_harness_files) para poder:
    #     - convertir fixture/client a sync, o
    #     - eliminarla y moverla a conftest.
    # - C2 (asserts): permitimos tests/test_*.py + pytest.ini; conftest permitido pero no necesario.
    if not patch:
        return {}
    if phase == "C1":
        allowed = {"tests/conftest.py", "tests/stubs.py", "pytest.ini", "requirements-dev.txt"}
        out = {p: c for p, c in patch.items() if p in allowed}
        if allow_harness_files:
            allow = set(allow_harness_files)
            for p, c in patch.items():
                if p in allow and p.startswith("tests/test_") and p.endswith(".py"):
                    out[p] = c
        return out
    # C2
    out2: Dict[str, str] = {}
    for p, c in patch.items():
        if p in ("pytest.ini", "requirements-dev.txt"):
            out2[p] = c
            continue

        # En C2 normalmente NO dejamos tocar harness (conftest/stubs), salvo escape hatch controlado.
        if p in ("tests/conftest.py", "tests/stubs.py"):
            if allow_escape_hatch_harness_in_c2:
                out2[p] = c
            continue

        if p.startswith("tests/test_") and p.endswith(".py"):
            out2[p] = c
    return out2


_BASEID_RE = re.compile(r"baseid='(?P<path>tests/test_[^']+\.py)'", re.MULTILINE)
_ERROR_COLLECTING_RE = re.compile(r"ERROR collecting\s+(?P<path>tests/test_[^\s]+\.py)", re.MULTILINE)
_TESTCLIENT_CALL_RE = re.compile(r"\bclient\.(?P<meth>get|post|put|patch|delete|request)\s*\(", re.MULTILINE)
_URL_LITERAL_RE = re.compile(r"['\"](/[^'\"]+)['\"]")

# Señales de que el fallo NO es de asserts, sino de harness/wiring/stubs:
# (aunque aparezca durante "call")
_HARNESS_RUNTIME_ERROR_RE = re.compile(
    r"(NoneType can't be used in 'await' expression|"
    r"ResponseValidationError|"
    r"AttributeError:\s*'.*'\s*object has no attribute\s*'(add|execute|commit|refresh|delete)'|"
    r"TestClient\.get\(\) got an unexpected keyword argument 'json'|"
    r"async def functions are not natively supported|"
    r"Fixture\s+\"[^\"]+\"\s+called directly|"
    r"ModuleNotFoundError:\s*No module named)",
    re.IGNORECASE,
)


def _detect_harness_file_from_pytest_output(pytest_output: str) -> Optional[str]:
    if not pytest_output:
        return None
    m = _BASEID_RE.search(pytest_output)
    if m:
        p = (m.group("path") or "").strip()
        if p:
            return p
    m2 = _ERROR_COLLECTING_RE.search(pytest_output)
    if m2:
        p2 = (m2.group("path") or "").strip()
        if p2:
            return p2
    return None


def _runtime_openapi_paths(runtime_contracts: Optional[dict]) -> Dict[str, set]:
    """Devuelve dict path -> set(methods) (methods en minúscula). Best-effort."""
    if not runtime_contracts:
        return {}
    # Formatos esperados: runtime_contracts.openapi.paths o runtime_contracts.openapi_json.paths
    openapi = None
    for k in ("openapi", "openapi_json", "openapi_spec"):
        v = runtime_contracts.get(k) if isinstance(runtime_contracts, dict) else None
        if isinstance(v, dict) and v:
            openapi = v
            break
    paths = (openapi or {}).get("paths") if isinstance(openapi, dict) else None
    if not isinstance(paths, dict) or not paths:
        return {}
    out: Dict[str, set] = {}
    for p, methods in paths.items():
        if not isinstance(p, str) or not isinstance(methods, dict):
            continue
        ms = {str(m).lower() for m in methods.keys() if isinstance(m, str)}
        if ms:
            out[p] = ms
    return out


def _gate_patch_tests_endpoints_exist_in_openapi(
    patch: Dict[str, str],
    *,
    runtime_contracts: Optional[dict],
) -> Tuple[bool, str]:
    """Hard constraint: los tests no deben inventar rutas/métodos fuera de OpenAPI.

    Se aplica en C2 y como gate general si el patch introduce llamadas client.*("/path").
    En C1 no bloquea harness puro, pero evita que el modelo meta endpoints inventados en nuevos tests.
    """
    openapi_paths = _runtime_openapi_paths(runtime_contracts)
    if not openapi_paths:
        return True, "ok"  # si no hay OpenAPI, no podemos validar

    bad: List[str] = []
    for path, content in (patch or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/") or not path.endswith(".py"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        for m in _TESTCLIENT_CALL_RE.finditer(content):
            meth = (m.group("meth") or "").lower()
            # Para client.request("GET", "/x") extraemos método y url literal
            if meth == "request":
                # buscar literal del método inmediatamente después del paréntesis
                # (best-effort, sin AST)
                tail = content[m.end() : m.end() + 200]
                mm = re.search(r"^\s*['\"](?P<m>[A-Za-z]+)['\"]\s*,\s*['\"](?P<u>/[^'\"]+)['\"]", tail)
                if not mm:
                    continue
                meth = (mm.group("m") or "").lower()
                url = (mm.group("u") or "").strip()
            else:
                tail = content[m.end() : m.end() + 120]
                um = _URL_LITERAL_RE.search(tail)
                if not um:
                    continue
                url = (um.group(1) or "").strip()

            if not url:
                continue
            # Normalizar path params: /items/1 -> /items/{id} si existe en OpenAPI
            candidates = [url]
            if re.search(r"/\d+", url):
                candidates.append(re.sub(r"/\d+", "/{id}", url))
            ok = False
            for cand in candidates:
                ms = openapi_paths.get(cand)
                if ms and meth in ms:
                    ok = True
                    break
            if not ok:
                bad.append(f"{path}:{meth.upper()} {url}")

    if bad:
        return False, f"Tests call non-existent endpoints (not in OpenAPI): {bad[:10]}"
    return True, "ok"


_DEFAULT_ARTIFACTS = {
    "pytest_last_output": ".poc_it/pytest_last_output.txt",
    "pytest_junit": ".poc_it/pytest_junit.xml",
    "pytest_report": ".poc_it/pytest_report.json",
}


def _mk_result(
    *,
    ok: bool,
    attempts: int,
    last_output: str,
    patched_files: Dict[str, str],
    degraded: bool = False,
    degrade_type: Optional[str] = None,
) -> PytestRepairResult:
    return PytestRepairResult(
        ok=ok,
        attempts=attempts,
        last_output=last_output,
        patched_files=patched_files,
        degraded=degraded,
        degrade_type=degrade_type,
        artifacts=dict(_DEFAULT_ARTIFACTS),
    )


def repair_tests_until_pytest_passes(
    *,
    nombre_proyecto: str,
    project_dir: str,
    estructura: Dict[str, str],
    max_repairs: int = 3,
    runtime_contracts: Optional[dict] = None,
    runtime_facts: Optional[dict] = None,
) -> PytestRepairResult:
    # Asegurar harness base
    _ensure_base_conftest(
        nombre_proyecto=nombre_proyecto,
        project_dir=project_dir,
        estructura=estructura,
        runtime_contracts=runtime_contracts,
        runtime_facts=runtime_facts,
    )

    # extraer tests actuales desde estructura
    current_tests: Dict[str, str] = {}
    for p, c in (estructura or {}).items():
        if not isinstance(p, str):
            continue
        if p == "pytest.ini" or p.startswith("tests/"):
            current_tests[p] = c if isinstance(c, str) else ""

    last_out = ""
    patched_total: Dict[str, str] = {}

    # Métrica de progreso: (errors, failed) + fingerprint de error
    prev_err, prev_fail = 999, 999
    prev_fp = ""
    stuck_fp_streak = 0  # contador de fingerprint repetido (útil para escape hatch en C2)

    # Si el LLM no devuelve patch aplicable repetidamente en C1, antes se aplicaba un fixer determinista.
    # Estrategia actual: NO aplicar fixers deterministas. Solo trazas y, si no converge, degradar.
    c1_no_patch_streak = 0
    C1_NO_PATCH_LIMIT = 999999

    no_improve_streak = 0
    NO_IMPROVE_LIMIT = 4

    # Contexto normativo (compacto) para prompts + trazas
    test_ctx = build_test_repair_context(estructura=estructura, runtime_contracts=runtime_contracts, runtime_facts=runtime_facts)
    _persist_test_repair_context(project_dir, test_ctx)

    # Presupuesto adaptativo (harness suele necesitar más iteraciones)
    hard_budget = max(3, int(max_repairs or 3))
    max_allowed = max(hard_budget, 10)

    # Acceptance gate: nunca aceptar un patch que empeore (errors+fails) o que no mejore en absoluto.
    # - Si empeora vs baseline => rollback del patch aplicado en esa iteración.
    # - Si no mejora durante NO_IMPROVE_LIMIT => degradar (ya existe).
    baseline_total = None
    baseline_fp = None
    last_accepted_total = None
    last_accepted_fp = None

    attempt = 0
    while attempt <= max_allowed:
        ok, out, _junit_path = _run_pytest(project_dir)
        last_out = out
        cls = classify_pytest_failure(out)

        junit_xml = _read_pytest_junit_xml(project_dir)
        counts = _extract_counts_from_junit_xml(junit_xml)

        # Fuente de verdad: JUnit XML (built-in). Fallback: parser best-effort de stdout.
        if counts:
            err_n, fail_n, passed_n, skipped_n, total_all = counts
            total_n = err_n + fail_n
            total_prev = prev_err + prev_fail
            counts_source = "junit_xml"
        else:
            err_n, fail_n = _extract_pytest_counts(out)
            passed_n, skipped_n, total_all = 0, 0, 0
            total_n = err_n + fail_n
            total_prev = prev_err + prev_fail

            out_tail = "\n".join((out or "").splitlines()[-150:])
            if _PYTEST_SUMMARY_RE.search(out_tail):
                counts_source = "summary"
            elif any(
                re.match(r"^(?P<tok>[EF\.]{2,})\b", (ln or "").strip())
                for ln in (out or "").splitlines()[:600]
                if (ln or "").strip()
            ):
                counts_source = "progress_token"
            else:
                counts_source = "proxy_sections"

        # Estado visible para debug: qué está fallando exactamente.
        # En JUnit no tenemos phase (setup/call/teardown), pero sí podemos listar testcases fallidos.
        failed_nodeids: List[str] = []
        error_nodeids: List[str] = []
        phase_counts = {"setup": 0, "call": 0, "teardown": 0, "other": 0}

        try:
            if isinstance(junit_xml, str) and junit_xml.strip():
                import xml.etree.ElementTree as ET

                root = ET.fromstring(junit_xml)
                for tc in root.findall(".//testcase"):
                    name = (tc.get("name") or "").strip()
                    classname = (tc.get("classname") or "").strip()
                    nodeid = f"{classname}::{name}" if classname and name else (name or classname)
                    if tc.find("failure") is not None:
                        if nodeid:
                            failed_nodeids.append(nodeid)
                    elif tc.find("error") is not None:
                        if nodeid:
                            error_nodeids.append(nodeid)
        except Exception:
            failed_nodeids = []
            error_nodeids = []

        logger.info(
            "[PYTEST-REPAIR] attempt=%s/%s ok=%s class=%s counts_source=%s "
            "summary(E=%s F=%s P=%s S=%s T=%s) failing(total=%s prev=%s) "
            "phases=%s errors_nodeids=%s failed_nodeids=%s stuck_fp_streak=%s",
            attempt,
            max_allowed,
            ok,
            cls.kind,
            counts_source,
            err_n,
            fail_n,
            passed_n,
            skipped_n,
            total_all,
            total_n,
            total_prev,
            phase_counts,
            error_nodeids[:3],
            failed_nodeids[:3],
            stuck_fp_streak,
        )
        if not ok:
            # Persistir output completo para diagnóstico (evita “atascos” por truncado en logs/trace).
            try:
                os.makedirs(os.path.join(project_dir, ".poc_it"), exist_ok=True)
                with open(os.path.join(project_dir, ".poc_it", "pytest_last_output.txt"), "w", encoding="utf-8") as f:
                    f.write(out or "")
            except Exception:
                pass

            # Persistir json-report si existe (pytest-json-report)
            try:
                jr_path = os.path.join(project_dir, ".poc_it", "pytest_report.json")
                if os.path.exists(jr_path):
                    with open(jr_path, "r", encoding="utf-8") as f:
                        estructura[".poc_it/pytest_report.json"] = f.read()
            except Exception:
                pass

            head = (out or "").strip()[:800]
            tail = "\n".join((out or "").splitlines()[-120:])  # tail humano (últimas ~120 líneas)
            logger.info("[PYTEST-REPAIR] pytest output (head 800): %s", head)
            logger.info("[PYTEST-REPAIR] pytest output (tail 120 lines): %s", tail)

        # IMPORTANTE: no contamos “sin mejora” si no aplicamos patch (ver abajo).
        # Aquí solo medimos mejora relativa, pero el contador final se decide después de aplicar patch.
        # Fingerprint (anti-loop):
        # preferimos la señal del JUnit XML porque es estable (lista de testcases fail/error + msg).
        fp = _fingerprint_from_pytest_output(out)
        try:
            if isinstance(junit_xml, str) and junit_xml.strip():
                import xml.etree.ElementTree as ET

                root = ET.fromstring(junit_xml)
                items = []
                for tc in root.findall(".//testcase"):
                    classname = (tc.get("classname") or "").strip()
                    name = (tc.get("name") or "").strip()
                    nodeid = f"{classname}::{name}" if classname and name else (name or classname)
                    if tc.find("failure") is not None:
                        msg = (tc.find("failure").get("message") or "") if tc.find("failure") is not None else ""
                        items.append({"nodeid": nodeid, "outcome": "failed", "message": msg[:200]})
                    elif tc.find("error") is not None:
                        msg = (tc.find("error").get("message") or "") if tc.find("error") is not None else ""
                        items.append({"nodeid": nodeid, "outcome": "error", "message": msg[:200]})
                blob = json.dumps(sorted(items, key=lambda x: str(x.get("nodeid"))), ensure_ascii=False)[:20000]
                fp = hashlib.sha1(blob.encode("utf-8", errors="ignore")).hexdigest()
        except Exception:
            pass

        improved = (total_n < total_prev) or (fp and fp != prev_fp)

        # baseline: primera ejecución antes de aplicar patches
        if baseline_total is None:
            baseline_total = total_n
            baseline_fp = fp
            last_accepted_total = total_n
            last_accepted_fp = fp

        if fp and fp == prev_fp:
            stuck_fp_streak += 1
        else:
            stuck_fp_streak = 0

        prev_err, prev_fail = err_n, fail_n
        prev_fp = fp or prev_fp

        append_trace_event(
            project_dir,
            TraceEvent(
                ts=now_ts(),
                phase="C",
                attempt=attempt,
                action="pytest_run",
                classifier_kind=cls.kind,
                detail=cls.detail,
                pytest_head=(out or "").strip()[:400],
            ),
        )

        if ok:
            return _mk_result(ok=True, attempts=attempt, last_output=out, patched_files=patched_total)

        # --- Fixers deterministas (C0) ----------------------------------------------------
        # Estrategia híbrida:
        # - Para patrones de alta confianza y muy frecuentes (TestClient.get(json=...), dict-as-db, async mismatch...)
        #   aplicamos 1 fixer determinista ANTES de gastar tokens del LLM.
        # - Mantiene el loop LLM para casos "long tail", pero reduce flakiness y coste.
        if apply_first_matching_fixer is not None:
            try:
                fx = apply_first_matching_fixer(out or "", estructura or {})
            except Exception:
                fx = None
            if fx and isinstance(fx.patched_files, dict) and fx.patched_files:
                logger.info("[PYTEST-REPAIR][FIXER] Applied deterministic fixer: %s", fx.message)
                materializar_proyecto(
                    nombre_proyecto=nombre_proyecto,
                    estructura=fx.patched_files,
                    limpiar_directorio=False,
                )
                estructura.update(fx.patched_files)
                current_tests.update({k: v for k, v in fx.patched_files.items() if k == "pytest.ini" or k.startswith("tests/")})
                patched_total.update(fx.patched_files)
                append_trace_event(
                    project_dir,
                    TraceEvent(
                        ts=now_ts(),
                        phase="C0",
                        attempt=attempt,
                        action="deterministic_fixer",
                        detail=fx.message,
                        files_changed=list(fx.patched_files.keys()),
                    ),
                )
                attempt += 1
                continue

        # --- Nueva fase: reparación de overrides/harness (anti-atasco C2) -----------------
        # Heurística:
        # - si hay 1 fallo recurrente tipo REQUEST_SHAPE (p.ej. POST esperado 201) en suite hermética,
        #   suele ser wiring/overrides (dependency_overrides no-callable / get_db incorrecto) más que asserts.
        # - intentamos una reparación dedicada de conftest antes de gastar iteraciones en C2.
        if cls.kind in ("REQUEST_SHAPE", "RESPONSE_SHAPE") and attempt in (0, 1):
            applied = _maybe_repair_overrides_with_llm(
                nombre_proyecto=nombre_proyecto,
                project_dir=project_dir,
                estructura=estructura,
                current_tests=current_tests,
                pytest_output=out,
                runtime_contracts=runtime_contracts,
            )
            if applied:
                # reintenta pytest inmediatamente sin consumir una iteración de LLM general
                attempt += 1
                continue

        # Nota: la degradación se evaluará DESPUÉS de intentar aplicar un patch.

        # presupuesto adaptativo: si es un problema de harness, damos más intentos.
        if cls.kind in ("ASYNC_SYNC", "IMPORT_ERROR", "FIXTURES", "COLLECTION", "UNKNOWN"):
            max_allowed = max(max_allowed, max(hard_budget, 8))
        elif cls.kind in ("ASSERTIONS",):
            max_allowed = max(max_allowed, hard_budget)
        else:
            max_allowed = max(max_allowed, max(hard_budget, 6))

        if attempt >= max_allowed:
            break

        # Selección de prompt por clase (C1 harness vs C2 asserts)
        # - Harness: collection/import, fixtures/harness, async mismatch, request/response shape
        # - Asserts: assertion mismatches (suite runnable)
        #
        # Problema real observado: algunos fallos aparecen como "FAILURES" pero su causa es wiring
        # (stubs mal configurados) y relajar asserts NO arregla nada.
        out_txt = out or ""
        has_failures_section = "================================== FAILURES" in out_txt
        has_errors_section = ("=================================== ERRORS" in out_txt) or ("ERROR at setup" in out_txt) or ("ERROR collecting" in out_txt)

        looks_like_harness = bool(_HARNESS_RUNTIME_ERROR_RE.search(out_txt))
        force_c1 = bool(looks_like_harness)

        # Regla pragmática:
        # - Si hay señales de harness/runtime, prioriza C1 aunque sea failure en call.
        # - Si NO hay señales de harness y hay FAILURES sin ERRORS, vamos a C2.
        force_c2 = bool((has_failures_section and not has_errors_section) and not force_c1)

        if not force_c1 and (force_c2 or cls.kind in ("ASSERTIONS",)):
            # En C2 necesitamos contexto del wiring real + firmas de stubs para alinear asserts.
            # stub_signatures se persiste en `.poc_it/stub_signatures.json` por el pipeline de stubs.
            stub_signatures = None
            try:
                ss_path = os.path.join(project_dir, ".poc_it", "stub_signatures.json")
                if os.path.exists(ss_path):
                    with open(ss_path, "r", encoding="utf-8") as f:
                        stub_signatures = json.load(f)
            except Exception:
                stub_signatures = None

            pytest_json_report_obj = None
            try:
                jr_raw = estructura.get(".poc_it/pytest_report.json") or ""
                pytest_json_report_obj = json.loads(jr_raw) if isinstance(jr_raw, str) and jr_raw.strip() else None
            except Exception:
                pytest_json_report_obj = None

            prompt = build_prompt_c2_asserts_repair(
                pytest_output=out,
                current_tests=current_tests,
                test_repair_context=test_ctx,
                runtime_contracts=runtime_contracts,
                stub_signatures=stub_signatures,
                pytest_json_report=pytest_json_report_obj,
            )
            subphase = "C2"
        else:
            # En C1 pasamos también stub_signatures (si existen) para que el modelo repare stubs/overrides
            # con información del wiring real (awaited vs sync, métodos esperados, etc.).
            stub_signatures = None
            try:
                ss_path = os.path.join(project_dir, ".poc_it", "stub_signatures.json")
                if os.path.exists(ss_path):
                    with open(ss_path, "r", encoding="utf-8") as f:
                        stub_signatures = json.load(f)
            except Exception:
                stub_signatures = None

            # Extraer código del endpoint implicado (best-effort) para reducir alucinación:
            # - vemos el path/method en el traceback (archivo app/endpoints/*.py o app/routes/*.py)
            # - cargamos ese archivo y lo pasamos al prompt (solo ese)
            endpoint_code = None
            try:
                mfile = re.search(r'File \"(?P<path>.*?app[\\\\/].*?\\.py)\"', out_txt)
                if mfile:
                    ep_abs = mfile.group("path")
                    if ep_abs and os.path.exists(ep_abs):
                        with open(ep_abs, "r", encoding="utf-8") as f:
                            endpoint_code = {os.path.relpath(ep_abs, project_dir).replace("\\\\", "/"): f.read()}
            except Exception:
                endpoint_code = None

            pytest_json_report_obj = None
            try:
                jr_raw = estructura.get(".poc_it/pytest_report.json") or ""
                pytest_json_report_obj = json.loads(jr_raw) if isinstance(jr_raw, str) and jr_raw.strip() else None
            except Exception:
                pytest_json_report_obj = None

            prompt = build_prompt_c1_harness_repair(
                pytest_output=out,
                current_tests=current_tests,
                test_repair_context=test_ctx,
                runtime_contracts=runtime_contracts,
                runtime_facts=runtime_facts,
                stub_signatures=stub_signatures,
                endpoint_code=endpoint_code,
                pytest_json_report=pytest_json_report_obj,
            )
            subphase = "C1"

        append_trace_event(
            project_dir,
            TraceEvent(ts=now_ts(), phase=subphase, attempt=attempt, action="llm_prompt_built", classifier_kind=cls.kind, detail=cls.detail),
        )

        harness_file = None
        if subphase == "C1":
            # También aplica si cls=UNKNOWN pero hay ERROR collecting/baseid (caso real observado)
            harness_file = _detect_harness_file_from_pytest_output(out)

        raw = chat_completion_json(
            prompt=prompt,
            system=None,
            temperature=0.1,
            max_tokens=1800,
            provider_hint="code-gen",
            fase="generacion_codigo",
        )

        data = extraer_json_tolerante(raw) or {}
        patch = _normalize_patch_files(data.get("files"))

        # Escape hatch:
        # - C2: si repetimos el mismo fingerprint varias veces, permitimos tocar conftest/stubs para arreglar wiring/stubs.
        # - C1: si el LLM insiste en devolver parches no aplicables/vacíos, dejamos que edite el harness embebido (harness_file)
        #       y conftest/stubs (ya permitido en C1) sin quedarnos en bucle infinito.
        allow_escape_hatch_harness_in_c2 = bool(subphase == "C2" and stuck_fp_streak >= 2)

        patch = _filter_patch_for_phase(
            patch,
            phase=subphase,
            allow_harness_files=[harness_file] if harness_file else None,
            allow_escape_hatch_harness_in_c2=allow_escape_hatch_harness_in_c2,
        )
        if not patch:
            logger.info("[PYTEST-REPAIR] LLM no devolvió patch aplicable para fase=%s; continuando.", subphase)
            append_trace_event(project_dir, TraceEvent(ts=now_ts(), phase=subphase, attempt=attempt, action="llm_no_patch_applicable"))

            if subphase == "C1":
                c1_no_patch_streak += 1
                append_trace_event(
                    project_dir,
                    TraceEvent(
                        ts=now_ts(),
                        phase="C1",
                        attempt=attempt,
                        action="llm_no_patch_applicable_c1",
                        detail=f"no_patch_streak={c1_no_patch_streak}",
                    ),
                )

            # no cuenta como no-improve: no se aplicó nada
            attempt += 1
            continue

        # Constraint de convergencia (genérica): si el error está en un harness embebido,
        # el patch de C1 debe tocar ese archivo o, si no, no puede resolver el problema.
        if subphase == "C1" and harness_file and harness_file.startswith("tests/test_") and harness_file not in patch:
            logger.info(
                "[PYTEST-REPAIR] Patch C1 ignoró harness_file=%s; reintentando sin consumir presupuesto.",
                harness_file,
            )
            append_trace_event(
                project_dir,
                TraceEvent(
                    ts=now_ts(),
                    phase=subphase,
                    attempt=attempt,
                    action="llm_patch_missing_harness_file",
                    files_changed=list(patch.keys()),
                ),
            )
            attempt += 1
            continue

        logger.info("[PYTEST-REPAIR] LLM patch (fase=%s) files=%s", subphase, list(patch.keys()))
        append_trace_event(
            project_dir,
            TraceEvent(ts=now_ts(), phase=subphase, attempt=attempt, action="llm_patch", files_changed=list(patch.keys())),
        )

        ok_gate, reason = _gate_patch_tests_no_undefined_override_symbols(patch)
        if ok_gate:
            ok_gate, reason = _gate_patch_tests_override_fqns_in_allowlist(patch, runtime_contracts=runtime_contracts, test_ctx=test_ctx)
        if ok_gate:
            ok_gate, reason = _gate_patch_tests_override_asyncness_matches_runtime(patch, runtime_contracts=runtime_contracts)
        if ok_gate:
            ok_gate, reason = _gate_patch_tests_no_async_tests_with_testclient(patch)
        if ok_gate:
            ok_gate, reason = _gate_patch_tests_no_client_get_json_kw(patch)
        if ok_gate:
            ok_gate, reason = _gate_patch_tests_endpoints_exist_in_openapi(patch, runtime_contracts=runtime_contracts)
        # Gate de igualdad exacta:
        # - En C1 NO debe bloquear la reparación de harness.
        # - En C2 sí bloquea asserts frágiles.
        if ok_gate and subphase != "C1":
            ok_gate, reason = _gate_patch_tests_no_exact_response_json_equality(patch, allow_in_conftest=True)
        if ok_gate:
            ok_gate, reason = _gate_patch_tests_no_wide_status_code_sets(patch, phase=subphase)
        if ok_gate:
            ok_gate, reason = _gate_patch_tests_stub_returns_required_keys(patch, test_ctx=test_ctx)

        if not ok_gate:
            # Gates suaves: intentamos aplicar subset que permita progreso (C1 preserva harness_file).
            logger.info("[PYTEST-REPAIR] Patch rechazado por gate: %s", reason)

            filtered: Dict[str, str] = {}
            if subphase == "C1":
                filtered = _filter_patch_for_phase(
                    patch,
                    phase="C1",
                    allow_harness_files=[harness_file] if harness_file else None,
                )
                # Si el gate era el de igualdad exacta (ya no aplica en C1), aquí no debería llegar.
            else:
                # En C2: si el problema viene por igualdad exacta, intentamos quitar los archivos que la contienen.
                if "Fragile assert detected" in (reason or ""):
                    filtered = {p: c for p, c in patch.items() if p in ("tests/conftest.py", "pytest.ini")}

            if filtered:
                logger.info("[PYTEST-REPAIR] Gate soft-apply: aplicando subset files=%s", list(filtered.keys()))
                patch = filtered
                ok_gate = True
            else:
                logger.info("[PYTEST-REPAIR] Gate rejected files=%s", list(patch.keys()))
                append_trace_event(
                    project_dir,
                    TraceEvent(ts=now_ts(), phase=subphase, attempt=attempt, action="gate_reject", gate_reason=reason, files_changed=list(patch.keys())),
                )

                retry_prompt = (prompt + "\n\n[GATE FAILED]\n" + reason).strip()
                raw2 = chat_completion_json(
                    prompt=retry_prompt,
                    system=None,
                    temperature=0.1,
                    max_tokens=1800,
                    provider_hint="code-gen",
                    fase="generacion_codigo",
                )
                data2 = extraer_json_tolerante(raw2) or {}
                patch2 = _normalize_patch_files(data2.get("files"))

                allow_escape_hatch_harness_in_c2 = bool(subphase == "C2" and stuck_fp_streak >= 2)

                patch2 = _filter_patch_for_phase(
                    patch2,
                    phase=subphase,
                    allow_harness_files=[harness_file] if harness_file else None,
                    allow_escape_hatch_harness_in_c2=allow_escape_hatch_harness_in_c2,
                )
                if not patch2:
                    logger.info("[PYTEST-REPAIR] LLM no devolvió patch tras gate; continuando a siguiente iteración.")
                    append_trace_event(project_dir, TraceEvent(ts=now_ts(), phase=subphase, attempt=attempt, action="llm_no_patch_after_gate"))
                    attempt += 1
                    continue

                ok_gate2, reason2 = _gate_patch_tests_no_undefined_override_symbols(patch2)
                if ok_gate2:
                    ok_gate2, reason2 = _gate_patch_tests_override_fqns_in_allowlist(patch2, runtime_contracts=runtime_contracts, test_ctx=test_ctx)
                if ok_gate2:
                    ok_gate2, reason2 = _gate_patch_tests_override_asyncness_matches_runtime(patch2, runtime_contracts=runtime_contracts)
                if ok_gate2:
                    ok_gate2, reason2 = _gate_patch_tests_no_async_tests_with_testclient(patch2)
                if ok_gate2:
                    ok_gate2, reason2 = _gate_patch_tests_no_client_get_json_kw(patch2)
                if ok_gate2:
                    ok_gate2, reason2 = _gate_patch_tests_endpoints_exist_in_openapi(patch2, runtime_contracts=runtime_contracts)
                if ok_gate2 and subphase != "C1":
                    ok_gate2, reason2 = _gate_patch_tests_no_exact_response_json_equality(patch2, allow_in_conftest=True)
                if ok_gate2:
                    ok_gate2, reason2 = _gate_patch_tests_no_wide_status_code_sets(patch2, phase=subphase)
                if ok_gate2:
                    ok_gate2, reason2 = _gate_patch_tests_stub_returns_required_keys(patch2, test_ctx=test_ctx)

                if not ok_gate2:
                    logger.info("[PYTEST-REPAIR] Patch sigue inválido tras gate: %s", reason2)
                    append_trace_event(
                        project_dir,
                        TraceEvent(ts=now_ts(), phase=subphase, attempt=attempt, action="gate_reject_after_retry", gate_reason=reason2),
                    )
                    attempt += 1
                    continue

                patch = patch2

        # Aplicar patch a disco + estructura in-memory (con snapshot para rollback)
        snapshot_tests = dict(current_tests)
        snapshot_estructura = dict(estructura)

        materializar_proyecto(
            nombre_proyecto=nombre_proyecto,
            estructura=patch,
            limpiar_directorio=False,
        )
        estructura.update(patch)
        current_tests.update(patch)
        patched_total.update(patch)

        # Acceptance gate: re-run pytest inmediatamente y aceptar SOLO si mejora vs baseline o vs prev
        ok_after, out_after, _ = _run_pytest(project_dir)
        last_out = out_after

        junit_xml_after = _read_pytest_junit_xml(project_dir)
        counts_after = _extract_counts_from_junit_xml(junit_xml_after)
        if counts_after:
            err_a, fail_a, *_rest = counts_after
            total_after = err_a + fail_a
        else:
            err_a, fail_a = _extract_pytest_counts(out_after)
            total_after = err_a + fail_a

        fp_after = _fingerprint_from_pytest_output(out_after)

        # aceptación:
        # - pasa => aceptar
        # - o mejora total failures/errors vs último aceptado
        # - o cambia fingerprint vs último aceptado (indicando progreso real)
        #
        # Nota: NO comparamos contra `total_n` porque pertenece al run pre-patch de ESTA iteración;
        # lo correcto es comparar contra el último estado aceptado (evita aceptar "flapping" o regresiones).
        accept = bool(ok_after) or bool(
            (last_accepted_total is not None and total_after < last_accepted_total)
        ) or bool(fp_after and last_accepted_fp and fp_after != last_accepted_fp)

        if not accept:
            logger.info(
                "[PYTEST-REPAIR][GATE] Patch rechazado (no mejora). before(total=%s fp=%s) after(total=%s fp=%s). Rollback.",
                total_n,
                (fp or "")[:8],
                total_after,
                (fp_after or "")[:8],
            )
            # rollback in-memory
            estructura.clear()
            estructura.update(snapshot_estructura)
            current_tests.clear()
            current_tests.update(snapshot_tests)

            # rollback on-disk: re-materializar snapshot (solo tests/ + pytest.ini)
            rollback_patch = {p: c for p, c in snapshot_tests.items()}
            try:
                materializar_proyecto(
                    nombre_proyecto=nombre_proyecto,
                    estructura=rollback_patch,
                    limpiar_directorio=False,
                )
            except Exception:
                pass

            append_trace_event(
                project_dir,
                TraceEvent(
                    ts=now_ts(),
                    phase=subphase,
                    attempt=attempt,
                    action="gate_reject_no_improve",
                    files_changed=list(patch.keys()),
                ),
            )

            attempt += 1
            continue

        # Patch aceptado => actualizar estado aceptado.
        last_accepted_total = total_after
        last_accepted_fp = fp_after or last_accepted_fp

        # Si ya pasa tras el patch, devolver OK inmediatamente
        if ok_after:
            return _mk_result(ok=True, attempts=attempt, last_output=out_after, patched_files=patched_total)

        # 🔒 Reinyectar configuración base de pytest.ini tras cada patch (anti-LLM override)
        try:
            base_pytest_ini = """[pytest]
addopts = -q
testpaths = tests
python_files = test_smoke_import.py test_openapi.py test_endpoints_hermetic.py
asyncio_mode = auto
markers =
    hermetic
    openapi
    smoke
"""
            materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura={"pytest.ini": base_pytest_ini},
                limpiar_directorio=False,
            )
            estructura["pytest.ini"] = base_pytest_ini
            current_tests["pytest.ini"] = base_pytest_ini
            patched_total["pytest.ini"] = base_pytest_ini
        except Exception:
            logger.exception("[PYTEST-REPAIR] No se pudo reinyectar pytest.ini base.")

        # Budget / no_improve: solo cuenta si aplicamos patch.
        # Reset contador de "no patch" en C1 cuando sí aplicamos algo.
        if subphase == "C1":
            c1_no_patch_streak = 0

        # En esta iteración, si llegamos aquí el patch ha sido aceptado (mejoró).
        no_improve_streak = 0

        # Si no mejora en varias iteraciones, degradamos para garantizar tests passing.
        if no_improve_streak >= NO_IMPROVE_LIMIT:
            logger.info(
                "[PYTEST-REPAIR] Sin mejora en %s iteraciones CON patch aplicado; degradando a contract-lite.",
                no_improve_streak,
            )
            _degrade_to_contract_lite(nombre_proyecto=nombre_proyecto, estructura=estructura)
            ok2, out2, _ = _run_pytest(project_dir)

            # Política:
            # - Degradamos a contract-lite y re-ejecutamos pytest (suite mínima smoke+openapi).
            # - Si pasa => OK_DEGRADED (publicable).
            # - Si no pasa => ERROR (no publicable).
            if ok2:
                return _mk_result(
                    ok=True,
                    attempts=attempt,
                    last_output=out2,
                    patched_files=patched_total,
                    degraded=True,
                    degrade_type="contract-lite",
                )

            return _mk_result(
                ok=False,
                attempts=attempt,
                last_output=out2,
                patched_files=patched_total,
                degraded=True,
                degrade_type="contract-lite",
            )

        # compuerta: tests deben compilar
        if not _compile_tests(project_dir):
            logger.info("[PYTEST-REPAIR] compileall falló tras patch; se corta para evitar loops.")
            append_trace_event(project_dir, TraceEvent(ts=now_ts(), phase=subphase, attempt=attempt, action="compileall_failed"))
            break

        attempt += 1

    # Último recurso: si se agota presupuesto sin converger, degradar a contract-lite.
    # Esto unifica el comportamiento con PARCIAL: preferimos devolver una PoC ejecutable y verificable
    # (smoke+openapi) antes que abortar.
    try:
        logger.info("[PYTEST-REPAIR] Presupuesto agotado; degradando a contract-lite (último recurso).")
        _degrade_to_contract_lite(nombre_proyecto=nombre_proyecto, estructura=estructura)
        ok2, out2, _ = _run_pytest(project_dir)
        if ok2:
            return _mk_result(
                ok=True,
                attempts=attempt,
                last_output=out2,
                patched_files=patched_total,
                degraded=True,
                degrade_type="contract-lite",
            )
        return _mk_result(
            ok=False,
            attempts=attempt,
            last_output=out2,
            patched_files=patched_total,
            degraded=True,
            degrade_type="contract-lite",
        )
    except Exception:
        return _mk_result(ok=False, attempts=attempt, last_output=last_out, patched_files=patched_total)
