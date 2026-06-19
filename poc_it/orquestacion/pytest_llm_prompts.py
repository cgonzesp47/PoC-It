from __future__ import annotations
"""
Prompts especializados para reparación LLM de tests.

Motivación
----------
Separar el loop C en dos sub-loops:
- C1: reparar harness (fixtures/overrides/imports/request shaping base) para que la suite sea "runnable"
- C2: reparar asserts / expectations (contract-lite) para converger a verde

Ambos prompts consumen:
- pytest_output (truncado)
- test_repair_context (artefacto compacto, normativo)
- tests actuales (fuente de verdad)
"""

import json
from typing import Dict


def _files_payload(current_tests: Dict[str, str]) -> str:
    files = [{"path": p, "content": c} for p, c in sorted((current_tests or {}).items())]
    return json.dumps(files, ensure_ascii=False)


def build_prompt_c1_harness_repair(
    *,
    pytest_output: str,
    current_tests: Dict[str, str],
    test_repair_context: dict,
    runtime_contracts: dict | None = None,
    runtime_facts: dict | None = None,
    stub_signatures: dict | None = None,
    endpoint_code: dict | None = None,
    pytest_json_report: dict | None = None,
) -> str:
    return f"""
TAREA (C1 - HARNESS REPAIR)
Pytest está fallando. Arregla SOLO el HARNESS de tests para que la suite sea ejecutable.

Definición de \"harness\" aquí:
- fixtures (conftest.py, client fixture)
- dependency_overrides / monkeypatch
- request shaping mínimo para evitar 422 triviales (params/json basados en context.endpoints)
- imports de tests (no imports de SDKs/drivers de integraciones externas)

PROHIBIDO
- Modificar app/**.
- Cambiar lógica de negocio de tests (asserts detallados) salvo si es necesario para evitar errores de ejecución.

REGLA ABSOLUTA (PIPELINE C)
- PROHIBIDO modificar archivos de producción: app/**, requirements.txt, README*.md, spec.json.
- Si necesitas cambiar comportamiento de la app para que los tests pasen, debes hacerlo con dependency_overrides/monkeypatch en tests.

HERMETICIDAD (OBLIGATORIO)
- No red, no DB real, no credenciales, no os.getenv requerido.
- Si app intenta integraciones externas, aislar con dependency_overrides.

FUENTE DE VERDAD (OBLIGATORIO)
- Usa SOLO este contexto normativo y runtime_contracts para decidir overrides y request-shape:
{json.dumps(test_repair_context, ensure_ascii=False)}

RUNTIME_CONTRACTS (OBLIGATORIO)
- Este objeto describe el wiring real detectado (endpoints, depends, allowed overrides).
- Incluye hints para tests/stubs: endpoints[*].sample_request, sample_response, assert_policy.
- Debes basarte en él para elegir correctamente qué dependencia overridear y con qué firma (sync/async).
{json.dumps(runtime_contracts or {}, ensure_ascii=False)}

STUB_SIGNATURES (OBLIGATORIO SI ESTÁ DISPONIBLE)
- Describe qué métodos se llaman y si son awaited. Úsalo para:
  - crear stubs con métodos correctos (nombres exactos)
  - devolver objetos awaitables cuando corresponda
  - evitar el patrón `await None`
{json.dumps(stub_signatures or {}, ensure_ascii=False)}

ENDPOINT CODE (SI ESTÁ DISPONIBLE)
- Solo el/los archivo(s) de endpoint implicados en el fallo (no toda la app).
- Úsalo para ver el Depends real y el nombre exacto de la dependencia.
{json.dumps(endpoint_code or {}, ensure_ascii=False)}

PYTEST OUTPUT (head 12000)
{(pytest_output or '')[:12000]}

PYTEST JSON REPORT (si disponible; fuente estructurada, truncado)
{json.dumps(pytest_json_report or {}, ensure_ascii=False)[:12000]}

TESTS ACTUALES (archivos completos)
{_files_payload(current_tests)}

SALIDA (JSON patch)
{{"files":[{{"path":"tests/...", "content":"..."}}, {{"path":"pytest.ini","content":"..."}}, {{"path":"requirements-dev.txt","content":"..."}}]}}

REGLAS DE IMPORTS
- stdlib permitido implícitamente
- app.* permitido
- cualquier third-party debe estar en requirements*. Si necesitas uno para tests herméticos, añade a requirements-dev.txt.
- NO añadas SDKs/drivers de integraciones externas para “arreglar” tests.

REGLAS DE OVERRIDES (OBLIGATORIO)
- Solo puedes overridear dependencias listadas en context.allowed_dependency_overrides.
- Si el error menciona \"Fixture ... called directly\": NO uses fixtures como override callable. Overrides deben ser funciones normales.

REGLAS CRÍTICAS (ANTI-PATRONES) (OBLIGATORIO)
- `tests/conftest.py` es el ÚNICO lugar para construir el `TestClient` y aplicar `app.dependency_overrides`.
- Todos los tests deben recibir el client vía inyección de fixture: `def test_xxx(client): ...`
- PROHIBIDO en cualquier `tests/test_*.py`:
  - `client = TestClient(app)` en top-level (a nivel módulo)
  - mutar `app.dependency_overrides[...] = ...` en top-level (a nivel módulo)
  - crear un `TestClient(app)` dentro del propio test (salvo que el test sea `test_openapi.py` muy específico)
- Si detectas que un test hace cualquiera de esos 3 anti-patrones:
  - reescribe el test para usar el fixture `client`
  - y mueve el wiring a `tests/conftest.py`

- Si usas `from fastapi.testclient import TestClient`:
  - PROHIBIDO: `async def test_*`
  - PROHIBIDO: `await client.get/post/put/delete(...)`
  - Los tests deben ser `def test_*` (sync).

- FIX ESPECÍFICO (CASO REAL): fixture `client` embebida en un test file
  - Si existe `@pytest.fixture async def client(): return TestClient(app)` dentro de un `tests/test_*.py`, debes arreglarlo.
  - Opción A (preferida): mover la fixture a `tests/conftest.py` como fixture SYNC y eliminarla del test file.
  - Opción B: convertir esa fixture local a SYNC (quitar `async`) y hacer `yield TestClient(app)` con cleanup.

- PROHIBIDO: declarar `@pytest.fixture async def ...` para luego usarla como `app.dependency_overrides[...] = fixture`.
  - En su lugar, define overrides inline dentro del fixture `client()` (sync fixture) usando `async def override(): ...`

- PROHIBIDO: `client.get(..., json=...)`.

RECETA CANÓNICA (USAR COMO BASE)
```py
@pytest.fixture
def client():
    async def override_dep_1():
        # return o yield stub hermético
        ...

    app.dependency_overrides[dep_1] = override_dep_1

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
```

REGLA DE RESPUESTA (OBLIGATORIA)
- Si creas stubs/services que devuelven dicts, deben incluir TODAS las claves required del response_model
  (ver context.endpoints[*].response_json_required_keys) para evitar ResponseValidationError.
""".strip()


