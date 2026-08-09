from __future__ import annotations

import pytest

from poc_it.testing.domain.models import FileSpec, RequestSpec
from poc_it.testing.rendering.request_renderer import materialize_path, render_request_call


def test_render_request_call_supports_json_query_headers_and_path():
    rendered = render_request_call(
        RequestSpec(
            method="POST",
            path_template="/products/{product_id}",
            path_params={"product_id": 7},
            query_params={"notify": True},
            headers={"X-Request-ID": "abc"},
            json_body={"name": "Keyboard"},
        )
    )

    assert "response = client.post(" in rendered
    assert "/products/7" in rendered
    assert "params={'notify': True}" in rendered
    assert "headers={'X-Request-ID': 'abc'}" in rendered
    assert "json={'name': 'Keyboard'}" in rendered
    assert "data=" not in rendered
    assert "files=" not in rendered


def test_render_request_call_escapes_path_params():
    assert materialize_path("/users/{slug}", {"slug": "alice/bob"}) == "/users/alice%2Fbob"
    assert materialize_path("/files/{name}", {"name": "report 2026.txt"}) == "/files/report%202026.txt"


def test_materialize_path_replaces_all_placeholders():
    assert materialize_path(
        "/items/{item_id}",
        {"item_id": 1},
    ) == "/items/1"


def test_materialize_path_rejects_unresolved_placeholders():
    with pytest.raises(
        ValueError,
        match="item_id",
    ):
        materialize_path(
            "/items/{item_id}",
            {},
        )


def test_render_request_call_uses_form_without_json():
    rendered = render_request_call(
        {
            "method": "PATCH",
            "path_template": "/login",
            "form_data": {"username": "u", "password": "p"},
            "json_body": None,
        }
    )

    assert "response = client.patch(" in rendered
    assert "data={'username': 'u', 'password': 'p'}" in rendered
    assert "json=" not in rendered


def test_render_request_call_uses_files_and_form_without_incompatible_json():
    rendered = render_request_call(
        RequestSpec(
            method="PUT",
            path_template="/upload",
            form_data={"folder": "docs"},
            json_body={"unexpected": True},
            files={
                "file": FileSpec(
                    filename="report.txt",
                    content="hello",
                    content_type="text/plain",
                )
            },
        )
    )

    assert "response = client.put(" in rendered
    assert "files={'file': ('report.txt', 'hello', 'text/plain')}" in rendered
    assert "data={'folder': 'docs'}" in rendered
    assert "json=" not in rendered


def test_render_request_call_supports_cookies_head_and_options():
    rendered = render_request_call(
        RequestSpec(
            method="HEAD",
            path_template="/health",
            cookies={"session": "abc"},
            headers={"X-Mode": "test"},
        ),
        client_name="api_client",
        response_name="resp",
    )

    assert "resp = api_client.head(" in rendered
    assert "cookies={'session': 'abc'}" in rendered
    assert "headers={'X-Mode': 'test'}" in rendered


def test_render_request_call_redacts_environment_like_sensitive_values():
    rendered = render_request_call(
        RequestSpec(
            method="GET",
            path_template="/secure",
            headers={"Authorization": "$TOKEN"},
            cookies={"session": "{env:SESSION_COOKIE}"},
        )
    )

    assert "headers={'Authorization': 'REDACTED'}" in rendered
    assert "cookies={'session': 'REDACTED'}" in rendered


def test_render_request_call_rejects_unsupported_methods():
    with pytest.raises(ValueError, match="Unsupported HTTP method: TRACE"):
        render_request_call(RequestSpec(method="TRACE", path_template="/trace"))
