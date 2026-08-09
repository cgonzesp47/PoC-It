from __future__ import annotations

import asyncio

import pytest

from poc_it.testing.execution.test_doubles import (
    DependencyBehavior,
    DependencyProtocol,
    InMemoryStatefulDouble,
    MethodProtocol,
    ProbeDouble,
    StatefulProtocolError,
    StatefulProtocolMap,
    StrictDouble,
    StrictDoubleError,
)


def test_probe_double_records_calls_without_failing():
    probe = ProbeDouble("app.dependencies.service")

    result = probe.create({"name": "demo"})
    probe.delete(item_id=1)

    assert result is None
    assert [call.method_name for call in probe.calls] == ["create", "delete"]
    assert probe.calls[0].args == ({"name": "demo"},)
    assert probe.calls[1].kwargs == {"item_id": 1}


def test_strict_double_fails_on_unexpected_method():
    double = StrictDouble(
        DependencyProtocol(
            dependency_fqn="app.dependencies.repo",
            methods=[MethodProtocol(method_name="get_by_id")],
            strict=True,
        )
    )

    with pytest.raises(StrictDoubleError, match="Unexpected method 'delete'"):
        double.delete


def test_strict_double_returns_configured_values_and_records_calls():
    double = StrictDouble(
        DependencyProtocol(
            dependency_fqn="app.dependencies.repo",
            methods=[MethodProtocol(method_name="get_by_id")],
            strict=True,
        ),
        behaviors=[
            DependencyBehavior(
                dependency_fqn="app.dependencies.repo",
                method_name="get_by_id",
                action="return",
                value={"id": 1},
            )
        ],
    )

    result = double.get_by_id(1)

    assert result == {"id": 1}
    assert double.assert_called_methods() == ["get_by_id"]
    assert double.calls[0].args == (1,)


def test_strict_double_can_raise_configured_errors():
    double = StrictDouble(
        DependencyProtocol(
            dependency_fqn="app.dependencies.client",
            methods=[MethodProtocol(method_name="send")],
            strict=True,
        ),
        behaviors=[
            DependencyBehavior(
                dependency_fqn="app.dependencies.client",
                method_name="send",
                action="raise",
                value=RuntimeError("boom"),
            )
        ],
    )

    with pytest.raises(RuntimeError, match="boom"):
        double.send({"payload": True})

    assert double.assert_called_methods() == ["send"]


def test_strict_double_supports_async_methods():
    double = StrictDouble(
        DependencyProtocol(
            dependency_fqn="app.dependencies.async_client",
            methods=[MethodProtocol(method_name="fetch", is_async=True)],
            strict=True,
        ),
        behaviors=[
            DependencyBehavior(
                dependency_fqn="app.dependencies.async_client",
                method_name="fetch",
                action="async_return",
                value={"ok": True},
            )
        ],
    )

    result = asyncio.run(double.fetch("/status"))

    assert result == {"ok": True}
    assert double.assert_called_methods() == ["fetch"]


def test_method_protocol_uses_method_name():
    protocol = MethodProtocol(
        method_name="save",
        is_async=True,
    )

    assert protocol.method_name == "save"
    assert protocol.name == "save"
    assert protocol.is_async is True


def test_method_protocol_does_not_require_legacy_name_argument():
    protocol = MethodProtocol(method_name="find")
    assert protocol.method_name == "find"


def test_strict_double_can_return_none_for_not_found_cases():
    double = StrictDouble(
        DependencyProtocol(
            dependency_fqn="app.dependencies.repo",
            methods=[MethodProtocol(method_name="get_by_id")],
            strict=True,
        )
    )

    double.configure_return("get_by_id", None)

    result = double.get_by_id(999)

    assert result is None
    assert double.assert_called_methods() == ["get_by_id"]


