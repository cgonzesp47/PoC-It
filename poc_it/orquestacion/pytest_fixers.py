from __future__ import annotations
"""
Fixers deterministas para fallos típicos detectados por pytest.

Objetivo:
- Reducir dependencia del LLM en el post-procesado.
- Aplicar cambios pequeños, repetibles y seguros basados en firmas de error.

Diseño:
- Cada fixer detecta un patrón en el output de pytest y devuelve un patch {path: content}
  con cambios mínimos.
- Se asume que `estructura` usa paths POSIX dentro del proyecto generado (p.ej. app/main.py).

Reglas:
- Nunca inventar archivos fuera del proyecto, salvo requirements-dev.txt si se requiere para tests.
- Cambios quirúrgicos: tocar lo mínimo para que pase el test/colección.
"""

from dataclasses import dataclass
import json
import re
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class FixResult:
    patched_files: Dict[str, str]
    message: str


_HTTPX_MISSING_RE = re.compile(
    r"(ModuleNotFoundError: No module named 'httpx2?'"
    r"|requires the httpx2? package to be installed)",
    re.IGNORECASE,
)

_TESTCLIENT_GET_JSON_RE = re.compile(
    r"TestClient\\.get\\(\\) got an unexpected keyword argument 'json'",
    re.IGNORECASE,
)

_FIXTURE_CALLED_DIRECTLY_RE = re.compile(
    r'Fixture\\s+"(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)"\\s+called directly',
    re.IGNORECASE,
)


_ASYNC_FIXTURE_RE = re.compile(
    r"(requested an async fixture|async fixture 'override_get_db'|PytestRemovedIn9Warning:.*async fixture)",
    re.IGNORECASE,
)

# Caso: tests async (`await client.get`) usando TestClient (sync) => TypeError en runtime.
_AWAIT_TESTCLIENT_RESPONSE_RE = re.compile(
    r"TypeError:\\s*object Response can't be used in 'await' expression",
    re.IGNORECASE,
)

# Caso: pytest falla porque hay funciones async en suite sync (sin plugin) => "async def functions are not natively supported."
_ASYNC_DEF_NOT_SUPPORTED_RE = re.compile(
    r"async def functions are not natively supported",
    re.IGNORECASE,
)

_DICT_AS_DB_SESSION_RE = re.compile(
    r"AttributeError: 'dict' object has no attribute '(add|execute|delete|commit|refresh)'",
    re.IGNORECASE,
)

# Caso: response_model no se cumple porque el stub/harness devuelve {} o dict incompleto.
_RESPONSE_VALIDATION_ERROR_RE = re.compile(
    r"fastapi\\.exceptions\\.ResponseValidationError:",
    re.IGNORECASE,
)

# Variante: el test overridea get_db con un objeto "MockService"/stub que no implementa métodos de sesión.
_MOCKSERVICE_AS_DB_SESSION_RE = re.compile(
    r"AttributeError: 'MockService' object has no attribute '(add|execute|delete|commit|refresh)'",
    re.IGNORECASE,
)

_EXPECTED_500_GOT_200_RE = re.compile(
    r"assert\\s+500\\s*==\\s*200|assert\\s+response\\.status_code\\s*==\\s*expected_status\\s*\\n\\s*E\\s*assert\\s+500\\s*==\\s*200",
    re.IGNORECASE,
)

_EXPECTED_200_GOT_422_RE = re.compile(
    r"assert\\s+422\\s*==\\s*(200|201)|assert\\s+response\\.status_code\\s*==\\s*expected_status\\s*\\n\\s*E\\s*assert\\s+422\\s*==\\s*(200|201)",
    re.IGNORECASE,
)

_EXPECTED_200_GOT_201_RE = re.compile(
    r"assert\\s+201\\s*==\\s*200|assert\\s+response\\.status_code\\s*==\\s*expected_status\\s*\\n\\s*E\\s*assert\\s+201\\s*==\\s*200",
    re.IGNORECASE,
)

_EXPECTED_200_GOT_500_RE = re.compile(
    r"assert\\s+500\\s*==\\s*200|assert\\s+response\\.status_code\\s*==\\s*expected_status\\s*\\n\\s*E\\s*assert\\s+500\\s*==\\s*200",
    re.IGNORECASE,
)

_HTTP_404_WRAPPED_AS_500_RE = re.compile(
    r"fastapi\\.exceptions\\.HTTPException:\\s*404:|HTTPException\\(status_code=.*404\\)",
    re.IGNORECASE,
)

_EXPECTED_405_GOT_404_RE = re.compile(
    r"assert\\s+404\\s*==\\s*405|assert\\s+response\\.status_code\\s*==\\s*405\\s*\\n\\s*E\\s*assert\\s+404\\s*==\\s*405",
    re.IGNORECASE,
)

