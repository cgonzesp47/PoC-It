from __future__ import annotations

from poc_it.orquestacion.tests_harness import render_conftest_py


def test_render_conftest_py_uses_generic_dependency_doubles_for_db_like_dependency():
    runtime_contracts = {
        "tests_style": "sync",
        "allowed_dependency_overrides": ["app.dependencies.get_db"],
        "endpoints": [
            {
                "method": "POST",
                "path": "/products",
                "depends_imports": ["app.dependencies.get_db"],
                "observed_calls": [{"method_name": "add", "awaited": False, "arg_names": ["item"]}],
                "response_json_shape": "object",
                "response_json_required_keys": ["id", "name"],
            }
        ],
    }

    content = render_conftest_py(runtime_contracts)

    assert "GeneratedStrictDouble" in content
    assert "app.dependency_overrides[dep_callable] = _override_dep" in content
    assert "FakeAsyncSession" not in content
    assert "_override_get_db" not in content
    assert "crud_mod" not in content
    assert "SQLAlchemy" not in content
    assert "dep_callable = _import_callable(dep_fqn)" in content


def test_render_conftest_py_also_supports_non_db_dependencies_without_special_cases():
    runtime_contracts = {
        "tests_style": "sync",
        "allowed_dependency_overrides": ["app.dependencies.get_ai_client"],
        "endpoints": [
            {
                "method": "POST",
                "path": "/summarize",
                "depends_imports": ["app.dependencies.get_ai_client"],
                "observed_calls": [{"method_name": "summarize", "awaited": True, "arg_names": ["payload"]}],
                "response_json_shape": "object",
                "response_json_required_keys": ["summary"],
            }
        ],
    }

    content = render_conftest_py(runtime_contracts)

    assert "GeneratedStrictDouble" in content
    assert "app.dependency_overrides[dep_callable] = _override_dep" in content
    assert "get_db" not in content
    assert "response.status_code < 500" not in content