def build_prompt_c2_asserts_repair(
    *,
    pytest_output: str,
    current_tests: Dict[str, str],
    test_repair_context: dict,
    runtime_contracts: dict | None = None,
    stub_signatures: dict | None = None,
    pytest_json_report: dict | None = None,
) -> str:
    return f"""
TAREA (C2 - ASSERTS REPAIR)
Pytest está fallando pero la suite ya debe ser ejecutable. Ajusta SOLO ASSERTS / EXPECTATIONS para que pase.

POLICY ANTI-PERMISIVIDAD (OBLIGATORIO)
- Está PROHIBIDO usar listas amplias de status codes tipo: [200, 201, 404, 405, 422] o `in (200, 201, 404, 405, 422)`.
- Los asserts de status_code deben derivarse de OpenAPI/runtime_contracts, con esta política:

1) Si el endpoint NO existe en OpenAPI:
   - ÚNICO permitido: assert status_code in {404, 405}
   - No se permite <500 genérico.

2) Si el endpoint SÍ existe en OpenAPI:
   - POST: esperado {200, 201} (usa exactamente este set).
   - GET: esperado {200, 404} (si id puede no existir).
   - PUT/PATCH: esperado {200, 404}.
   - DELETE: esperado {200, 204, 404}.
   - Si runtime_contracts especifica explícitamente otro código, manda runtime_contracts.

3) Si hay integraciones externas no stubbeables:
   - No relajes a “aceptar todo”.
   - Degrada el test a contract-lite (ej. /openapi.json) o smoke import, pero mantén la política de códigos anterior.

Objetivo
- Convertir tests frágiles a contract-lite sin perder señal:
  - evita equality exacta de response.json()
  - asserts por subset de keys/valores relevantes
  - NO uses status_code < 500 como comodín (salvo en smoke/openapi).
  - evitar exigir 200 vs 201 en POST (acepta ambos si aplica)

PROHIBIDO
- Modificar app/**.
- Reescribir el harness de overrides salvo que sea estrictamente necesario para evitar 5xx.

REGLA DE HARNESS (OBLIGATORIA, TAMBIÉN EN C2)
- `tests/conftest.py` es la fuente de verdad del `client` y de `dependency_overrides`.
- Todos los tests deben usar el fixture `client` (inyección de pytest).
- PROHIBIDO en `tests/test_*.py`:
  - `client = TestClient(app)` en top-level
  - mutar `app.dependency_overrides[...] = ...` en top-level
  - crear un `TestClient(app)` ad-hoc por test
- Si un test viola estas reglas, reescríbelo para usar fixture `client` y mueve wiring a conftest.

FUENTE DE VERDAD (OBLIGATORIO)
- Usa este contexto normativo para required keys y request-shape:
{json.dumps(test_repair_context, ensure_ascii=False)}

RUNTIME_CONTRACTS (DISPONIBLE)
- Describe wiring real detectado (endpoints, depends, allowed overrides, OpenAPI).
- Incluye hints para asserts/stubs: endpoints[*].sample_request, sample_response, assert_policy.
- Úsalo para decidir status codes plausibles y claves mínimas del response_model.
{json.dumps(runtime_contracts or {}, ensure_ascii=False)}

STUB_SIGNATURES (DISPONIBLE)
- Describe las firmas/capacidades detectadas de stubs/harness (p.ej. qué métodos existen, qué devuelven).
- Úsalo para no pedir asserts imposibles (ej. exigir `id` si el stub no lo genera).
{json.dumps(stub_signatures or {}, ensure_ascii=False)}

PYTEST OUTPUT (head 12000)
{(pytest_output or '')[:12000]}

PYTEST JSON REPORT (si disponible; fuente estructurada, truncado)
{json.dumps(pytest_json_report or {}, ensure_ascii=False)[:12000]}

TESTS ACTUALES (archivos completos)
{_files_payload(current_tests)}

SALIDA (JSON patch)
{{"files":[{{"path":"tests/...", "content":"..."}}, {{"path":"pytest.ini","content":"..."}}, {{"path":"requirements-dev.txt","content":"..."}}]}}

REGLA CRÍTICA
- PROHIBIDO: `assert response.json() == {{...}}`
""".strip()
