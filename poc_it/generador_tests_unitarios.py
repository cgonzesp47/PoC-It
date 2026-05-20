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
  - tests/test_smoke_import.py      (siempre)
  - tests/test_endpoints_spec.py    (si SPEC define endpoints)

Estrategia de tests (anti-frágil pero útil)
------------------------------------------
- Siempre: smoke test de `import app.main`.
- Para cada endpoint del SPEC, generar tests basados en contrato:
  1) **Smoke/wiring**: el endpoint debe responder y nunca devolver 500 por errores de import/DI.
  2) **Contrato mínimo**:
     - Validar el tipo de request (none/json/multipart/query) según SPEC.
     - Si SPEC define `response.json_example`, validar que el JSON de respuesta tenga al menos
       las mismas claves de primer nivel (cuando el status sea 2xx).
  3) **Negativos útiles**:
     - Si request.type == "json" y hay `required`, enviar `{}` y esperar 4xx (normalmente 422),
       y confirmar que **no** es 500.
     - Si request.type == "multipart": si hay un campo `file`/binary en schema, enviar un archivo
       de prueba y esperar `<500`. Si faltan campos requeridos, esperar 4xx.
- No se hacen asserts de 200/201 salvo que el SPEC lo deje muy claro y el endpoint sea determinista.
- No se prueba integración real externa salvo que el código lo permita
  sin credenciales; en ese caso se prueba el comportamiento de error esperado (4xx) y no 500.

Fallback (si LLM falla)
-----------------------
- Se genera solo el smoke import test (y pytest.ini).
- No se asumen endpoints: los demás tests dependen del SPEC y de la respuesta del modelo.