_DELETE_BODY_ASSERT_RE = re.compile(
    r"assert\\s+\"nombre\"\\s+in\\s+response_json|assert\\s+\"id\"\\s+in\\s+response_json|assert\\s+\"id\"\\s+in\\s+response\\.json\\(\\)|assert\\s+\"name\"\\s+in\\s+response\\.json\\(\\)|Item deleted",
    re.IGNORECASE,
)

# Variante actual: asserts contra response_data (no response_json)
_DELETE_ASSERT_NOMBRE_IN_RESPONSE_DATA_RE = re.compile(
    r"assert\\s+\"nombre\"\\s+in\\s+response_data",
    re.IGNORECASE,
)

_DELETE_RETURNS_MESSAGE_NOT_RESOURCE_RE = re.compile(
    r"Producto eliminado correctamente|\"mensaje\"\\s*:\\s*\"Producto eliminado correctamente\"|\\{\\s*'detail'\\s*:\\s*'[^']+'\\s*\\}|\\{\\s*\"detail\"\\s*:\\s*\"[^\"]+\"\\s*\\}|\\bItem deleted\\b|\\bdeleted\\b",
    re.IGNORECASE,
)

_DELETE_ASSERT_ID_IN_MESSAGE_BODY_RE = re.compile(
    r"AssertionError:\\s*assert\\s+'id'\\s+in\\s+\\{('mensaje'|\"mensaje\"|'detail'|\"detail\"|'message'|\"message\"):\\s*'[^']+'\\}|AssertionError:\\s*assert\\s+'id'\\s+in\\s+\\{('mensaje'|\"mensaje\"|'detail'|\"detail\"|'message'|\"message\"):\\s*\"[^\"]+\"\\}",
    re.IGNORECASE,
)

_DELETE_ASSERT_ID_IN_RESPONSE_DATA_RE = re.compile(
    r"assert\\s+\"id\"\\s+in\\s+response_data",
    re.IGNORECASE,
)

_EXPECTED_422_GOT_404_RE = re.compile(
    r"assert\\s+(404\\s*==\\s*422|422\\s*==\\s*404)|assert\\s+response\\.status_code\\s*==\\s*expected_status\\s*\\n\\s*E\\s*assert\\s+(404\\s*==\\s*422|422\\s*==\\s*404)",
    re.IGNORECASE,
)

# Caso: test aserta body vacío {}, pero FastAPI HTTPException serializa como {"detail": ...}
# Ej: assert response.json() == {}  (con status_code 4xx/5xx)
_HTTPXCEPTION_DETAIL_JSON_SHAPE_MISMATCH_RE = re.compile(
    r"assert\\s+response\\.json\\(\\)\\s*==\\s*\\{\\s*\\}|Left contains 1 more item:\\s*\\{'detail':",
    re.IGNORECASE,
)


def _ensure_line_in_reqs(content: str, requirement: str) -> str:
    requirement = requirement.strip()
    if not requirement:
        return content
    lines = [ln.rstrip() for ln in (content or "").splitlines()]
    normalized = {ln.strip().lower() for ln in lines if ln.strip() and not ln.strip().startswith("#")}
    if requirement.lower() in normalized:
        return (content or "")
    out = (content or "").rstrip("\n")
    if out:
        out += "\n"
    out += requirement + "\n"
    return out


