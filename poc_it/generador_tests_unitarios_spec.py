"""
PoC-it – Generador de Pruebas Unitarias (módulo independiente)

Objetivo
--------
Generar tests para proyectos FastAPI generados.

En modo PARCIAL el objetivo es inequívoco:
- Generar SIEMPRE una suite 100% HERMÉTICA (sin red, sin credenciales, sin DB real, sin env vars).
- Los tests deben alinearse con el wiring real (FastAPI + Depends) usando runtime_contracts/runtime_facts.
- Evitar prompts “ensayo”: el modelo debe producir un set de archivos fijo y un harness obligatorio.

Estrategia (PARCIAL / hermética)
--------------------------------
- Siempre: smoke import + openapi básico.
- Endpoints: tests contract-lite herméticos (status_code < 500) con dependency_overrides obligatorios.
- Se genera `tests/conftest.py` SIEMPRE y es donde vive el harness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from poc_it.generador.json_utils import extraer_json_tolerante
from poc_it.llm_client import chat_completion_json
from poc_it.poc_facts_extractor import extract_poc_facts_from_structure


@dataclass(frozen=True)
class GeneracionTestsResult:
    estructura_tests: Dict[str, str]
    errores: List[str]
    raw_llm: Optional[str] = None


def _spec_endpoints(spec: Optional[dict]) -> List[dict]:
    if not isinstance(spec, dict):
        return []
    eps = spec.get("endpoints", [])
    return eps if isinstance(eps, list) else []


def _runtime_contracts_min(estructura_generada: Dict[str, str]) -> dict | None:
    raw = estructura_generada.get(".poc_it/runtime_contracts.json")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        rc = json.loads(raw)
    except Exception:
        return None
    if not isinstance(rc, dict):
        return None

    endpoints_min: List[dict] = []
    for ep in (rc.get("endpoints") or []):
        if not isinstance(ep, dict):
            continue
        endpoints_min.append(
            {
                "path": ep.get("path"),
                "method": ep.get("method"),
                "module_path": ep.get("module_path"),
                "func_name": ep.get("func_name"),
                "status_code": ep.get("status_code"),
                "depends_imports": ep.get("depends_imports") or [],
                "injected_params": ep.get("injected_params") or [],
                "uses_injected": bool(ep.get("uses_injected")),
                "observed_calls": ep.get("observed_calls") or [],
                "request_required_fields": ep.get("request_required_fields") or [],
                "request_optional_fields": ep.get("request_optional_fields") or [],
                "response_model_required_fields": ep.get("response_model_required_fields") or [],
                "response_model_optional_fields": ep.get("response_model_optional_fields") or [],
            }
        )

    return {
        "tests_style": rc.get("tests_style") or "sync",
        "hermetic": bool(rc.get("hermetic") if rc.get("hermetic") is not None else True),
        "allowed_dependency_overrides": rc.get("allowed_dependency_overrides") or [],
        "endpoints": endpoints_min,
    }


def _build_prompt_tests(spec: Optional[dict], estructura_generada: Dict[str, str]) -> str:
    """
    Prompt reducido y determinista para generación de suite HERMÉTICA.
    No pedimos “tests genéricos”: pedimos un producto de salida concreto:
    - conftest.py (harness obligatorio)
    - test_smoke_import.py
    - test_openapi.py
    - test_endpoints_hermetic.py
    """
    facts = extract_poc_facts_from_structure(estructura_generada).to_dict()
    rc_min = _runtime_contracts_min(estructura_generada)
    spec_endpoints = _spec_endpoints(spec)

    return f"""
TAREA
Genera una SUITE DE TESTS HERMÉTICA para un proyecto FastAPI.

DEFINICIÓN DE "HERMÉTICO" (NO NEGOCIABLE)
- La suite debe pasar SIN base de datos real, SIN credenciales, SIN red, SIN variables de entorno.
- No debe requerir DATABASE_URL, GOOGLE_APPLICATION_CREDENTIALS ni similares.
- No debe ejecutar integraciones externas reales.
- Si el proyecto NO permite ejecutar endpoints sin tocar infra real, los tests deben seguir pasando (por aislamiento vía Depends).

