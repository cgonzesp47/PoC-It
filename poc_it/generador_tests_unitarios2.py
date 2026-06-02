"""
PoC-it – Generador de Pruebas Unitarias (módulo independiente)

Objetivo
--------
Generar un conjunto MÍNIMO de tests unitarios para el proyecto FastAPI generado,
basándose ÚNICAMENTE en el SPEC recibido (y, opcionalmente, en los paths/contenidos
materializados). NO debe asumir endpoints por defecto (p.ej. /health) salvo que el
SPEC lo incluya explícitamente.

Requisitos clave
----------------
- Los tests deben PASAR con alta probabilidad según el código generado.
- Debe realizar una nueva llamada al modelo a través del proxy usando el alias
  `code-gen` para beneficiarse de fallbacks.
- Módulo nuevo y aislado: se integrará desde `orquestador_parcial.py`.
- Mantenible, estructurado y fácil de leer.

Diseño
------
- Entrada: spec (dict), estructura_generada (dict path->content), nombre_proyecto
- Salida: dict path->content con tests y config mínima:
  - pytest.ini
  - tests/__init__.py
  - tests/test_smoke_import.py          (siempre)
  - tests/test_endpoints_hermetic.py    (si modo=PARCIAL y SPEC define endpoints)
  - tests/test_endpoints_spec.py        (si modo!=PARCIAL y SPEC define endpoints)

Notas de consistencia
---------------------
- En modo PARCIAL, este módulo NO debe generar NI mencionar `test_endpoints_spec.py`.
- El orquestador selecciona explícitamente una estrategia por modo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional

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


def _build_prompt_tests(spec: Optional[dict], estructura_generada: Dict[str, str]) -> str:
    endpoints = _spec_endpoints(spec)

    # Facts deterministas desde el código: evita alucinaciones (símbolos inexistentes, endpoints que no existen, etc.)
    facts = extract_poc_facts_from_structure(estructura_generada).to_dict()

    runtime_facts_raw = estructura_generada.get(".poc_it/runtime_facts.json")
    runtime_facts_json = None
    if isinstance(runtime_facts_raw, str) and runtime_facts_raw.strip():
        try:
            runtime_facts_json = json.loads(runtime_facts_raw)
        except Exception:
            runtime_facts_json = None

    runtime_contracts_raw = estructura_generada.get(".poc_it/runtime_contracts.json")
    runtime_contracts_json = None
    if isinstance(runtime_contracts_raw, str) and runtime_contracts_raw.strip():
        try:
            runtime_contracts_json = json.loads(runtime_contracts_raw)
        except Exception:
            runtime_contracts_json = None

    di_source = runtime_contracts_json if isinstance(runtime_contracts_json, dict) else None

    req_runtime = (estructura_generada.get("requirements.txt") or "").strip()
    req_dev = (estructura_generada.get("requirements-dev.txt") or "").strip()
    requirements_bundle = {"requirements.txt": req_runtime, "requirements-dev.txt": req_dev}

    paths = sorted(list(estructura_generada.keys()))
    main_py = estructura_generada.get("app/main.py", "")

    # IMPORTANTE:
    # No usar f-string con JSON literal que contenga `{}` sin escapar: Python lo interpreta como placeholders
    # y rompe con `Invalid format specifier ...`. Construimos el prompt con `.format()` y escapamos llaves.
    template = """
TAREA
Genera tests unitarios para un proyecto FastAPI basados en el SPEC y el CÓDIGO materializado.

OBJETIVO
- Los tests deben PASAR con alta probabilidad según el código existente.
- Usar el SPEC como contrato de intención, pero NO inventar símbolos: usa FACTS / RUNTIME_* como fuente de verdad para imports/paths reales.
- No modifiques archivos de la app (solo genera archivos de tests + pytest.ini).

REGLA MÁXIMA (OBLIGATORIA): SUITE 100% HERMÉTICA (SIN I/O REAL)
- PROHIBIDO red/credenciales/SDKs cloud/conexiones reales DB/servicios externos.
- Los tests deben ejecutar con `pytest` sin variables de entorno.
- Si no puedes aislar I/O con evidencia dura, degrada a tests de smoke import y/o OpenAPI.

