from __future__ import annotations

from poc_it.testing.execution.test_doubles import DependencyBehavior, DependencyProtocol, MethodProtocol
from poc_it.testing.rendering.client_fixture_renderer import render_client_fixture
from poc_it.testing.rendering.dependency_renderer import (
    RenderableDependency,
    render_dependency_fixtures,
    render_dependency_override_fixture,
)


def test_render_dependency_fixtures_generates_stable_stateful_double_fixture():
    rendered = render_dependency_fixtures(
        dependencies=[
            RenderableDependency(
                fixture_name="product_repository_double",
                dependency_fqn="app.repositories.product.ProductRepository",
                import_fqn="app.dependencies.get_product_repository",
                protocol=DependencyProtocol(
                    dependency_fqn="app.repositories.product.ProductRepository",
                    methods=[MethodProtocol(method_name="save"), MethodProtocol(method_name="get_by_id", is_async=True)],
                    strict=True,
                ),
                behaviors=[
                    DependencyBehavior(
                        dependency_fqn="app.repositories.product.ProductRepository",
                        method_name="save",
                        action="return",
                        value={"id": 1},
                    )
                ],
                stateful=True,
            )
        ]
    )

    assert "def product_repository_double():" in rendered
    assert "_state = {}" in rendered
    assert "StrictDouble(" in rendered
    assert "MethodProtocol(method_name='save', is_async=False)" in rendered
    assert "MethodProtocol(method_name='get_by_id', is_async=True)" in rendered
    assert "DependencyBehavior(dependency_fqn='app.repositories.product.ProductRepository'" in rendered


def test_render_dependency_override_fixture_uses_real_callables_and_clears_overrides():
    rendered = render_dependency_override_fixture(
        app_fixture_name="app",
        dependencies=[
            RenderableDependency(
                fixture_name="product_repository_double",
                dependency_fqn="app.repositories.product.ProductRepository",
                import_fqn="app.dependencies.get_product_repository",
                protocol=DependencyProtocol(
                    dependency_fqn="app.repositories.product.ProductRepository",
                    methods=[MethodProtocol(method_name="save")],
                    strict=True,
                ),
            )
        ],
    )

    assert "def dependency_overrides_registry(app, product_repository_double):" in rendered
    assert "_callable = _import_callable('app.dependencies.get_product_repository')" in rendered
    assert "app.dependency_overrides[_callable] = _product_repository_double_override" in rendered
    assert "yield _registry" in rendered
    assert "finally:" in rendered
    assert "app.dependency_overrides.clear()" in rendered
    assert "dependency_overrides[" not in rendered.split("try:", 1)[0]


def test_render_dependency_override_fixture_supports_async_dependencies():
    rendered = render_dependency_override_fixture(
        dependencies=[
            RenderableDependency(
                fixture_name="mail_client_double",
                dependency_fqn="vendor.mail.MailClient",
                import_fqn="app.dependencies.get_mail_client",
                protocol=DependencyProtocol(
                    dependency_fqn="vendor.mail.MailClient",
                    methods=[MethodProtocol(method_name="send", is_async=True)],
                    strict=True,
                ),
            )
        ],
    )

    assert "async def _mail_client_double_override(_instance=mail_client_double):" in rendered


def test_render_client_fixture_sync_and_async_modes():
    rendered_sync = render_client_fixture(tests_style="sync", app_fixture_name="application")
    rendered_async = render_client_fixture(tests_style="async", app_fixture_name="application")

    assert "def client(application, dependency_overrides_registry):" in rendered_sync
    assert "with TestClient(application) as test_client:" in rendered_sync

    assert "async def client(application, dependency_overrides_registry):" in rendered_async
    assert "httpx.AsyncClient(app=application, base_url='http://test')" in rendered_async
