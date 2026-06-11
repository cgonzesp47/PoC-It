from __future__ import annotations
"""
poc_it.orquestacion.contract_test_renderer

Renderizado determinista de tests a partir de `.poc_it/test_plan.json`.

Principios
----------
- Determinista: no usa LLM para escribir tests.
- Contract-first: los asserts derivan del plan (y por ende de runtime_contracts/OpenAPI).
- Hermético: nunca depende de DB/red/credenciales/ficheros reales.
- Anti-fragilidad:
  - prohibido `assert response.json() == {...}`
  - prohibido `assert status_code in (200,201,400,404,422)`
  - prohibido aceptar 5xx como correcto

Outputs
-------
- tests/test_smoke_import.py
- tests/test_openapi.py
- tests/test_endpoint_contracts.py
- tests/conftest.py solo si hay endpoints invocables (HERMETIC_ENDPOINT_CONTRACT o SEMANTIC_STATEFUL)
- pytest.ini restringido a estos tests (evita residuales)

Nota
----
El conftest se apoya en `poc_it.orquestacion.tests_harness.render_conftest_py`, pero SOLO se genera
si hay endpoints invocables; si todo es OPENAPI_CONTRACT, no hace falta.

Este módulo devuelve un dict path->content listo para materializar.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from poc_it.orquestacion.test_plan import TEST_PLAN_PATH
from poc_it.orquestacion.tests_harness import render_conftest_py


def _safe_json_loads(raw: str) -> Optional[dict]:
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _py_literal(obj: Any) -> str:
    # Para dicts simples; repr es suficiente para tests.
    return repr(obj)


def _endpoint_id(method: str, path: str) -> str:
    base = (method.upper() + "_" + path).strip()
    base = base.replace("/", "_").replace("{", "").replace("}", "")
    base = "".join(ch for ch in base if ch.isalnum() or ch == "_")
    while "__" in base:
        base = base.replace("__", "_")
    return base.strip("_").lower() or "endpoint"


def _render_pytest_ini() -> str:
    return (
        "[pytest]\n"
        "addopts = -q\n"
        "testpaths = tests\n"
        "python_files = test_smoke_import.py test_openapi.py test_endpoint_contracts.py\n"
        "markers =\n"
        "    smoke\n"
        "    openapi\n"
        "    contract\n"
    )


def _render_test_smoke_import() -> str:
    return (
        "def test_import_app_main():\n"
        "    import importlib\n"
        "    mod = importlib.import_module('app.main')\n"
        "    assert mod is not None\n"
    )


def _render_test_openapi(plan: dict) -> str:
    eps = plan.get("endpoints") or []
    openapi_checks = []
    for ep in eps:
        if not isinstance(ep, dict):
            continue
        path = str(ep.get("path") or "")
        method = str(ep.get("method") or "").lower()
        if not path or not method or path == "*" or method == "*":
            continue
        openapi_checks.append((method, path))

    checks_lines = ""
    if openapi_checks:
        checks_lines = "    checks = " + _py_literal(openapi_checks) + "\n"
        checks_lines += (
            "    for meth, path in checks:\n"
            "        assert path in data.get('paths', {}), f'missing path in OpenAPI: {path}'\n"
            "        assert meth in (data.get('paths', {}).get(path) or {}), f'missing method in OpenAPI: {meth.upper()} {path}'\n"
        )
    else:
        checks_lines = "    assert 'paths' in data\n"

    return (
        "import pytest\n"
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n\n\n"
        "@pytest.mark.openapi\n"
        "def test_openapi_json_available_and_contains_planned_endpoints():\n"
        "    with TestClient(app) as client:\n"
        "        resp = client.get('/openapi.json')\n"
        "    assert resp.status_code < 500\n"
        "    data = resp.json()\n"
        + checks_lines
    )


def _materialize_path(path: str) -> str:
    # Convierte rutas tipo "/items/{id}" a "/items/1" para invocación real.
    # Heurística pragmática: cualquier "{...id...}" -> 1, el resto -> "test".
    import re

    def repl(m: re.Match) -> str:
        name = (m.group(1) or "").lower()
        if "id" in name:
            return "1"
        return "test"

    return re.sub(r"\{([^}]+)\}", repl, path or "")


def _render_test_endpoint_contracts(plan: dict) -> str:
    eps = plan.get("endpoints") or []

    # Generar tests por endpoint invocable (hermetic/stateful). Los OPENAPI_CONTRACT se validan en test_openapi.
    #
    # Regla hermética:
    # - Los tests NO deben instanciar TestClient(app) ni importar app directamente.
    # - Deben usar el fixture `client` definido en tests/conftest.py, que aplica dependency_overrides.
    blocks: List[str] = []

    for ep in eps:
        if not isinstance(ep, dict):
            continue
        level = str(ep.get("level") or "")
        if level not in ("HERMETIC_ENDPOINT_CONTRACT", "SEMANTIC_STATEFUL"):
            continue

        method = str(ep.get("method") or "").upper()
        path = str(ep.get("path") or "")
        expected_status = ep.get("expected_status")
        allowed_statuses = ep.get("allowed_statuses") or []
        sample_request = ep.get("sample_request")
        required_keys = ep.get("required_response_keys") or []
        resp_mt = ep.get("response_media_type")

        tid = _endpoint_id(method, path)

        call_lines = ""
        call_path = _materialize_path(path)

        if method in ("POST", "PUT", "PATCH"):
            call_lines = (
                f"    resp = client.request('{method}', '{call_path}', json={_py_literal(sample_request or {})})\n"
            )
        else:
            call_lines = f"    resp = client.request('{method}', '{call_path}')\n"

        # 1) Nunca aceptar 5xx
        assert_lines = "    assert resp.status_code < 500\n"

        # 2) Contract status:
        # - si allowed_statuses viene del plan, usarlo (y nunca inventarlo)
        # - si no, usar expected_status estricto si existe
        if isinstance(allowed_statuses, list) and allowed_statuses:
            allowed: List[int] = []
            seen = set()
            for x in allowed_statuses:
                if isinstance(x, int) and x < 500 and x not in seen:
                    seen.add(x)
                    allowed.append(x)
            allowed = sorted(allowed)
            if allowed:
                assert_lines += f"    assert resp.status_code in {allowed!r}\n"
        elif isinstance(expected_status, int):
            assert_lines += f"    assert resp.status_code == {expected_status}\n"

        # 3) required_response_keys:
        # solo si 2xx y no 204 y JSON (o si resp_mt sugiere json).
        # No validar keys en 4xx (p.ej. 404/422) para no convertirlo en semántico.
        keys = [str(k) for k in required_keys if str(k).strip()]
        if keys:
            assert_lines += "    if 200 <= resp.status_code < 300 and resp.status_code != 204:\n"
            assert_lines += "        data = resp.json()\n"
            assert_lines += "        assert isinstance(data, dict)\n"
            assert_lines += f"        for k in {keys!r}:\n"
            assert_lines += "            assert k in data\n"
        else:
            if isinstance(resp_mt, str) and "json" in resp_mt:
                assert_lines += "    if 200 <= resp.status_code < 300 and resp.status_code != 204:\n"
                assert_lines += "        _ = resp.json()\n"

        reason = str(ep.get("reason") or "").replace('"""', '\\"\\"\\"')
        blocks.append(
            (
                f"@pytest.mark.contract\n"
                f"def test_contract_{tid}(client):\n"
                f"    \"\"\"{reason}\"\"\"\n"
                + call_lines
                + assert_lines
            )
        )

    if not blocks:
        return (
            "import pytest\n\n\n"
            "@pytest.mark.contract\n"
            "def test_no_invocable_endpoints_in_plan():\n"
            "    # Plan degradado a OPENAPI_CONTRACT/SMOKE_ONLY: se valida en test_openapi.\n"
            "    assert True\n"
        )

    header = (
        "import pytest\n\n\n"
        "# Nota: este archivo DEBE usar el fixture `client` de tests/conftest.py.\n"
        "# Prohibido: instanciar el cliente FastAPI manualmente en este archivo.\n\n\n"
    )
    return header + "\n\n\n".join(blocks).rstrip() + "\n"


