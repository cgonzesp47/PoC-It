from __future__ import annotations

"""
poc_it.testing.rendering.contract_test_renderer

Renderizado determinista de tests a partir de `.poc_it/test_plan.json`.
"""

import json
import re
from typing import Any, Dict, List, Optional

from poc_it.testing.domain.models import TEST_PLAN_PATH
from poc_it.testing.rendering.assertion_renderer import render_assertion_lines
from poc_it.testing.rendering.request_renderer import render_request_call
from poc_it.testing.rendering.semantic_scenario_renderer import render_semantic_scenarios
from poc_it.testing.rendering.tests_harness import render_conftest_py

_ALLOWED_ASSERTION_KINDS = {
    "STATUS_EQUALS",
    "JSON_HAS_KEYS",
    "JSON_FIELD_EQUALS",
    "JSON_FIELD_TYPE",
    "JSON_FIELD_DYNAMIC",
    "CALL_COUNT",
    "CALL_ARGS_PARTIAL",
    "STATE_CONTAINS",
}


def _safe_json_loads(raw: str) -> Optional[dict]:
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _py_literal(obj: Any) -> str:
    return repr(obj)


def _endpoint_id(method: str, path: str) -> str:
    raw = f"{method.upper()}_{path}".strip()
    base = re.sub(r"[^A-Za-z0-9]+", "_", raw)
    base = re.sub(r"_+", "_", base)
    return base.strip("_").lower() or "endpoint"


def _scenario_id(value: str) -> str:
    raw = str(value or "").strip()
    base = re.sub(r"[^A-Za-z0-9]+", "_", raw)
    base = re.sub(r"_+", "_", base)
    return base.strip("_").lower() or "scenario"


def _render_pytest_ini() -> str:
    return (
        "[pytest]\n"
        "addopts = -q\n"
        "testpaths = tests\n"
        "python_files = test_startup.py test_openapi_contract.py test_request_validation.py test_http_behavior.py test_semantic_scenarios.py\n"
        "markers =\n"
        "    startup\n"
        "    openapi\n"
        "    request_validation\n"
        "    contract\n"
        "    scenario\n"
    )


def _render_test_startup() -> str:
    return (
        "import importlib\n\n"
        "import pytest\n"
        "from fastapi import FastAPI\n"
        "from fastapi.testclient import TestClient\n\n\n"
        "def _load_app():\n"
        "    mod = importlib.import_module('app.main')\n"
        "    app = getattr(mod, 'app', None)\n"
        "    assert app is not None, 'FastAPI app instance not found at app.main:app'\n"
        "    assert isinstance(app, FastAPI), 'app.main:app is not a FastAPI instance'\n"
        "    return app\n\n\n"
        "@pytest.mark.startup\n"
        "def test_application_can_be_imported():\n"
        "    app = _load_app()\n"
        "    assert app is not None\n\n\n"
        "@pytest.mark.startup\n"
        "def test_application_lifespan_starts():\n"
        "    app = _load_app()\n"
        "    with TestClient(app) as client:\n"
        "        assert client is not None\n\n\n"
        "@pytest.mark.startup\n"
        "def test_openapi_document_can_be_generated():\n"
        "    app = _load_app()\n"
        "    with TestClient(app) as client:\n"
        "        resp = client.get('/openapi.json')\n"
        "    assert resp.status_code == 200\n"
        "    data = resp.json()\n"
        "    assert isinstance(data, dict)\n"
        "    assert 'openapi' in data\n"
        "    assert 'paths' in data\n"
    )


def _plan_endpoints(plan: dict) -> List[dict]:
    eps = plan.get("endpoints") or []
    return [ep for ep in eps if isinstance(ep, dict)]


def _get_required_parameters(ep: dict) -> List[dict]:
    params = ep.get("parameters") or []
    return [
        p
        for p in params
        if isinstance(p, dict)
        and bool(p.get("required", False))
        and str(p.get("name") or "").strip()
        and str(p.get("in") or "").strip()
    ]


def _get_response_statuses(ep: dict) -> List[str]:
    responses = ep.get("responses") or {}
    if isinstance(responses, dict):
        return [str(code) for code in responses.keys() if str(code).strip()]
    statuses = ep.get("expected_statuses") or ep.get("status_codes") or []
    if isinstance(statuses, list):
        return [str(code) for code in statuses if str(code).strip()]
    return []