def test_strict_double_consumes_behaviors_per_case_without_leaking():
    double = StrictDouble(
        DependencyProtocol(
            dependency_fqn="app.dependencies.drive",
            methods=[MethodProtocol(method_name="upload_file")],
            strict=True,
        ),
        behaviors=[
            DependencyBehavior(
                dependency_fqn="app.dependencies.drive",
                method_name="upload_file",
                action="return",
                value={"id": "file-123", "name": "report.pdf"},
            ),
            DependencyBehavior(
                dependency_fqn="app.dependencies.drive",
                method_name="upload_file",
                action="raise",
                value=RuntimeError("quota exceeded"),
            ),
        ],
    )

    first = double.upload_file({"filename": "report.pdf"})
    assert first == {"id": "file-123", "name": "report.pdf"}

    with pytest.raises(RuntimeError, match="quota exceeded"):
        double.upload_file({"filename": "report.pdf"})

    assert double.assert_called_methods() == ["upload_file", "upload_file"]


def test_strict_double_can_yield_and_persist_state():
    shared_state = {}
    double = StrictDouble(
        DependencyProtocol(
            dependency_fqn="app.dependencies.stateful",
            methods=[MethodProtocol(method_name="open_stream")],
            strict=True,
        ),
        behaviors=[
            DependencyBehavior(
                dependency_fqn="app.dependencies.stateful",
                method_name="open_stream",
                action="yield",
                value=("last_stream", {"token": "abc"}),
            )
        ],
        state=shared_state,
    )

    stream = double.open_stream()
    yielded = next(iter(stream))

    assert yielded == {"token": "abc"}
    assert shared_state == {"last_stream": {"token": "abc"}}
    assert double.assert_called_methods() == ["open_stream"]


def test_strict_double_has_no_database_specific_logic():
    double = StrictDouble(
        DependencyProtocol(
            dependency_fqn="vendor.mail.MailClient",
            methods=[MethodProtocol(method_name="deliver")],
            strict=True,
        ),
        behaviors=[
            DependencyBehavior(
                dependency_fqn="vendor.mail.MailClient",
                method_name="deliver",
                action="return",
                value="sent",
            )
        ],
    )

    assert double.deliver(to="user@example.com") == "sent"
    assert "db" not in repr(double.protocol).lower()


def test_in_memory_stateful_double_supports_crud_with_semantic_mapping():
    shared_state = {}
    double = InMemoryStatefulDouble(
        StatefulProtocolMap(
            dependency_fqn="app.dependencies.product_repo",
            operations={
                "add_product": "create",
                "find_product": "get_by_id",
                "all_products": "list",
                "save_product": "update",
                "remove_product": "delete",
                "has_product": "exists",
            },
            entity_name="product",
            id_field="id",
        ),
        state=shared_state,
    )

    created = double.add_product({"name": "Keyboard"})
    assert created == {"id": 1, "name": "Keyboard"}
    assert shared_state["next_id"] == 2

    fetched = double.find_product(1)
    assert fetched == {"id": 1, "name": "Keyboard"}
    assert double.has_product(product_id=1) is True

    updated = double.save_product(1, {"name": "Mechanical Keyboard"})
    assert updated == {"id": 1, "name": "Mechanical Keyboard"}

    listed = double.all_products()
    assert listed == [{"id": 1, "name": "Mechanical Keyboard"}]

    deleted = double.remove_product(1)
    assert deleted is True
    assert double.find_product(1) is None
    assert double.has_product(product_id=1) is False
    assert shared_state["items"] == {}
    assert double.assert_called_methods() == [
        "add_product",
        "find_product",
        "has_product",
        "save_product",
        "all_products",
        "remove_product",
        "find_product",
        "has_product",
    ]


def test_in_memory_stateful_double_fails_for_unexpected_or_ambiguous_protocols():
    with pytest.raises(StatefulProtocolError, match="create operation"):
        InMemoryStatefulDouble(
            StatefulProtocolMap(
                dependency_fqn="app.dependencies.repo",
                operations={"find_product": "get_by_id"},
            )
        )

    double = InMemoryStatefulDouble(
        StatefulProtocolMap(
            dependency_fqn="app.dependencies.repo",
            operations={"add_product": "create"},
        )
    )

    with pytest.raises(StatefulProtocolError, match="Unexpected stateful method 'delete'"):
        double.delete

    with pytest.raises(StatefulProtocolError, match="dictionary-like payload"):
        double.add_product()