def render_tests_from_test_plan(
    *,
    structure: Dict[str, str],
    runtime_contracts: Optional[dict],
    runtime_facts: Optional[dict],
) -> Dict[str, str]:
    raw = structure.get(TEST_PLAN_PATH) or ""
    plan = _safe_json_loads(raw) if isinstance(raw, str) and raw.strip() else None
    if not isinstance(plan, dict):
        # Fallback: smoke+openapi
        plan = {"mode": "UNKNOWN", "strategy": "contract-first", "endpoints": []}

    eps = plan.get("endpoints") or []
    has_invocable = any(
        isinstance(ep, dict) and str(ep.get("level") or "") in ("HERMETIC_ENDPOINT_CONTRACT", "SEMANTIC_STATEFUL")
        for ep in eps
    )

    patch: Dict[str, str] = {
        "pytest.ini": _render_pytest_ini(),
        "tests/__init__.py": "",
        "tests/test_smoke_import.py": _render_test_smoke_import(),
        "tests/test_openapi.py": _render_test_openapi(plan),
        "tests/test_endpoint_contracts.py": _render_test_endpoint_contracts(plan),
    }

    if has_invocable:
        # conftest determinista: fuente de verdad de overrides permitidos
        try:
            try:
                conftest = render_conftest_py(runtime_contracts=runtime_contracts, runtime_facts=runtime_facts)
            except TypeError:
                conftest = render_conftest_py(runtime_contracts=runtime_contracts)
            patch["tests/conftest.py"] = conftest
        except Exception:
            # Si el conftest falla, dejamos que la suite corra sin overrides (los endpoints deberían degradarse en el plan).
            pass

    # ----------------------------
    # Guardrails post-render (hermetic)
    # ----------------------------
    try:
        import ast

        ep_tests = patch.get("tests/test_endpoint_contracts.py") or ""

        # Guardrail 1: el archivo no puede importar la app ni instanciar TestClient(...).
        tree = ast.parse(ep_tests)

        for node in ast.walk(tree):
            # `from app.main import app`
            if isinstance(node, ast.ImportFrom) and (node.module or "") == "app.main":
                for n in node.names:
                    if (n.name or "") == "app":
                        raise ValueError(
                            "Guardrail: test_endpoint_contracts.py no puede importar app (usar fixture client)"
                        )

            # `TestClient(...)`
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id == "TestClient":
                    raise ValueError(
                        "Guardrail: test_endpoint_contracts.py no puede instanciar TestClient (usar fixture client)"
                    )

        if has_invocable:
            # Guardrail 2: cada test contractual debe recibir el fixture `client`
            if "def test_contract_" in ep_tests and "(client" not in ep_tests:
                raise ValueError("Guardrail: tests contractuales deben recibir parámetro client (fixture)")

            # Guardrail 3: número de tests contractuales debe igualar endpoints invocables en el plan
            expected = sum(
                1
                for ep in eps
                if isinstance(ep, dict)
                and str(ep.get("level") or "") in ("HERMETIC_ENDPOINT_CONTRACT", "SEMANTIC_STATEFUL")
            )
            actual = ep_tests.count("def test_contract_")
            if actual != expected:
                raise ValueError(f"Guardrail: mismatch tests contractuales: expected={expected} actual={actual}")
    except Exception:
        # Si falla un guardrail, degradamos a contract-lite (smoke+openapi) para no ejecutar endpoints sin overrides.
        patch.pop("tests/test_endpoint_contracts.py", None)
        patch.pop("tests/conftest.py", None)
        patch["pytest.ini"] = (
            "[pytest]\n"
            "addopts = -q\n"
            "testpaths = tests\n"
            "python_files = test_smoke_import.py test_openapi.py\n"
            "markers =\n"
            "    smoke\n"
            "    openapi\n"
        )

    return patch