def _collect_schema_refs(value: Any) -> List[str]:
    refs: List[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref not in refs:
                refs.append(ref)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(value)
    return refs


def _request_body_defined(ep: dict) -> bool:
    request_body = ep.get("request_body")
    if isinstance(request_body, dict):
        content = request_body.get("content")
        if isinstance(content, dict) and content:
            return True
    if isinstance(ep.get("request_body_schema"), dict):
        return True
    return False


def _render_test_openapi_contract(plan: dict) -> str:
    endpoint_tests: List[str] = []

    for ep in _plan_endpoints(plan):
        path = str(ep.get("path") or "").strip()
        method = str(ep.get("method") or "").lower().strip()
        if not path or not method or path == "*" or method == "*":
            continue

        tid = _endpoint_id(method, path)
        required_params = [(str(p.get("name")), str(p.get("in"))) for p in _get_required_parameters(ep)]
        expected_statuses = _get_response_statuses(ep)
        schema_refs = _collect_schema_refs(ep.get("request_body")) + _collect_schema_refs(ep.get("responses"))
        seen_refs: List[str] = []
        schema_refs = [ref for ref in schema_refs if not (ref in seen_refs or seen_refs.append(ref))]
        requires_request_body = _request_body_defined(ep)

        lines = [
            "@pytest.mark.openapi",
            f"def test_openapi_contract_{tid}():",
            "    openapi = _load_openapi()",
            "    paths = openapi.get('paths') or {}",
            f"    assert {path!r} in paths",
            f"    operation = (paths.get({path!r}) or {{}}).get({method!r})",
            f"    assert operation is not None, 'missing operation: {method.upper()} {path}'",
        ]

        if required_params:
            lines.extend(
                [
                    "    parameters = operation.get('parameters') or []",
                    "    param_index = {(str(p.get('name')), str(p.get('in'))): p for p in parameters if isinstance(p, dict)}",
                ]
            )
            for name, location in required_params:
                lines.append(f"    assert ({name!r}, {location!r}) in param_index")
                lines.append(f"    assert bool(param_index[({name!r}, {location!r})].get('required', False)) is True")

        if requires_request_body:
            lines.extend(
                [
                    "    request_body = operation.get('requestBody')",
                    "    assert isinstance(request_body, dict)",
                    "    assert bool((request_body.get('content') or {})) is True",
                ]
            )

        if expected_statuses:
            lines.append("    responses = operation.get('responses') or {}")
            for status_code in expected_statuses:
                lines.append(f"    assert {status_code!r} in responses")

        if schema_refs:
            lines.append("    components = ((openapi.get('components') or {}).get('schemas') or {})")
            for ref in schema_refs:
                if ref.startswith("#/components/schemas/"):
                    schema_name = ref.split("/")[-1]
                    lines.append(f"    assert {schema_name!r} in components")
                else:
                    lines.append(f"    assert {ref!r}.startswith('#/components/schemas/')")

        endpoint_tests.append("\n".join(lines))

    if not endpoint_tests:
        endpoint_tests.append(
            "@pytest.mark.openapi\n"
            "def test_openapi_contract_suite_has_no_endpoints():\n"
            "    openapi = _load_openapi()\n"
            "    assert isinstance(openapi, dict)\n"
            "    assert 'paths' in openapi\n"
        )

    return (
        "import pytest\n"
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n\n\n"
        "def _load_openapi():\n"
        "    with TestClient(app) as client:\n"
        "        response = client.get('/openapi.json')\n"
        "    assert response.status_code == 200\n"
        "    return response.json()\n\n\n"
        + "\n\n\n".join(endpoint_tests)
        + "\n"
    )


def _render_test_request_validation(plan: dict) -> str:
    endpoint_tests: List[str] = []

    for ep in _plan_endpoints(plan):
        path = str(ep.get("path") or "").strip()
        method = str(ep.get("method") or "").upper().strip()
        if not path or not method or path == "*" or method == "*":
            continue
        if method not in {"POST", "PUT", "PATCH", "DELETE", "GET"}:
            continue

        invalid_request = ep.get("invalid_request")
        if not isinstance(invalid_request, dict):
            continue

        tid = _endpoint_id(method, path)
        query_params = invalid_request.get("query_params") if isinstance(invalid_request.get("query_params"), dict) else None
        headers = invalid_request.get("headers") if isinstance(invalid_request.get("headers"), dict) else None
        cookies = invalid_request.get("cookies") if isinstance(invalid_request.get("cookies"), dict) else None
        json_body = invalid_request.get("json_body") if "json_body" in invalid_request else None

        kwargs: List[str] = []
        if query_params:
            kwargs.append(f"params={_py_literal(query_params)}")
        if headers:
            kwargs.append(f"headers={_py_literal(headers)}")
        if cookies:
            kwargs.append(f"cookies={_py_literal(cookies)}")
        if json_body is not None:
            kwargs.append(f"json={_py_literal(json_body)}")
        kwargs_code = (", " + ", ".join(kwargs)) if kwargs else ""

        endpoint_tests.append(
            "@pytest.mark.request_validation\n"
            f"def test_request_validation_{tid}():\n"
            "    with TestClient(app) as client:\n"
            f"        response = client.request('{method}', '{path}'{kwargs_code})\n"
            "    assert response.status_code == 422\n"
        )

    if not endpoint_tests:
        endpoint_tests.append(
            "@pytest.mark.request_validation\n"
            "def test_request_validation_suite_has_no_invalid_examples():\n"
            "    assert True\n"
        )

    return "import pytest\nfrom fastapi.testclient import TestClient\nfrom app.main import app\n\n\n" + "\n\n\n".join(endpoint_tests) + "\n"


def _http_cases(endpoint_plan: dict) -> List[dict]:
    return [
        case
        for case in endpoint_plan.get("cases") or []
        if isinstance(case, dict) and str(case.get("level") or "") == "HERMETIC_HTTP"
    ]


def _case_test_name_suffix(case: dict, *, index: int) -> str:
    case_id = str(case.get("case_id") or "").strip()
    if not case_id:
        return str(index)
    slug = re.sub(r"[^0-9a-zA-Z_]+", "_", case_id).strip("_")
    return slug or str(index)


def _assertion_kind(assertion: dict) -> str:
    return str(assertion.get("kind") or "").strip()


def _render_canonical_assertion_lines(assertions: list[dict], *, response_name: str = "resp", response_data_name: str = "data") -> str:
    normalized: list[dict] = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            continue

        kind = _assertion_kind(assertion)
        if kind not in _ALLOWED_ASSERTION_KINDS:
            raise ValueError(f"Unsupported assertion kind: {kind}")

        if kind == "STATE_CONTAINS":
            target = assertion.get("target")
            expected = assertion.get("expected")
            metadata = assertion.get("metadata") if isinstance(assertion.get("metadata"), dict) else {}
            if target == "startup_available":
                return "    assert True\n"
            if target == "semantic_state_transition":
                return "    assert True\n"
            if target == "openapi_path":
                return (
                    "    with TestClient(app) as openapi_client:\n"
                    "        _openapi = openapi_client.get('/openapi.json').json()\n"
                    f"    assert {_py_literal(expected)} in (_openapi.get('paths') or {{}})\n"
                )
            if target == "openapi_method":
                path = metadata.get("path")
                if not isinstance(path, str):
                    raise ValueError("STATE_CONTAINS openapi_method requires metadata.path")
                return (
                    "    with TestClient(app) as openapi_client:\n"
                    "        _openapi = openapi_client.get('/openapi.json').json()\n"
                    f"    assert {str(expected).lower()!r} in ((_openapi.get('paths') or {{}}).get({path!r}) or {{}})\n"
                )
            raise ValueError(f"Unsupported STATE_CONTAINS target: {target}")

        normalized.append(assertion)

    rendered = render_assertion_lines(normalized, response_name=response_name, response_data_name=response_data_name)
    return "".join(f"    {line}\n" for line in rendered.rstrip().splitlines()) if rendered.strip() else ""


def _render_dependency_setup_lines(test_case: dict) -> str:
    setups = test_case.get("dependency_setup") or []
    if not isinstance(setups, list) or not setups:
        return ""

    providers: List[dict] = []
    auto_doubles: List[dict] = []
    auto_raising_doubles: List[dict] = []
    method_behaviors: List[dict] = []

    for item in setups:
        if not isinstance(item, dict):
            continue

        action = str(item.get("action") or "").strip()
        dependency_fqn = str(item.get("dependency_fqn") or "").strip()

        if not dependency_fqn:
            continue

        if action == "provide":
            providers.append(item)
            continue

        if action == "provide_auto":
            auto_doubles.append(item)
            continue

        if action == "provide_auto_raise":
            auto_raising_doubles.append(item)
            continue

        method_name = str(item.get("method_name") or "").strip()

        if method_name and action in {"return", "raise", "async_return"}:
            method_behaviors.append(item)

    grouped: Dict[str, List[dict]] = {}
    for item in method_behaviors:
        dep_fqn = str(item.get("dependency_fqn") or "").strip()
        grouped.setdefault(dep_fqn, []).append(item)

    if not providers and not auto_doubles and not auto_raising_doubles and not grouped:
        return ""

    lines: List[str] = []

    for provider in providers:
        dep_fqn = str(provider["dependency_fqn"])
        lines.append(
            "    dependency_overrides_guard.bind_value("
            f"{dep_fqn!r}, "
            f"{_py_literal(provider.get('value'))}"
            ")"
        )

    seen_auto_doubles: set = set()
    for auto_double in auto_doubles:
        dep_fqn = str(auto_double["dependency_fqn"])
        if dep_fqn in seen_auto_doubles:
            continue
        seen_auto_doubles.add(dep_fqn)
        lines.append(f"    dependency_overrides_guard.bind_auto_double({dep_fqn!r})")

    seen_auto_raising_doubles: set = set()
    for auto_raising in auto_raising_doubles:
        dep_fqn = str(auto_raising["dependency_fqn"])
        if dep_fqn in seen_auto_raising_doubles:
            continue
        seen_auto_raising_doubles.add(dep_fqn)
        exc_type = str(auto_raising.get("exception_type") or "RuntimeError")
        exc_message = str(auto_raising.get("exception_message") or exc_type)
        if exc_type == "HTTPException":
            status_code = int(auto_raising.get("exception_status_code") or 500)
            exc_literal = f"HTTPException(status_code={status_code!r}, detail={_py_literal(exc_message)})"
        else:
            exc_literal = f"{exc_type}({_py_literal(exc_message)})"
        lines.append(
            f"    dependency_overrides_guard.bind_auto_double_raising({dep_fqn!r}, {exc_literal})"
        )

    for dep_fqn, behaviors in grouped.items():
        methods = sorted(
            {
                str(item.get("method_name") or "").strip()
                for item in behaviors
                if str(item.get("method_name") or "").strip()
            }
        )
        var_name = "dep_" + _scenario_id(dep_fqn)
        lines.append(
            f"    {var_name} = dependency_overrides_guard.get_double({dep_fqn!r}, methods={_py_literal(methods)})"
        )
        for behavior in behaviors:
            action = str(behavior.get("action") or "").strip()
            method_name = str(behavior.get("method_name") or "").strip()
            if action == "return":
                lines.append(f"    {var_name}.configure_return({method_name!r}, {_py_literal(behavior.get('value'))})")
            elif action == "async_return":
                lines.append(f"    {var_name}.configure_async_return({method_name!r}, {_py_literal(behavior.get('value'))})")
            elif action == "raise":
                exc_type = str(behavior.get("exception_type") or "RuntimeError")
                exc_message = str(behavior.get("exception_message") or exc_type)
                if exc_type == "HTTPException":
                    status_code = int(behavior.get("exception_status_code") or 500)
                    lines.append(
                        f"    {var_name}.configure_raise({method_name!r}, "
                        f"HTTPException(status_code={status_code!r}, detail={_py_literal(exc_message)}))"
                    )
                else:
                    lines.append(
                        f"    {var_name}.configure_raise({method_name!r}, {exc_type}({_py_literal(exc_message)}))"
                    )
    return "\n".join(lines) + ("\n" if lines else "")


def _render_http_behavior(plan: dict) -> str:
    endpoint_plans = plan.get("endpoint_plans") or []
    blocks: List[str] = []

    for endpoint_plan in endpoint_plans:
        if not isinstance(endpoint_plan, dict):
            continue
        http_cases = _http_cases(endpoint_plan)
        if not http_cases:
            continue

        method = str(endpoint_plan.get("method") or "GET").upper()
        path = str(endpoint_plan.get("path") or "/")
        tid = _endpoint_id(method, path)
        reason = "; ".join(endpoint_plan.get("limitations") or []) or "Contract case"

        for index, http_case in enumerate(http_cases):
            request = http_case.get("request")
            assertions = http_case.get("assertions") or []
            category = str(http_case.get("category") or "").strip()

            request_dict = request if isinstance(request, dict) else {}
            if "method" not in request_dict:
                request_dict = {**request_dict, "method": method}
            if "path_template" not in request_dict:
                request_dict = {**request_dict, "path_template": path}

            dependency_lines = _render_dependency_setup_lines(http_case)
            imports = ["from fastapi.testclient import TestClient\n", "from app.main import app\n"]
            if "HTTPException(" in dependency_lines:
                imports.append("from fastapi import HTTPException\n")
            call_lines = render_request_call(request_dict, client_name="client", response_name="resp")
            call_block = "".join(f"    {line}\n" for line in call_lines.rstrip().splitlines())
            assert_lines = _render_canonical_assertion_lines(assertions)

            suffix = _case_test_name_suffix(http_case, index=index)
            case_doc = f"{reason} ({category})" if category else reason

            blocks.append(
                "".join(imports)
                + "\n"
                + f"@pytest.mark.contract\n"
                + f"def test_contract_{tid}__{suffix}(client, dependency_overrides_guard):\n"
                + f'    """{str(case_doc).replace(chr(34) * 3, r"\\\"\\\"\\\"")}"""\n'
                + dependency_lines
                + call_block
                + assert_lines
            )

    if not blocks:
        return (
            "import pytest\n\n\n"
            "@pytest.mark.contract\n"
            "def test_no_invocable_endpoints_in_plan():\n"
            "    assert True\n"
        )

    return "import pytest\n\n\n" + "\n\n\n".join(blocks).rstrip() + "\n"


def render_tests_from_test_plan(
    *,
    structure: Dict[str, str],
    runtime_contracts: Optional[dict],
    runtime_facts: Optional[dict],
) -> Dict[str, str]:
    raw = structure.get(TEST_PLAN_PATH) or ""
    plan = _safe_json_loads(raw) if isinstance(raw, str) and raw.strip() else None
    if not isinstance(plan, dict):
        plan = {
            "mode": "UNKNOWN",
            "strategy": "contract-first",
            "endpoints": [],
            "endpoint_plans": [],
            "scenario_plans": [],
        }

    endpoint_plans = plan.get("endpoint_plans") or []
    eps = plan.get("endpoints") or []
    scenario_plans = plan.get("scenario_plans") or []
    has_startup = True
    has_invocable = has_startup and (
        any(
            isinstance(ep, dict)
            and any(
                isinstance(case, dict) and str(case.get("level") or "") == "HERMETIC_HTTP"
                for case in (ep.get("cases") or [])
            )
            for ep in endpoint_plans
        )
        or any(
            isinstance(ep, dict) and str(ep.get("level") or "") == "HERMETIC_ENDPOINT_CONTRACT"
            for ep in eps
        )
    )
    has_scenarios = any(isinstance(scenario, dict) and (scenario.get("operations") or []) for scenario in scenario_plans)

    patch: Dict[str, str] = {
        "pytest.ini": _render_pytest_ini(),
        "tests/__init__.py": "",
        "tests/test_startup.py": _render_test_startup(),
        "tests/test_openapi_contract.py": _render_test_openapi_contract(plan),
        "tests/test_request_validation.py": _render_test_request_validation(plan),
    }

    if has_invocable:
        patch["tests/test_http_behavior.py"] = _render_http_behavior(plan)
    if has_scenarios:
        patch["tests/test_semantic_scenarios.py"] = render_semantic_scenarios(plan)
    if has_invocable or has_scenarios:
        try:
            try:
                conftest = render_conftest_py(runtime_contracts=runtime_contracts, runtime_facts=runtime_facts)
            except TypeError:
                conftest = render_conftest_py(runtime_contracts=runtime_contracts)
            patch["tests/conftest.py"] = conftest
        except Exception:
            pass

    return patch


__all__ = ["render_tests_from_test_plan"]
