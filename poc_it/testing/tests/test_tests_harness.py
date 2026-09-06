from __future__ import annotations

from poc_it.testing.rendering.tests_harness import render_conftest_py


def test_render_conftest_is_self_contained():
    rendered = render_conftest_py(
        {
            "tests_style": "sync",
            "allowed_dependency_overrides": [],
            "endpoints": [],
        }
    )

    assert "from poc_it" not in rendered
    assert "import poc_it" not in rendered
    assert "class GeneratedStrictDouble:" in rendered


def test_render_conftest_exposes_dependency_guard():
    rendered = render_conftest_py(
        {
            "tests_style": "sync",
            "allowed_dependency_overrides": [],
            "endpoints": [],
        }
    )

    assert "@pytest.fixture\ndef dependency_overrides_guard(app, dependency_double_factory):" in rendered
    assert "def get_double(self, dep_fqn, *, methods=None):" in rendered
    assert "app.dependency_overrides[dep_callable] = _override_dep" in rendered


def test_render_conftest_supports_value_providers():
    rendered = render_conftest_py(
        {
            "tests_style": "sync",
            "allowed_dependency_overrides": [],
            "endpoints": [],
        }
    )

    assert "def bind_value(self, dep_fqn, value):" in rendered
    assert "def bind(self, dep_fqn, value):" in rendered
    assert "return self.bind_value(dep_fqn, value)" in rendered


def test_render_conftest_supports_auto_doubles():
    rendered = render_conftest_py(
        {
            "tests_style": "sync",
            "allowed_dependency_overrides": [],
            "endpoints": [],
        }
    )

    assert "from unittest.mock import MagicMock" in rendered
    assert "def bind_auto_double(self, dep_fqn):" in rendered
    assert "double = MagicMock(name=dep_fqn.rsplit('.', 1)[-1])" in rendered
    assert "app.dependency_overrides[dep_callable] = _override_auto" in rendered


def test_render_conftest_clears_dependency_overrides():
    rendered = render_conftest_py(
        {
            "tests_style": "sync",
            "allowed_dependency_overrides": [],
            "endpoints": [],
        }
    )

    assert "finally:" in rendered
    assert "app.dependency_overrides.clear()" in rendered


def test_render_conftest_supports_sync_client():
    rendered = render_conftest_py(
        {
            "tests_style": "sync",
            "allowed_dependency_overrides": [],
            "endpoints": [],
        }
    )

    assert "from fastapi.testclient import TestClient" in rendered
    assert "def client(app, monkeypatch):" in rendered
    assert "raise_server_exceptions=False" in rendered


def test_render_conftest_supports_async_client():
    rendered = render_conftest_py(
        {
            "tests_style": "async",
            "allowed_dependency_overrides": [],
            "endpoints": [],
        }
    )

    assert "import httpx" in rendered
    assert "async def client(app, monkeypatch):" in rendered
    assert "httpx.AsyncClient(app=app, base_url='http://test')" in rendered


def test_render_conftest_includes_explicit_environment_variables():
    rendered = render_conftest_py(
        {
            "env_vars_explicit": [
                "GOOGLE_APPLICATION_CREDENTIALS",
            ],
            "endpoints": [],
        }
    )

    assert "monkeypatch.setenv('GOOGLE_APPLICATION_CREDENTIALS'" in rendered


def test_generated_strict_double_is_strict_and_records_calls_in_rendered_output():
    rendered = render_conftest_py(
        {
            "tests_style": "sync",
            "allowed_dependency_overrides": [],
            "endpoints": [
                {
                    "depends_imports": ["app.main.get_repository"],
                    "observed_calls": [
                        {
                            "method_name": "save",
                            "awaited": False,
                            "arg_names": ["payload"],
                        }
                    ],
                }
            ],
        }
    )

    assert "raise AttributeError(f\"Method {method_name!r} not allowed in GeneratedStrictDouble\")" in rendered
    assert "self.calls.append({'method_name': method_name, 'args': args, 'kwargs': kwargs})" in rendered
    assert "app.main.get_repository" in rendered
    assert "save" in rendered