FUENTE DE VERDAD (wiring/Depends)
- Usa RUNTIME_CONTRACTS_MIN como fuente de verdad para:
  - qué endpoints existen realmente
  - qué Depends(...) existen realmente (depends_imports)
  - qué símbolos puedes overridear (allowed_dependency_overrides)
- FACTS es respaldo anti-alucinación (no inventes imports).

INPUTS
RUNTIME_CONTRACTS_MIN (JSON):
{json.dumps(rc_min, ensure_ascii=False)}

FACTS (JSON, respaldo anti-alucinación):
{json.dumps(facts, ensure_ascii=False)}

SPEC.endpoints (intención, NO wiring):
{json.dumps(spec_endpoints, ensure_ascii=False)}

SALIDA (EXCLUSIVAMENTE JSON, y SOLO estos ficheros)
{{
  "files": [
    {{"path": "pytest.ini", "content": "..."}},
    {{"path": "tests/__init__.py", "content": ""}},
    {{"path": "tests/conftest.py", "content": "..."}},
    {{"path": "tests/test_smoke_import.py", "content": "..."}},
    {{"path": "tests/test_openapi.py", "content": "..."}},
    {{"path": "tests/test_endpoints_hermetic.py", "content": "..."}}
  ]
}}

OBLIGATORIO: harness en conftest.py
- Debes definir una fixture `client` (sync o async) según `RUNTIME_CONTRACTS_MIN.tests_style`.

Regla CRÍTICA (lifespan/startup):
- TestClient/AsyncClient ejecuta lifespan al entrar, ANTES de que puedas hacer requests.
- Muchos proyectos inicializan DB/engine/Settings en lifespan/startup y fallan si faltan env vars (p.ej. DATABASE_URL).
- Para que la suite sea hermética, DEBES evitar que el lifespan toque Settings/env vars.

Implementación OBLIGATORIA:
- Importa `app.main` como módulo (no solo `from app.main import app`) para poder monkeypatchear:
  - `import app.main as app_main`
  - `app = app_main.app`
- Antes de crear el cliente, monkeypatchea en `app_main`:
  - `app_main.get_engine` si existe, y/o
  - `app_main.get_settings` si existe
  para que NO lea env vars.
  - Si `get_engine` es sync: reemplázalo por una función sync que devuelva un fake engine.
  - Si `get_engine` es async: reemplázalo por una función async que devuelva un fake engine.
- El fake engine debe soportar como mínimo:
  - `.begin()` como async context manager
  - `conn.run_sync(fn)` como async (no-op)
- Después, aplica `app.dependency_overrides[...] = ...` para las dependencias necesarias.
- Limpia overrides al final (app.dependency_overrides.clear()).

REGLAS PARA dependency_overrides (OBLIGATORIAS)
- Solo puedes usar como key de dependency_overrides símbolos importables que estén listados en:
  `RUNTIME_CONTRACTS_MIN.allowed_dependency_overrides` (FQNs).
- Para cada endpoint en RUNTIME_CONTRACTS_MIN.endpoints:
  - Para cada fqn en `depends_imports`: debes crear un override en conftest.py para esa dependencia.
- Los overrides deben ser STUBS simples, in-memory, deterministas y (si procede) stateful por fixture.
- No intentes usar SDKs reales ni SQLAlchemy real. El stub debe cubrir solo lo que el código invoca.

IMPORTANTE:
- Aunque hagas dependency_overrides, eso NO evita fallos en lifespan si el proyecto crea engine/settings en startup.
- Por tanto, el monkeypatch de lifespan/startup descrito arriba es obligatorio incluso si `depends_imports` existe.

REGLA SYNC/ASYNC (OBLIGATORIA)
- Si tests_style == "sync":
  - Usar TestClient.
  - Fixtures y tests SYNC (def).
  - PROHIBIDO async def / await / pytest.mark.asyncio.
- Si tests_style == "async":
  - Usar httpx.AsyncClient.
  - Fixtures y tests ASYNC (async def) + pytest.mark.asyncio (o anyio) de forma consistente.

