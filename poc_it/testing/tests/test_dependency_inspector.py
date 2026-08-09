from __future__ import annotations

from poc_it.testing.analysis.dependency_inspector import inspect_fastapi_dependencies


def overrideable_dep() -> str:
    return "ok"


def internal_service_dep() -> str:
    return "svc"


async def async_value_dep() -> str:
    return "async"


def sync_generator_dep():
    yield "db"


async def async_generator_dep():
    yield "adb"


class ExternalApiClient:
    def fetch(self) -> dict:
        return {"ok": True}


def external_dep() -> str:
    return "external"


def get_items(
    svc=internal_service_dep,
    db=sync_generator_dep,
):
    return None


get_items.__module__ = "app.handlers.items"
internal_service_dep.__module__ = "app.dependencies"
sync_generator_dep.__module__ = "app.dependencies"
overrideable_dep.__module__ = "app.dependencies"
async_value_dep.__module__ = "app.dependencies"
async_generator_dep.__module__ = "app.dependencies"
external_dep.__module__ = "vendor.integrations"


def test_dependency_inspector_associates_dependencies_with_the_correct_endpoint():
    def endpoint_a():
        return None

    def endpoint_b():
        return None

    endpoint_a.__module__ = "app.handlers.a"
    endpoint_b.__module__ = "app.handlers.b"

    def dep_a():
        return "a"

    def dep_b():
        return "b"

    dep_a.__module__ = "app.dependencies"
    dep_b.__module__ = "app.dependencies"

    source_by_module = {
        "app.handlers.a": """
def endpoint_a(service):
    return service.create()
""",
        "app.handlers.b": """
def endpoint_b(repo):
    return repo.delete()
""",
    }

    reports = inspect_fastapi_dependencies(
        routes=[
            {
                "endpoint": endpoint_a,
                "endpoint_id": "get_a",
                "path": "/a",
                "method": "GET",
                "dependency_calls": [
                    {"parameter_name": "service", "call": dep_a},
                ],
            },
            {
                "endpoint": endpoint_b,
                "endpoint_id": "delete_b",
                "path": "/b",
                "method": "DELETE",
                "dependency_calls": [
                    {"parameter_name": "repo", "call": dep_b},
                ],
            },
        ],
        source_by_module=source_by_module,
        allowed_dependency_overrides=[],
    )

    assert len(reports) == 2
    assert reports[0].endpoint_id == "get_a"
    assert reports[0].dependencies[0].parameter_name == "service"
    assert reports[0].dependencies[0].methods_used == ["create"]

    assert reports[1].endpoint_id == "delete_b"
    assert reports[1].dependencies[0].parameter_name == "repo"
    assert reports[1].dependencies[0].methods_used == ["delete"]


def test_dependency_inspector_detects_lifecycle_and_annotations():
    source_by_module = {
        "app.handlers.items": """
async def get_items(svc, db, async_value, async_db):
    await async_value.strip()
    svc.create()
    db.commit()
    await async_db.execute()
    return {"ok": True}
""",
    }

    reports = inspect_fastapi_dependencies(
        routes=[
            {
                "endpoint": get_items,
                "endpoint_id": "get_items",
                "path": "/items",
                "method": "GET",
                "dependency_calls": [
                    {"parameter_name": "svc", "call": overrideable_dep},
                    {"parameter_name": "db", "call": sync_generator_dep},
                    {"parameter_name": "async_value", "call": async_value_dep},
                    {"parameter_name": "async_db", "call": async_generator_dep},
                ],
            }
        ],
        source_by_module=source_by_module,
        allowed_dependency_overrides=["app.dependencies.overrideable_dep"],
    )

    deps = {dep.parameter_name: dep for dep in reports[0].dependencies}
    assert deps["svc"].lifecycle == "value"
    assert deps["svc"].return_annotation == "str"
    assert deps["svc"].classification == "OVERRIDEABLE_DEPENDENCY"

    assert deps["db"].lifecycle == "sync_generator"
    assert deps["db"].return_annotation is None

    assert deps["async_value"].lifecycle == "async_value"
    assert deps["async_value"].return_annotation == "str"

    assert deps["async_db"].lifecycle == "async_generator"
    assert deps["async_db"].return_annotation is None


def test_dependency_inspector_reports_uncontrolled_external_instantiation_and_side_effects():
    def create_order():
        return None

    create_order.__module__ = "app.handlers.orders"

    source_by_module = {
        "app.handlers.orders": """
def create_order(api):
    client = ExternalApiClient()
    api.send()
    return client.fetch()
""",
        "vendor.integrations": """
session = SessionFactory()
warmup()
def external_dep():
    return "external"
""",
    }

    reports = inspect_fastapi_dependencies(
        routes=[
            {
                "endpoint": create_order,
                "endpoint_id": "create_order",
                "path": "/orders",
                "method": "POST",
                "dependency_calls": [
                    {"parameter_name": "api", "call": external_dep},
                ],
            }
        ],
        source_by_module=source_by_module,
        allowed_dependency_overrides=[],
    )

    dep = reports[0].dependencies[0]
    assert "ExternalApiClient" in dep.instantiated_outside_depends
    assert dep.import_side_effects == ["SessionFactory", "warmup"]