def test_in_memory_stateful_double_returns_deterministic_missing_results():
    double = InMemoryStatefulDouble(
        StatefulProtocolMap(
            dependency_fqn="app.dependencies.repo",
            operations={
                "create_item": "create",
                "get_item": "get",
                "update_item": "update",
                "delete_item": "delete",
                "exists_item": "exists",
            },
            entity_name="item",
        )
    )

    assert double.get_item(999) is None
    assert double.update_item(999, {"name": "ghost"}) is None
    assert double.delete_item(999) is False
    assert double.exists_item(999) is False


def test_strict_double_supports_google_drive_style_chains():
    double = StrictDouble(
        DependencyProtocol(
            protocol_id="google_drive_service",
            dependency_fqn="app.dependencies.get_drive_service",
            methods=[MethodProtocol(method_name="files", returns_protocol="drive_files_resource")],
            nested_protocols={
                "drive_files_resource": DependencyProtocol(
                    protocol_id="drive_files_resource",
                    dependency_fqn="",
                    methods=[MethodProtocol(method_name="create", returns_protocol="drive_request")],
                    nested_protocols={
                        "drive_request": DependencyProtocol(
                            protocol_id="drive_request",
                            dependency_fqn="",
                            methods=[MethodProtocol(method_name="execute")],
                            nested_protocols={},
                        )
                    },
                )
            },
        ),
        behaviors=[
            DependencyBehavior(
                dependency_fqn="app.dependencies.get_drive_service",
                call_path=("files", "create", "execute"),
                action="return",
                value={"id": "file-123"},
            )
        ],
    )

    result = double.files().create(body={"name": "demo.txt"}).execute()

    assert result == {"id": "file-123"}
    assert [call.call_path for call in double.calls] == [
        ("files",),
        ("files", "create"),
        ("files", "create", "execute"),
    ]


def test_strict_double_detects_unexpected_calls_inside_chain():
    double = StrictDouble(
        DependencyProtocol(
            protocol_id="google_drive_service",
            dependency_fqn="app.dependencies.get_drive_service",
            methods=[MethodProtocol(method_name="files", returns_protocol="drive_files_resource")],
            nested_protocols={
                "drive_files_resource": DependencyProtocol(
                    protocol_id="drive_files_resource",
                    dependency_fqn="",
                    methods=[MethodProtocol(method_name="create", returns_protocol="drive_request")],
                    nested_protocols={
                        "drive_request": DependencyProtocol(
                            protocol_id="drive_request",
                            dependency_fqn="",
                            methods=[MethodProtocol(method_name="execute")],
                            nested_protocols={},
                        )
                    },
                )
            },
        )
    )

    with pytest.raises(StrictDoubleError, match="Unexpected method 'delete'"):
        double.files().delete


def test_strict_double_rejects_call_paths_over_depth_limit():
    deep_protocol = DependencyProtocol(
        protocol_id="p0",
        dependency_fqn="app.dependencies.deep_service",
        methods=[MethodProtocol(method_name="a", returns_protocol="p1")],
        nested_protocols={
            "p1": DependencyProtocol(
                protocol_id="p1",
                dependency_fqn="",
                methods=[MethodProtocol(method_name="b", returns_protocol="p2")],
                nested_protocols={
                    "p2": DependencyProtocol(
                        protocol_id="p2",
                        dependency_fqn="",
                        methods=[MethodProtocol(method_name="c", returns_protocol="p3")],
                        nested_protocols={
                            "p3": DependencyProtocol(
                                protocol_id="p3",
                                dependency_fqn="",
                                methods=[MethodProtocol(method_name="d", returns_protocol="p4")],
                                nested_protocols={
                                    "p4": DependencyProtocol(
                                        protocol_id="p4",
                                        dependency_fqn="",
                                        methods=[MethodProtocol(method_name="e")],
                                        nested_protocols={},
                                    )
                                },
                            )
                        },
                    )
                },
            )
        },
    )

    with pytest.raises(StrictDoubleError, match="MAX_DEPENDENCY_CHAIN_DEPTH"):
        StrictDouble(
            deep_protocol,
            behaviors=[
                DependencyBehavior(
                    dependency_fqn="app.dependencies.deep_service",
                    call_path=("a", "b", "c", "d", "e"),
                    action="return",
                    value={"too": "deep"},
                )
            ],
        )