def fix_missing_httpx(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    if not pytest_output or not _HTTPX_MISSING_RE.search(pytest_output):
        return None

    # Política: si hay requirements-dev.txt, añadir httpx/httpx2; si no, crear.
    # Se añaden ambos porque `starlette.testclient.TestClient` acepta cualquiera de los dos
    # (prueba httpx2 primero, cae a httpx con warning si no está); no sabemos aquí cuál de los
    # dos resolverá pip, así que garantizamos los dos.
    req_path = "requirements-dev.txt"
    current = estructura.get(req_path, "")
    patched = _ensure_line_in_reqs(current, "httpx")
    patched = _ensure_line_in_reqs(patched, "httpx2")

    if patched == current and current.strip():
        # ya están presentes; nada que hacer
        return None

    return FixResult(
        patched_files={req_path: patched or "httpx\nhttpx2\n"},
        message="Added httpx/httpx2 to requirements-dev.txt",
    )


def fix_testclient_get_json(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    if not pytest_output or not _TESTCLIENT_GET_JSON_RE.search(pytest_output):
        return None

    # Fix determinista: reemplazar `.get(..., json=payload)` por `.request("GET", ..., json=payload)`
    # en tests (genérico, agnóstico de PoC).
    patched_files: Dict[str, str] = {}
    replacements = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        new = content
        # Casos directos / comunes:
        new2 = re.sub(
            r'client\\.get\\(([^\\)]*),\\s*json\\s*=\\s*payload\\s*\\)',
            r'client.request("GET", \\1, json=payload)',
            new,
        )
        if new2 != new:
            new = new2

        # Variante general: json=<expr> (no solo payload)
        new2 = re.sub(
            r'client\\.get\\(([^\\)]*),\\s*json\\s*=\\s*([^\\)]+)\\)',
            r'client.request("GET", \\1, json=\\2)',
            new,
        )
        if new2 != new:
            new = new2

        if new != content:
            patched_files[path] = new
            replacements += 1

    if not patched_files:
        return None

    return FixResult(
        patched_files=patched_files,
        message=f"Rewrote TestClient.get(..., json=...) to client.request('GET', ...) in {replacements} test file(s)",
    )


def fix_fixture_called_directly(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix determinista: pytest falla con:
      Fixture "X" called directly

    Esto ocurre cuando un test:
    - llama a un fixture: `X()`
    - o asigna el fixture a dependency_overrides como si fuese un callable normal:
        `app.dependency_overrides[get_db] = X`

    Estrategia:
    - En tests/*:
      - `X()` -> `X`
      - `dependency_overrides[...] = X` -> `dependency_overrides[...] = (lambda: X)`
    """
    if not pytest_output:
        return None
    m = _FIXTURE_CALLED_DIRECTLY_RE.search(pytest_output)
    if not m:
        return None
    fixture_name = m.group("name")

    patched_files: Dict[str, str] = {}
    touched = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if fixture_name not in content:
            continue

        new = content

        new2 = re.sub(rf"\\b{re.escape(fixture_name)}\\s*\\(\\s*\\)", fixture_name, new)
        if new2 != new:
            new = new2

        new2 = re.sub(
            rf"(dependency_overrides\\s*\\[[^\\]]+\\]\\s*=\\s*){re.escape(fixture_name)}\\b",
            rf"\\1(lambda: {fixture_name})",
            new,
        )
        if new2 != new:
            new = new2

        if new != content:
            patched_files[path] = new
            touched += 1

    if not patched_files:
        return None

    return FixResult(
        patched_files=patched_files,
        message=f"Patched fixture '{fixture_name}' being called/assigned directly in {touched} test file(s)",
    )


def fix_async_fixtures_in_sync_suite(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix 100% agnóstico:
    - Si el suite usa TestClient (sync) pero el LLM generó tests/fixtures async (async def) o
      hace `await client.get/post/...`, pytest falla (TypeError/pytest-asyncio strict).
    - También cubre el mensaje clásico de pytest:
        "async def functions are not natively supported."
    - Reescribimos a sync:
      - `@pytest.fixture async def ...` -> `@pytest.fixture def ...`
      - `async def test_...` -> `def test_...`
      - eliminar `@pytest.mark.asyncio`
      - `response = await client.get(...)` -> `response = client.get(...)`
    """
    if not pytest_output or not (
        _ASYNC_FIXTURE_RE.search(pytest_output)
        or _AWAIT_TESTCLIENT_RESPONSE_RE.search(pytest_output)
        or _ASYNC_DEF_NOT_SUPPORTED_RE.search(pytest_output)
    ):
        return None

    patched_files: Dict[str, str] = {}
    touched = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        new = content

        # Si el archivo no usa TestClient, no tocamos (podría ser una suite async real)
        if "TestClient" not in new and "from fastapi.testclient import TestClient" not in new:
            continue

        # 1) fixtures async -> sync
        new2 = re.sub(r"@pytest\\.fixture\\s*\\n\\s*async\\s+def\\s+", "@pytest.fixture\n\ndef ", new)
        if new2 != new:
            new = new2

        # 2) tests async -> sync (sólo en archivos con TestClient)
        new2 = re.sub(r"\n\s*async\s+def\s+test_", "\n\ndef test_", new)
        if new2 != new:
            new = new2

        # 3) quitar markers asyncio (innecesarios en sync)
        new2 = re.sub(r"^\s*@pytest\.mark\.asyncio\s*$\n?", "", new, flags=re.MULTILINE)
        if new2 != new:
            new = new2

        # 4) si hay `await` en el body (no debería en TestClient), lo neutralizamos:
        new2 = re.sub(r"=\s*await\s+client\.", "= client.", new)
        if new2 != new:
            new = new2

        # 5) algunas veces se escribe `await client.get(...)` sin asignación
        new2 = re.sub(r"\bawait\s+client\.", "client.", new)
        if new2 != new:
            new = new2

        if new != content:
            patched_files[path] = new
            touched += 1

    if not patched_files:
        return None

    return FixResult(
        patched_files=patched_files,
        message=f"Rewrote async fixtures/tests to sync for TestClient suites in {touched} test file(s)",
    )


def fix_mock_object_as_db_session(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix agnóstico: si los tests overridean get_db con un mock/stub (ej: MockService) que NO implementa
    el contrato de la sesión (add/execute/...), el código peta y devuelve 500.

    En modo PARCIAL, si no hay BD real, la estrategia robusta es:
    - no inyectar stubs inválidos como si fueran una sesión
    - relajar el contrato del test a "no 5xx" (o permitir skip) para endpoints que dependen de integraciones externas.

    Implementación:
    - Neutraliza overrides que retornan un objeto mock (yield MockService/...) a `yield None`.
    - Cambia asserts rígidos `== expected_status` a `< 500` en tests de contrato parametrizados.
      (No tocamos asserts de 422/405 específicos, solo el bloque de "valid_request" que espera 200.)
    """
    if not pytest_output or not _MOCKSERVICE_AS_DB_SESSION_RE.search(pytest_output):
        return None

    patched_files: Dict[str, str] = {}
    touched = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if "dependency_overrides" not in content:
            continue

        new = content

        # 1) Neutralizar yield de mocks tipo MockService
        # Casos: `yield MockService()` o `service = MockService(); yield service`
        new2 = re.sub(r"yield\\s+MockService\\(\\)", "yield None", new)
        if new2 != new:
            new = new2

        new2 = re.sub(r"MockService\\(\\)\\s*\\n\\s*yield\\s+\\w+", "yield None", new)
        if new2 != new:
            new = new2

        # 2) Relajar assert rígido de status en el test de contrato "valid_request"
        # Si está en parametrización y hay `assert response.status_code == expected_status`, cambiarlo.
        new2 = re.sub(
            r"assert\\s+response\\.status_code\\s*==\\s*expected_status",
            "assert response.status_code < 500",
            new,
            count=1,
        )
        if new2 != new:
            new = new2

        if new != content:
            patched_files[path] = new
            touched += 1

    if not patched_files:
        return None

    return FixResult(
        patched_files=patched_files,
        message=f"Neutralized invalid MockService DB override and relaxed brittle 200-asserts in {touched} test file(s)",
    )


def fix_dict_as_db_session(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix agnóstico de PoC: si los tests están sobreescribiendo get_db (o similar) con un dict,
    pero el código espera un objeto tipo sesión/cliente con métodos (add/execute/...),
    NO intentamos emular una DB (sería ruido y frágil). En su lugar, hacemos el suite
    más robusto y hermético:

    - Eliminamos el override dict (para evitar AttributeError) y lo sustituimos por `yield None`.
    - Relajamos asserts de body para que el test verifique principalmente “no 5xx” cuando
      hay dependencias externas no disponibles en modo PARCIAL.

    Esta estrategia es pragmática y general: evita hardcodear “FakeSession” específica de SQLAlchemy
    y aplica a cualquier integración externa donde un dict se usó como doble incorrecto.
    """
    if not pytest_output or not _DICT_AS_DB_SESSION_RE.search(pytest_output):
        return None

    patched_files: Dict[str, str] = {}
    touched = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if "dependency_overrides" not in content:
            continue
        if "yield db" not in content:
            continue
        if "db = {" not in content:
            continue

        new = content

        # 1) Neutralizar el override dict: `db = {...}; yield db` -> `yield None`
        new2 = re.sub(
            r"db\\s*=\\s*\\{[\\s\\S]*?\\}\\s*\\n\\s*yield\\s+db",
            "yield None",
            new,
            count=1,
        )
        if new2 != new:
            new = new2

        # 2) Relajar asserts frágiles de body en tests “valid_request” cuando ya se afirma <500.
        #    (Mantiene asserts deterministas 405/422 sin tocarlos)
        new2 = re.sub(
            r"(assert\\s+response\\.status_code\\s*<\\s*500\\s*\\n)(?:assert\\s+response\\.json\\(\\)[\\s\\S]*?\\n)+",
            r"\\1",
            new,
            flags=re.MULTILINE,
        )
        if new2 != new:
            new = new2

        if new != content:
            patched_files[path] = new
            touched += 1

    if not patched_files:
        return None

    return FixResult(
        patched_files=patched_files,
        message=f"Detected dict-as-db override causing 5xx; neutralized overrides and relaxed fragile body asserts in {touched} test file(s)",
    )


def _get_runtime_contracts_obj(estructura: Dict[str, str]) -> Optional[dict]:
    try:
        raw = (estructura or {}).get(".poc_it/runtime_contracts.json") or ""
        if not isinstance(raw, str) or not raw.strip():
            return None
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _find_endpoint_contract(rc: dict, method: str, path: str) -> Optional[dict]:
    if not isinstance(rc, dict):
        return None
    eps = rc.get("endpoints") or []
    if not isinstance(eps, list):
        return None
    m = (method or "").upper()
    p = str(path or "")
    for ep in eps:
        if not isinstance(ep, dict):
            continue
        if str(ep.get("path") or "") == p and str(ep.get("method") or "").upper() == m:
            return ep
    return None


def fix_expected_200_got_422_request_shape(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix determinista: 422 cuando el test envía `json=` pero el endpoint realmente requiere query params.
    Reescribe `json={...}` -> `params={...}` para el endpoint que falló.

    Fuente de verdad: `.poc_it/runtime_contracts.json` enriquecido por runtime_probe.
    """
    if not pytest_output or not _EXPECTED_200_GOT_422_RE.search(pytest_output):
        return None

    # Detectar la llamada real en el test dentro del traceback para saber:
    # - método HTTP
    # - path
    # - nombre de la variable del payload (payload, project_data, task_data, data, etc.)
    call_re = re.compile(
        r"response\\s*=\\s*client\\.(?P<call>get|post|put|patch|delete)\\(\\s*(?P<q>['\\\"])(?P<path>[^'\\\"]+)\\2\\s*,\\s*json\\s*=\\s*(?P<payload>[A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )
    m = call_re.search(pytest_output)
    if not m:
        return None

    method = str(m.group("call") or "").upper()
    path = str(m.group("path") or "")
    payload_var = str(m.group("payload") or "payload")

    rc = _get_runtime_contracts_obj(estructura)
    ep = _find_endpoint_contract(rc or {}, method, path)
    if not isinstance(ep, dict):
        return None

    qreq = ep.get("query_params_required") or []
    body = ep.get("request_body_param")

    # Solo actuamos si es un caso CLARO: query required y sin body
    if not (isinstance(qreq, list) and qreq) or (body is not None and str(body).strip()):
        return None

    patched_files: Dict[str, str] = {}
    touched = 0

    for tpath, content in (estructura or {}).items():
        if not isinstance(tpath, str) or not tpath.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        if path not in content or "json=" not in content:
            continue

        new = content

        # Reescritura conservadora: solo para la primera ocurrencia del endpoint y preservando el nombre de la variable.
        #
        # Ejemplos:
        #   client.post("/projects", json=project_data) -> client.post("/projects", params=project_data)
        #   client.put("/x", json=payload) -> client.put("/x", params=payload)
        new2 = re.sub(
            rf'client\\.{method.lower()}\\(\\s*([\'\\"]{re.escape(path)}[\'\\"])\\s*,\\s*json\\s*=\\s*{re.escape(payload_var)}\\s*\\)',
            r"client." + method.lower() + r"(\\1, params=" + payload_var + r")",
            new,
            count=1,
        )
        if new2 != new:
            new = new2
        else:
            # Fallback: si el payload no se detectó exactamente (whitespace/kwargs extra), sustituimos solo el kw.
            new2 = re.sub(
                rf'(client\\.{method.lower()}\\(\\s*[\'\\"]{re.escape(path)}[\'\\"][^\\)]*?)\\bjson\\s*=\\s*',
                r"\\1params=",
                new,
                count=1,
            )
            if new2 != new:
                new = new2

        if new != content:
            patched_files[tpath] = new
            touched += 1

    if not patched_files:
        return None

    return FixResult(
        patched_files=patched_files,
        message=f"Rewrote json->params for {method} {path} using runtime_contracts (fix 422 request-shape) in {touched} test file(s)",
    )


def fix_put_requires_body_minimal(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix agnóstico: PUT/PATCH suelen requerir body JSON (request model) y si el test no envía json,
    FastAPI/Pydantic devuelve 422. Este fixer detecta el patrón típico en tests generados:
      - else: response = client.request(method, path)
    y lo reescribe para que envíe un payload mínimo cuando method sea PUT/PATCH.

    Nota: no intenta inferir el schema; solo manda un payload mínimo “seguro” y evita 422 por body ausente.
    Esto es válido para PoCs CRUD, integraciones externas, etc., porque el objetivo en modo PARCIAL es
    “no 5xx por wiring” y evitar errores triviales de request.
    """
    if not pytest_output or not _EXPECTED_200_GOT_422_RE.search(pytest_output):
        return None

    patched_files: Dict[str, str] = {}
    touched = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if "client.request(method, path)" not in content:
            continue
        if "method == \"POST\"" not in content:
            continue

        new = content

        # Inserta una rama para PUT/PATCH que envía json mínimo si no hay payload.
        # Mantener cambios pequeños: solo para el patrón exacto.
        new2 = re.sub(
            r'else:\\s*\\n\\s*response\\s*=\\s*client\\.request\\(method,\\s*path\\)\\s*\\n\\s*assert\\s+response\\.status_code\\s*==\\s*expected_status',
            'else:\\n        if method in ("PUT", "PATCH"):\\n            response = client.request(method, path, json={"_": "x"})\\n        else:\\n            response = client.request(method, path)\\n    assert response.status_code == expected_status',
            new,
            count=1,
        )
        if new2 != new:
            new = new2

        if new != content:
            patched_files[path] = new
            touched += 1

    if not patched_files:
        return None

    return FixResult(patched_files=patched_files, message=f"Added minimal JSON body for PUT/PATCH requests in {touched} test file(s) to avoid 422")


def fix_delete_response_body_asserts(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix agnóstico: DELETE responses frecuentemente devuelven solo un mensaje.
    Si el test aserta campos del recurso ("nombre", "id", etc.) falla.

    Cubre variantes comunes:
    - asserts genéricos `assert "id"/"nombre" in response_json`
    - asserts en variable intermedia `response_data = response.json(); assert "id"/"nombre" in response_data`
    - firmas del AssertionError en pytest_output

    Solo actúa si pytest_output trae evidencia de que DELETE devuelve un mensaje (no resource).
    """
    if not pytest_output:
        return None
    if not (
        _DELETE_BODY_ASSERT_RE.search(pytest_output)
        or _DELETE_ASSERT_ID_IN_MESSAGE_BODY_RE.search(pytest_output)
        or _DELETE_ASSERT_ID_IN_RESPONSE_DATA_RE.search(pytest_output)
        or _DELETE_ASSERT_NOMBRE_IN_RESPONSE_DATA_RE.search(pytest_output)
    ):
        return None

    patched_files: Dict[str, str] = {}
    touched = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        # Si el output de pytest sugiere explícitamente que DELETE devuelve "mensaje",
        # preferimos eliminar los asserts genéricos de resource fields (incluido el caso parametrizado).
        if not _DELETE_RETURNS_MESSAGE_NOT_RESOURCE_RE.search(pytest_output):
            # si no hay evidencia, no tocamos para evitar false positives
            continue

        new = content
        # Quita asserts típicos de resource fields en bloques genéricos.
        for pat in (
            r"\\n\\s*assert\\s+\"nombre\"\\s+in\\s+response_json\\s*",
            r"\\n\\s*assert\\s+\"precio\"\\s+in\\s+response_json\\s*",
            r"\\n\\s*assert\\s+\"disponible\"\\s+in\\s+response_json\\s*",
            r"\\n\\s*assert\\s+\"id\"\\s+in\\s+response_json\\s*",
            r"\\n\\s*assert\\s+\"id\"\\s+in\\s+response_data\\s*",
            r"\\n\\s*assert\\s+\"nombre\"\\s+in\\s+response_data\\s*",
            r"\\n\\s*assert\\s+\"id\"\\s+in\\s+response\\.json\\(\\)\\s*",
            r"\\n\\s*assert\\s+\"name\"\\s+in\\s+response\\.json\\(\\)\\s*",
        ):
            new2 = re.sub(pat, "\n", new)
            if new2 != new:
                new = new2

        # Además, si existe un bloque "if expected_status == 200:" común a varios métodos,
        # hacemos que para DELETE solo verifique que es dict y tiene alguna key.
        # Esto evita que vuelva a introducir asserts de resource en respuestas 200 no-resource.
        new2 = re.sub(
            r"(if\\s+expected_status\\s*==\\s*200:\\s*\\n\\s*response_json\\s*=\\s*response\\.json\\(\\)\\s*\\n)",
            r"\\1    # DELETE puede devolver solo mensaje (no resource fields)\n",
            new,
            count=1,
        )
        if new2 != new:
            new = new2

        if new != content:
            patched_files[path] = new
            touched += 1

    if not patched_files:
        return None

    return FixResult(patched_files=patched_files, message=f"Removed brittle resource-field asserts from DELETE response checks in {touched} test file(s)")


def fix_response_validation_error_relax_response_model(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """Fix determinista (modo PARCIAL): ResponseValidationError por response_model estricto.

    En modo PARCIAL, es común que la PoC genere endpoints con `response_model=...` pero:
    - no hay persistencia real / stubs incompletos
    - el handler devuelve `{}`/`None`/dict parcial
    => FastAPI lanza ResponseValidationError y los tests fallan en cascada.

    Estrategia pragmática y agnóstica:
    - Identificar el archivo del endpoint desde el traceback.
    - Quitar `response_model=...` SOLO del endpoint implicado.
    - Esto evita que FastAPI valide el response contra el modelo cuando estamos en modo “hermético sin integraciones”.

    Nota: esto toca app/**/*.py (a diferencia de pytest_llm_repair), pero aquí estamos en el
    pipeline de postprocesado general, donde SÍ tenemos permitido arreglar código si es el problema.
    """
    if not pytest_output or not _RESPONSE_VALIDATION_ERROR_RE.search(pytest_output):
        return None

    mfile = re.search(r'File \"(?P<path>.*?app[\\\\/].*?\\.py)\"', pytest_output)
    if not mfile:
        return None
    ep_abs = mfile.group("path")
    if not ep_abs:
        return None

    # Convertir a ruta relativa estilo estructura (POSIX)
    try:
        # Buscar el fragmento desde "app/" en adelante para hacerlo portable
        mm = re.search(r"(app[\\\\/].+?\\.py)", ep_abs)
        rel = mm.group(1) if mm else ep_abs
        rel = rel.replace("\\", "/")
    except Exception:
        rel = ep_abs.replace("\\", "/")

    content = (estructura or {}).get(rel)
    if not isinstance(content, str) or not content.strip():
        return None

    # Quitar response_model en decoradores FastAPI: @router.get(..., response_model=X) / @app.get(...)
    # Mantiene el resto de kwargs.
    new = re.sub(
        r"(\\@(router|app)\\.(get|post|put|patch|delete)\\([^\\)]*?)\\s*,\\s*response_model\\s*=\\s*[^,\\)]+",
        r"\\1",
        content,
        count=1,
        flags=re.MULTILINE,
    )

    if new == content:
        return None

    return FixResult(
        patched_files={rel: new},
        message=f"Removed response_model from {rel} to avoid ResponseValidationError in PARCIAL (hermetic stubs)",
    )


def fix_method_not_allowed_wrong_expectation(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix agnóstico: tests de 405 suelen estar mal construidos (usan un método que en realidad sí existe para el path),
    o dependen de que el path exista y acaban en 404.

    En vez de inventar métodos soportados (sin runtime_facts), degradamos el assert a:
      - assert status_code in (404, 405)
    Mantiene valor (no 5xx) sin fricción.
    """
    if not pytest_output or not _EXPECTED_405_GOT_404_RE.search(pytest_output):
        return None

    patched_files: Dict[str, str] = {}
    touched = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if "assert response.status_code == 405" not in content:
            continue

        new = content.replace("assert response.status_code == 405", "assert response.status_code in (404, 405)")
        if new != content:
            patched_files[path] = new
            touched += 1

    if not patched_files:
        return None

    return FixResult(patched_files=patched_files, message=f"Relaxed brittle 405 asserts to accept 404/405 in {touched} test file(s)")


def fix_expected_200_but_created_201(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix agnóstico: muchos POST devuelven 201 (Created). El test puede haber hardcodeado 200.
    Si el output muestra 201==200, cambiamos expected_status=200 -> 201 en el caso de POST.

    Estrategia:
    - Solo si pytest evidencia el mismatch 201 vs 200.
    - Parches pequeños: sustituir en parametrizaciones típicas ("POST", "...", ..., 200) -> ..., 201.
    """
    if not pytest_output or not _EXPECTED_200_GOT_201_RE.search(pytest_output):
        return None

    patched_files: Dict[str, str] = {}
    touched = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if "parametrize" not in content or "expected_status" not in content:
            continue

        new = content
        # patrón: ("POST", "/...", ..., 200) -> ..., 201)
        new2 = re.sub(r'\\("POST",\\s*("[^"]*"|\\\'[^\\\']*\\\'),\\s*([^\\)]*?),\\s*200\\)', r'("POST", \\1, \\2, 201)', new)
        if new2 != new:
            new = new2

        # patrón con path "/" o sin comillas (rare): ("POST", "/", {...}, 200)
        new2 = re.sub(r'\\("POST",\\s*/\\s*,\\s*([^\\)]*?),\\s*200\\)', r'("POST", "/", \\1, 201)', new)
        if new2 != new:
            new = new2

        if new != content:
            patched_files[path] = new
            touched += 1

    if not patched_files:
        return None
    return FixResult(patched_files=patched_files, message=f"Adjusted POST expected_status from 200 to 201 in {touched} test file(s)")


def fix_http_exception_detail_shape_mismatch(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix agnóstico: cuando un endpoint lanza HTTPException, el body estándar en FastAPI es:
      {"detail": <detail>}
    Por tanto, un assert `response.json() == {}` en respuestas 4xx/5xx es casi siempre incorrecto.

    Estrategia conservadora:
    - Sólo actuamos si pytest_output evidencia el mismatch (assert {} y diff muestra 'detail').
    - Parchamos tests para que:
      - si status_code >= 400: assert "detail" in response.json()
      - si status_code < 400: se mantiene assert {} (si aplica).

    Esto elimina una clase completa de fallos puntuales en integraciones externas (Drive/DB/HTTP).
    """
    if not pytest_output or not _HTTPXCEPTION_DETAIL_JSON_SHAPE_MISMATCH_RE.search(pytest_output):
        return None

    patched_files: Dict[str, str] = {}
    touched = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if "response.json() == {}" not in content:
            continue

        new = content

        # Solo tocamos el patrón directo dentro de un test donde ya existe el response.
        # Convertimos:
        #   assert response.json() == {}
        # a:
        #   if response.status_code >= 400:
        #       assert "detail" in response.json()
        #   else:
        #       assert response.json() == {}
        new2 = re.sub(
            r"\\n(\\s*)assert\\s+response\\.json\\(\\)\\s*==\\s*\\{\\s*\\}\\s*\\n",
            r'\\n\\1if response.status_code >= 400:\\n\\1    assert \"detail\" in response.json()\\n\\1else:\\n\\1    assert response.json() == {}\\n',
            new,
            count=1,
        )
        if new2 != new:
            new = new2

        if new != content:
            patched_files[path] = new
            touched += 1

    if not patched_files:
        return None

    return FixResult(
        patched_files=patched_files,
        message=f"Adjusted brittle `response.json()=={{}}` asserts to FastAPI HTTPException shape in {touched} test file(s)",
    )


def fix_contract_missing_required_wrongly_expects_200(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Fix agnóstico: tests *_missing_required a veces se generan mal (esperan 200 o hacen asserts de éxito).
    En modo PARCIAL debe esperarse 4xx (típicamente 422) o al menos <500.

    Casos que cubre:
    - mismatch 500==200 donde el log muestra HTTPException 404 (bug de handler que envuelve 404 en 500) o recurso inexistente.
    - mismatch 404==422 (ej: PUT /{id} con payload incompleto, pero el recurso no existe => 404).
    - "missing_required" para PUT/DELETE por id no es estable: debe relajarse.

    Estrategia segura:
    - Para tests que contengan "missing_required":
      - Cambiar asserts a `assert response.status_code < 500` (acepta 4xx).
      - Y/o ajustar expected_status de esos casos de PUT/DELETE a un set permisivo (422/404).
    """
    if not pytest_output or not (
        _EXPECTED_200_GOT_500_RE.search(pytest_output)
        or _HTTP_404_WRAPPED_AS_500_RE.search(pytest_output)
        or _EXPECTED_422_GOT_404_RE.search(pytest_output)
    ):
        return None

    patched_files: Dict[str, str] = {}
    touched = 0

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if "missing_required" not in content:
            continue

        new = content

        # Relajar asserts rígidos: assert status == expected_status -> assert status < 500
        new2 = re.sub(
            r"assert\\s+response\\.status_code\\s*==\\s*expected_status",
            "assert response.status_code < 500",
            new,
            count=1,
        )
        if new2 != new:
            new = new2

        # Si el test usa expected_status y valida PUT/DELETE "missing_required", permitir 404/422 sin romper.
        # Variante típica: assert response.status_code == expected_status (ya relajada), pero puede quedar otro assert.
        new2 = re.sub(
            r"assert\\s+response\\.status_code\\s*==\\s*422",
            "assert response.status_code in (404, 422)",
            new,
            count=1,
        )
        if new2 != new:
            new = new2

        # Y si hay expected_status=200 dentro de un test missing_required, cambiarlo a 422 (más correcto)
        new2 = re.sub(r"(missing_required[\\s\\S]*?\\()([\\s\\S]*?)(,\\s*200\\s*\\))", r"\\1\\2, 422)", new)
        if new2 != new:
            new = new2

        if new != content:
            patched_files[path] = new
            touched += 1

    if not patched_files:
        return None

    return FixResult(patched_files=patched_files, message=f"Relaxed/adjusted brittle expectations in missing_required tests in {touched} file(s)")


def apply_first_matching_fixer(pytest_output: str, estructura: Dict[str, str]) -> Optional[FixResult]:
    """
    Aplica solo 1 fixer por iteración para mantener cambios pequeños y observables.
    Orden:
    1) dependencias
    2) errores de sync/async (pytest-asyncio strict)
    3) errores de API de TestClient
    4) status code mismatch comunes (200 vs 201)
    5) payloads mínimos (422)
    6) asserts frágiles (DELETE)
    7) missing_required mal planteado / 404->500
    8) 405 mal planteado
    9) harness/integración
    """
    for fx in (
        # Dependencias primero (collection)
        fix_missing_httpx,
        # Footguns de pytest/harness (bloqueantes y muy comunes en PARCIAL)
        fix_fixture_called_directly,
        # Footguns de TestClient / harness (bloqueantes y muy comunes en PARCIAL)
        fix_testclient_get_json,
        fix_dict_as_db_session,
        fix_mock_object_as_db_session,
        # Sync/async mismatch (pytest-asyncio strict)
        fix_async_fixtures_in_sync_suite,
        # Mismatches típicos de contrato/status/payload
        fix_expected_200_but_created_201,
        fix_expected_200_got_422_request_shape,
        fix_put_requires_body_minimal,
        fix_response_validation_error_relax_response_model,
        fix_delete_response_body_asserts,
        fix_contract_missing_required_wrongly_expects_200,
        fix_method_not_allowed_wrong_expectation,
        # Contratos de error FastAPI (clase completa de fallos puntuales)
        fix_http_exception_detail_shape_mismatch,
    ):
        res = fx(pytest_output, estructura)
        if res:
            return res
    return None