TESTS DE ENDPOINTS (OBLIGATORIOS, en test_endpoints_hermetic.py)
- Genera tests por endpoint (path+method) presentes en RUNTIME_CONTRACTS_MIN.endpoints (no inventes).
- Para cada endpoint:
  - Llama al endpoint con un payload mínimo válido:
    - si request_required_fields no está vacío, inclúyelos.
    - si está vacío y el endpoint tiene body, usa un payload pequeño con valores genéricos.
  - Assert principal: `assert response.status_code < 500`
  - Si status es 2xx y `response_model_required_fields` no está vacío, valida que esas keys existan en el JSON.

OPENAPI (en test_openapi.py)
- Valida que /openapi.json responde y contiene "paths". No valides contenido exacto.

SMOKE (en test_smoke_import.py)
- `import app.main` debe pasar.

Notas
- No uses `client.get(..., json=...)`. Si fuese necesario, usa `client.request("GET", ... , json=...)`, pero preferiblemente no envíes body en GET.
- No uses Markdown fences.
""".strip()


def _fallback_tests_minimos() -> Dict[str, str]:
    pytest_ini = """[pytest]
addopts = -q
testpaths = tests
python_files = test_smoke_import.py test_openapi.py test_endpoints_hermetic.py
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
    return {
        "pytest.ini": pytest_ini,
        "tests/__init__.py": "",
        "tests/test_smoke_import.py": smoke_import,
        "tests/test_openapi.py": openapi_test,
        "tests/test_endpoints_hermetic.py": "",
        "tests/conftest.py": "",
    }


def generar_tests_unitarios_minimos(
    *,
    nombre_proyecto: str,
    spec: Optional[dict],
    estructura_generada: Dict[str, str],
    intentos: int = 1,
    modo: Optional[str] = None,
) -> GeneracionTestsResult:
    # En PARCIAL, preferimos la suite hermética con endpoints aislados (LLM) y, si falla, fallback mínimo (smoke+openapi).
    errores: List[str] = []

    prompt = _build_prompt_tests(spec, estructura_generada)

    retry_budget = max(1, int(intentos or 1))
    # En PoCs con endpoints definidos en SPEC suele convenir insistir un poco.
    if _spec_endpoints(spec):
        retry_budget = max(retry_budget, 3)

    for _ in range(retry_budget):
        raw: Optional[str] = None
        try:
            raw = chat_completion_json(
                prompt=prompt,
                system=None,
                temperature=0.1,
                max_tokens=1800,
                provider_hint="code-gen",
                fase="generacion_codigo",
            )

            data = extraer_json_tolerante(raw) or {}
            files = data.get("files")
            if not isinstance(files, list) or not files:
                raise ValueError("Respuesta sin files[]")

            estructura_tests: Dict[str, str] = {}
            for f in files:
                if not isinstance(f, dict):
                    continue
                path = str(f.get("path") or "").replace("\\", "/").strip()
                content = f.get("content")
                if not path:
                    continue
                if path != "pytest.ini" and not path.startswith("tests/"):
                    continue
                if not isinstance(content, str):
                    content = ""
                estructura_tests[path] = content

            # Guardrails: artefactos obligatorios de la estrategia
            required_paths = {
                "tests/__init__.py",
                "tests/conftest.py",
                "tests/test_smoke_import.py",
                "tests/test_openapi.py",
                "tests/test_endpoints_hermetic.py",
            }
            missing = [p for p in sorted(required_paths) if p not in estructura_tests]
            if missing:
                raise ValueError(f"Faltan archivos obligatorios: {missing}")

            estructura_tests.setdefault(
                "pytest.ini",
                "[pytest]\naddopts = -q\ntestpaths = tests\npython_files = test_smoke_import.py test_openapi.py test_endpoints_hermetic.py\n",
            )

            return GeneracionTestsResult(estructura_tests=estructura_tests, errores=[], raw_llm=raw)

        except Exception as exc:
            errores.append(str(exc))
            continue

    return GeneracionTestsResult(
        estructura_tests=_fallback_tests_minimos(),
        errores=errores,
        raw_llm=None,
    )