FUENTES DE VERDAD (ORDEN DE PRIORIDAD PARA CABLEADO / IMPORTS / DI)
1) RUNTIME_CONTRACTS (si existe): DI/wiring/endpoints reales (path/method/module_path/depends_imports).
2) RUNTIME_FACTS (si existe).
3) FACTS (extraído del código).
4) SPEC: intención; NO fiable para wiring.

CRÍTICO: DEPENDENCY OVERRIDES
- Solo overridear dependencias expuestas por Depends(...).
- Fuente de verdad ÚNICA para decidir overrides: `RUNTIME_CONTRACTS.endpoints[*].depends_imports`.
- Si depends_imports está vacío, NO uses dependency_overrides y NO llames endpoints.

ESTRATEGIA (OBLIGATORIA)
A) Siempre generar:
- pytest.ini (addopts=-q)
- tests/test_smoke_import.py
B) Generar tests/test_openapi.py:
- Solo si `import app.main` funciona.
- Debe pedir `/openapi.json` y validar `status_code < 500` y que contiene "paths".
C) Generar tests/test_endpoints_hermetic.py SOLO si:
- Para cada endpoint que se vaya a llamar:
  - existe en OpenAPI, y
  - tiene depends_imports no vacío, y
  - para CADA dependencia en depends_imports puedes crear un override sin IO real (MagicMock/AsyncMock).
- Si no se cumple, NO llames ese endpoint: valida presencia en OpenAPI y ya.

REGLAS DE GENERACIÓN (OBLIGATORIAS)
- Devuelve SOLO paths bajo tests/ y pytest.ini.
- PROHIBIDO crear conftest.py (lo genera el sistema o un prompt especializado de stubs).
- PROHIBIDO tocar app.dependency_overrides en los tests (los overrides se hacen en tests/conftest.py).
- Devuelve SOLO JSON (sin Markdown).

REGLAS HERMÉTICAS Y DE COMPATIBILIDAD (OBLIGATORIAS)
- Si se importa `from fastapi.testclient import TestClient`:
  - PROHIBIDO `async def test_*`
  - PROHIBIDO `@pytest.mark.anyio` o `@pytest.mark.asyncio`
  - PROHIBIDO `await client.get/post/put/delete(...)`
- PROHIBIDO usar strings como clave en dependency_overrides:
  - NO: `app.dependency_overrides["get_db"] = ...`
  - SÍ: `from app.database import get_db` y `app.dependency_overrides[get_db] = override`
- PROHIBIDO intentar overridear módulos o funciones que NO sean dependencias de FastAPI.
- Solo overridear callables presentes en `RUNTIME_CONTRACTS.allowed_dependency_overrides`.
- En test_endpoints_hermetic.py:
  - NO definir fixtures (ni `client`) dentro del archivo. La fixture `client` vive en conftest.
  - NO usar `TestClient(app)` a nivel módulo. Usar la fixture `client`.

FACTS (JSON):
{FACTS_JSON}

RUNTIME_FACTS (JSON):
{RUNTIME_FACTS_JSON}

RUNTIME_CONTRACTS (JSON):
{RUNTIME_CONTRACTS_JSON}

DI_SOURCE (usar solo para depends_imports; JSON):
{DI_SOURCE_JSON}

SPEC.endpoints (intención; JSON):
{SPEC_ENDPOINTS_JSON}

REQUIREMENTS (allowlist; JSON):
{REQUIREMENTS_JSON}

CONTEXTO (paths existentes; JSON):
{PATHS_JSON}

app/main.py (referencia, trunc):
{MAIN_PY}

