from __future__ import annotations

from poc_it.materializacion.codegen import sort_file_contracts_for_generation


def test_generation_order_places_internal_dependencies_before_consumers():
    contracts = [
        {
            "path": "README.md",
            "kind": "docs",
        },
        {
            "path": "app/main.py",
            "kind": "main",
        },
        {
            "path": "app/api/router.py",
            "kind": "router",
        },
        {
            "path": "tests/test_upload.py",
            "kind": "test",
        },
        {
            "path": "app/api/endpoints/upload.py",
            "kind": "endpoint",
        },
        {
            "path": "app/integrations/drive.py",
            "kind": "integration",
        },
        {
            "path": "app/services/upload_service.py",
            "kind": "service",
        },
        {
            "path": "app/repositories/file_repository.py",
            "kind": "repository",
        },
        {
            "path": "app/schemas/upload.py",
            "kind": "schema",
        },
        {
            "path": "app/core/config.py",
            "kind": "config",
        },
        {
            "path": "requirements.txt",
            "kind": "requirements",
        },
        {
            "path": "app/__init__.py",
            "kind": "package_init",
        },
    ]

    ordered = sort_file_contracts_for_generation(contracts)

    assert [item["kind"] for item in ordered] == [
        "package_init",
        "requirements",
        "config",
        "schema",
        "repository",
        "service",
        "integration",
        "endpoint",
        "test",
        "router",
        "main",
        "docs",
    ]


def test_generation_order_places_integration_before_endpoint():
    contracts = [
        {
            "path": "app/api/endpoints/upload.py",
            "kind": "endpoint",
        },
        {
            "path": "app/integrations/drive.py",
            "kind": "integration",
        },
    ]

    ordered = sort_file_contracts_for_generation(contracts)

    assert [item["kind"] for item in ordered] == [
        "integration",
        "endpoint",
    ]


def test_generation_order_keeps_unknown_kinds_after_known_kinds():
    contracts = [
        {
            "path": "custom/generated.py",
            "kind": "custom_kind",
        },
        {
            "path": "app/api/endpoints/items.py",
            "kind": "endpoint",
        },
        {
            "path": "app/services/items.py",
            "kind": "service",
        },
    ]

    ordered = sort_file_contracts_for_generation(contracts)

    assert [item["kind"] for item in ordered] == [
        "service",
        "endpoint",
        "custom_kind",
    ]


def test_generation_order_is_deterministic():
    contracts = [
        {
            "path": "app/services/zeta.py",
            "kind": "service",
        },
        {
            "path": "app/services/alpha.py",
            "kind": "service",
        },
    ]

    first = sort_file_contracts_for_generation(contracts)
    second = sort_file_contracts_for_generation(list(reversed(contracts)))

    assert [item["path"] for item in first] == [
        "app/services/alpha.py",
        "app/services/zeta.py",
    ]

    assert [item["path"] for item in second] == [
        "app/services/alpha.py",
        "app/services/zeta.py",
    ]