Nota sobre dependencias
-----------------------
Los proyectos generados suelen incluir FastAPI; para tests típicamente se requiere:
- pytest
- httpx (dependencia de TestClient; a veces ya viene, pero se recomienda añadirla)
Este módulo NO modifica requirements; la integración en el orquestador deberá:
- separar deps runtime vs dev/tests (requirements.txt vs requirements-dev.txt)
- añadir pytest/pytest-mock/httpx como dev deps cuando se generen tests
parchear requirements.txt si existe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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

    paths = sorted(list(estructura_generada.keys()))
    main_py = estructura_generada.get("app/main.py", "")

    return f"""
TAREA
Genera tests unitarios para un proyecto FastAPI basados en el SPEC y el CÓDIGO materializado.

OBJETIVO
- Los tests deben PASAR con alta probabilidad según el código existente.
- Usar el SPEC como contrato de intención, pero NO inventar símbolos: usa FACTS (extraído del código) como fuente de verdad para imports/monkeypatch/paths reales.
- Mejorar la cobertura respecto a “assert status_code < 500” sin volverlos frágiles.
- No modifiques archivos de la app (solo genera archivos de tests + pytest.ini).

FUENTES DE VERDAD
- SPEC.endpoints: describe lo que se pretendía generar.
- FACTS.endpoints: describe lo que realmente existe en el código (rutas, métodos, módulos, dependencias).
  Si hay conflicto, los tests deben alinearse con FACTS para que pasen, y reflejar en asserts sólo lo que el código garantiza.

FACTS (extraído del código; JSON):
{json.dumps(facts, ensure_ascii=False)}

FUENTE DE VERDAD: SPEC.endpoints (intención)
{json.dumps(endpoints, ensure_ascii=False)}

CONTEXTO (referencia; no inventar nada)
- Paths existentes:
{json.dumps(paths, ensure_ascii=False)}

- app/main.py (si existe; referencia):
{main_py[:5000]}

SALIDA (EXCLUSIVAMENTE JSON)
{{
  "files": [
    {{"path": "pytest.ini", "content": "..."}},
    {{"path": "tests/__init__.py", "content": ""}},
    {{"path": "tests/test_smoke_import.py", "content": "..."}},
    {{"path": "tests/test_endpoints_spec.py", "content": "..."}}
  ]
}}

REGLAS DE GENERACIÓN (OBLIGATORIAS)
- Devuelve SOLO paths bajo tests/ y pytest.ini.
- Usa pytest.
- Usa fastapi.testclient.TestClient y `from app.main import app`.
- pytest.ini NO debe incluir opciones de cobertura (`--cov`, `--cov-report`) porque pytest-cov puede no estar instalado.
  Usa como addopts mínimo: `-q`.

- NO inventes símbolos para monkeypatch:
  - Si necesitas aislar integraciones externas, usa FACTS.endpoints[*].module_path y FACTS.endpoints[*].depends para localizar el punto real de inyección.
  - Si no existe un símbolo real, NO lo uses.

- En endpoints parciales/no deterministas:
  - No asumas que la respuesta de 200 tendrá un JSON exacto (puede ser {{}} o {{"status":"ok"}}).
  - En su lugar, valida invariantes: status code, tipo (dict), y claves mínimas si el SPEC da json_example.

- Si el endpoint depende de integraciones externas o env vars, NO exijas 200 “real”.
  En su lugar, **aísla la dependencia con monkeypatch del dependency (Depends)** para convertir el test en unitario y determinista.
  Si no hay punto de inyección, el test debe validar el comportamiento con configuración ausente (4xx) según el SPEC.

- Incluye SIEMPRE `test_import_app_main`: `import app.main` debe pasar. (Este es el único test “arranque”.)

- Para cada endpoint del SPEC, genera EXACTAMENTE estos tests (si aplican):
  1) test_<name>_contract_valid_request:
     - Construye una request válida *exclusivamente* desde el SPEC:
       * none: sin body
       * json: `json=<payload>` donde payload contiene todos los `required` y respeta tipos
       * query: `params=<payload>` si hay schema; si no hay schema, omite params
       * multipart: SOLO si el SPEC tiene schema con campos binarios; si no hay información, omite este test
     - Si el endpoint parece integrar externos (típico: imports de `google*`, `boto3`, `requests`, `httpx`, clientes SDK):
       **OBLIGATORIO**: usar monkeypatch para reemplazar la llamada externa con un stub determinista.
       - Preferir monkeypatch del método del servicio usado por el endpoint
       - El stub debe devolver datos compatibles con el SPEC (p.ej. un id fijo como "abc123")
     - Assert de status_code:
       * Si se aisló con monkeypatch, entonces exigir 200/201 si el SPEC tiene `response.json_example`.
       * Si no se aisló (no había punto claro), entonces NO exigir 200: validar un 4xx/5xx esperado por SPEC.errors/contracts.
     - Validación de respuesta:
       * Si SPEC.response.json_example existe y la respuesta es 2xx:
         - validar que `resp.json()` sea dict
         - validar que contenga al menos las keys top-level de json_example (no validar valores exactos)

  2) test_<name>_contract_missing_required (solo si request.type == "json" y schema.required existe):
     - Llamar con `json={{}}`.
     - Esperar 422 o 400 (4xx) y validar que NO es 2xx.

  3) test_<name>_method_not_allowed:
     - Llamar con un método incorrecto y esperar 405 (esto es determinista en FastAPI).

- OBLIGATORIO: Los tests deben utilizar el SPEC como fuente de verdad:
  - No inventes paths, métodos ni cuerpos.
  - No inventes required fields: usa `schema.required`.
  - No inventes keys de respuesta: usa `response.json_example`.

- No uses Markdown fences (```).
""".strip()


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


def generar_tests_unitarios_minimos(
    *,
    nombre_proyecto: str,
    spec: Optional[dict],
    estructura_generada: Dict[str, str],
    intentos: int = 1,
) -> GeneracionTestsResult:
    errores: List[str] = []
    prompt = _build_prompt_tests(spec, estructura_generada)

    for _ in range(max(1, intentos)):
        raw: Optional[str] = None
        try:
            raw = chat_completion_json(
                prompt=prompt,
                system=None,
                temperature=0.1,
                max_tokens=1800,
                provider_hint="code-gen",  # alias del proxy para codegen + fallbacks
                fase="generacion_codigo",
            )

            # `chat_completion_json` puede devolver texto no parseable si el modelo incumple;
            # aquí aplicamos parsing estricto y dejamos que el caller use fallback.
            data = json.loads(raw)
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

            # Guardrails mínimos
            estructura_tests.setdefault("tests/__init__.py", "")
            if "tests/test_smoke_import.py" not in estructura_tests:
                raise ValueError("No se generó tests/test_smoke_import.py")

            estructura_tests.setdefault(
                "pytest.ini",
                "[pytest]\\naddopts = -q\\ntestpaths = tests\\n",
            )

            return GeneracionTestsResult(estructura_tests=estructura_tests, errores=[], raw_llm=raw)

        except Exception as exc:
            errores.append(str(exc))
            # reintentar (si procede) en la misma función; el proxy ya hace fallbacks por alias
            continue

    return GeneracionTestsResult(
        estructura_tests=_fallback_tests_minimos(),
        errores=errores,
        raw_llm=None,
    )