SALIDA (EXCLUSIVAMENTE JSON)
{{
  "files": [
    {{"path": "pytest.ini", "content": "..."}},
    {{"path": "tests/__init__.py", "content": ""}},
    {{"path": "tests/test_smoke_import.py", "content": "..."}},
    {{"path": "tests/test_openapi.py", "content": "..."}},
    {{"path": "tests/test_endpoints_hermetic.py", "content": "..."}}
  ]
}}
""".strip()

    return template.format(
        FACTS_JSON=json.dumps(facts, ensure_ascii=False),
        RUNTIME_FACTS_JSON=json.dumps(runtime_facts_json, ensure_ascii=False),
        RUNTIME_CONTRACTS_JSON=json.dumps(runtime_contracts_json, ensure_ascii=False),
        DI_SOURCE_JSON=json.dumps(di_source, ensure_ascii=False),
        SPEC_ENDPOINTS_JSON=json.dumps(endpoints, ensure_ascii=False),
        REQUIREMENTS_JSON=json.dumps(requirements_bundle, ensure_ascii=False),
        PATHS_JSON=json.dumps(paths, ensure_ascii=False),
        MAIN_PY=main_py[:5000],
    )


def _fallback_tests_minimos() -> Dict[str, str]:
    pytest_ini = """[pytest]
addopts = -q
testpaths = tests
"""
    smoke_import = """def test_import_app_main():
    import importlib

    mod = importlib.import_module("app.main")
    assert mod is not None
"""
    return {
        "pytest.ini": pytest_ini,
        "tests/__init__.py": "",
        "tests/test_smoke_import.py": smoke_import,
    }


def generar_tests_hermeticos_parcial(
    *,
    nombre_proyecto: str,
    spec: Optional[dict],
    estructura_generada: Dict[str, str],
    intentos: int = 2,
) -> GeneracionTestsResult:
    """Genera tests para modo PARCIAL (suite hermética o degradación contract-lite).

    Invariante: esta función NUNCA debe exigir ni referenciar `tests/test_endpoints_spec.py`.
    """
    endpoints = _spec_endpoints(spec)
    requires_endpoint_tests = bool(endpoints)

    try:
        prompt = _build_prompt_tests(spec, estructura_generada)
        retry_budget = max(2, int(intentos or 2))
        if requires_endpoint_tests:
            retry_budget = max(retry_budget, 3)

        raw_llm: Optional[str] = None
        last_errs: List[str] = []

        for _ in range(retry_budget):
            try:
                raw_llm = chat_completion_json(
                    prompt=prompt,
                    system=None,
                    temperature=0.1,
                    max_tokens=1800,
                    provider_hint="code-gen",
                    fase="generacion_codigo",
                )
                data = extraer_json_tolerante(raw_llm) or {}
                files = data.get("files")
                if not isinstance(files, list) or not files:
                    raise ValueError("Respuesta sin files[]")

                estructura_tests: Dict[str, str] = {}
                for f in files:
                    if not isinstance(f, dict):
                        continue
                    path = str(f.get("path") or "").replace("\\\\", "/").strip()
                    content = f.get("content")
                    if not path:
                        continue
                    if path != "pytest.ini" and not path.startswith("tests/"):
                        continue
                    if not isinstance(content, str):
                        content = ""
                    estructura_tests[path] = content

                estructura_tests.setdefault("tests/__init__.py", "")
                estructura_tests.setdefault("pytest.ini", "[pytest]\naddopts = -q\ntestpaths = tests\n")

                if "tests/test_smoke_import.py" not in estructura_tests:
                    raise ValueError("No se generó tests/test_smoke_import.py")

                if requires_endpoint_tests and "tests/test_endpoints_hermetic.py" not in estructura_tests:
                    raise ValueError("No se generó tests/test_endpoints_hermetic.py (PARCIAL + endpoints en SPEC)")

                return GeneracionTestsResult(estructura_tests=estructura_tests, errores=[], raw_llm=raw_llm)
            except Exception as exc:
                last_errs.append(str(exc))
                continue

        errores = last_errs or ["LLM sin respuesta válida en PARCIAL"]
    except Exception as exc:
        errores = [str(exc)]

    # Degradación: contract-lite (OpenAPI) + smoke.
    # Runtime-first estricto: NO enumeramos rutas desde SPEC para asserts (evita /products vs /productos).
    pytest_ini = """[pytest]
addopts = -q
testpaths = tests
markers =
    integration: tests que requieren integraciones externas reales (opt-in)
"""
    smoke_import = """def test_import_app_main():
    import importlib

    mod = importlib.import_module("app.main")
    assert mod is not None
"""
    openapi_test = '''from fastapi.testclient import TestClient
from app.main import app


def test_openapi_json_available():
    with TestClient(app) as client:
        resp = client.get("/openapi.json")
    assert resp.status_code < 500
    data = resp.json()
    assert "paths" in data
'''
    return GeneracionTestsResult(
        estructura_tests={
            "pytest.ini": pytest_ini,
            "tests/__init__.py": "",
            "tests/test_smoke_import.py": smoke_import,
            "tests/test_contract_lite_openapi.py": openapi_test,
        },
        errores=errores,
        raw_llm=None,
    )


def generar_tests_spec_no_parcial(
    *,
    nombre_proyecto: str,
    spec: Optional[dict],
    estructura_generada: Dict[str, str],
    intentos: int = 1,
) -> GeneracionTestsResult:
    """Genera tests para modos NO PARCIAL (suite legacy guiada por SPEC)."""
    errores: List[str] = []
    prompt = _build_prompt_tests(spec, estructura_generada)

    endpoints = _spec_endpoints(spec)
    requires_endpoint_tests = bool(endpoints)

    retry_budget = max(1, int(intentos or 1))
    if requires_endpoint_tests:
        retry_budget = max(retry_budget, 3)

    for _ in range(retry_budget):
        raw_llm: Optional[str] = None
        try:
            raw_llm = chat_completion_json(
                prompt=prompt,
                system=None,
                temperature=0.1,
                max_tokens=1800,
                provider_hint="code-gen",
                fase="generacion_codigo",
            )

            data = extraer_json_tolerante(raw_llm) or {}
            files = data.get("files")
            if not isinstance(files, list) or not files:
                raise ValueError("Respuesta sin files[]")

            estructura_tests: Dict[str, str] = {}
            for f in files:
                if not isinstance(f, dict):
                    continue
                path = str(f.get("path") or "").replace("\\\\", "/").strip()
                content = f.get("content")
                if not path:
                    continue
                if path != "pytest.ini" and not path.startswith("tests/"):
                    continue
                if not isinstance(content, str):
                    content = ""
                estructura_tests[path] = content

            estructura_tests.setdefault("tests/__init__.py", "")
            estructura_tests.setdefault("pytest.ini", "[pytest]\naddopts = -q\ntestpaths = tests\n")

            if "tests/test_smoke_import.py" not in estructura_tests:
                raise ValueError("No se generó tests/test_smoke_import.py")

            if requires_endpoint_tests and "tests/test_endpoints_spec.py" not in estructura_tests:
                raise ValueError("No se generó tests/test_endpoints_spec.py (SPEC incluye endpoints)")

            return GeneracionTestsResult(estructura_tests=estructura_tests, errores=[], raw_llm=raw_llm)
        except Exception as exc:
            errores.append(str(exc))
            continue

    return GeneracionTestsResult(estructura_tests=_fallback_tests_minimos(), errores=errores, raw_llm=None)


def generar_tests_unitarios_minimos(
    *,
    nombre_proyecto: str,
    spec: Optional[dict],
    estructura_generada: Dict[str, str],
    intentos: int = 1,
    modo: Optional[str] = None,
) -> GeneracionTestsResult:
    """Wrapper de compatibilidad: mantiene la firma original.

    Selección explícita por modo:
    - PARCIAL -> generar_tests_hermeticos_parcial
    - resto  -> generar_tests_spec_no_parcial
    """
    if str(modo or "").upper() == "PARCIAL":
        return generar_tests_hermeticos_parcial(
            nombre_proyecto=nombre_proyecto,
            spec=spec,
            estructura_generada=estructura_generada,
            intentos=max(2, int(intentos or 1)),
        )

    return generar_tests_spec_no_parcial(
        nombre_proyecto=nombre_proyecto,
        spec=spec,
        estructura_generada=estructura_generada,
        intentos=int(intentos or 1),
    )
