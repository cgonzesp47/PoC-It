from __future__ import annotations

from poc_it.orquestacion.contract_test_renderer import _render_assertion_lines, _render_request_call
from poc_it.testing.rendering.request_renderer import materialize_path


def test_request_renderer_materializes_path_with_escaping():
    assert materialize_path("/users/{slug}", {"slug": "alice/bob"}) == "/users/alice%2Fbob"


def test_legacy_renderer_delegates_request_rendering_without_incompatible_json():
    code = _render_request_call(
        {
            "method": "POST",
            "path_template": "/upload/{folder}",
            "path_params": {"folder": "docs/private"},
            "form_data": {"kind": "report"},
            "json_body": {"unexpected": True},
            "files": {
                "file": {
                    "filename": "report.txt",
                    "content": "hello",
                    "content_type": "text/plain",
                }
            },
        },
        "POST",
        "/upload/{folder}",
    )

    assert "resp = client.post(" in code
    assert "/upload/docs%2Fprivate" in code
    assert "files={'file': ('report.txt', 'hello', 'text/plain')}" in code
    assert "data={'kind': 'report'}" in code
    assert "json=" not in code


def test_legacy_renderer_delegates_assertions_to_v2_renderer():
    code = _render_assertion_lines(
        [
            {
                "kind": "STATUS_EQUALS",
                "expected": 201,
                "metadata": {"evidence": "openapi.responses.201"},
            },
            {
                "kind": "JSON_FIELD_EQUALS",
                "target": "name",
                "expected": "Keyboard",
                "metadata": {"evidence": "response.example.name"},
            },
            {
                "kind": "CALL_ARGS_PARTIAL",
                "target": "repository_double",
                "expected": {"name": "Keyboard"},
                "metadata": {"evidence": "handler.arguments", "method_name": "create"},
            },
        ]
    )

    assert "assert resp.status_code == 201" in code
    assert "data = resp.json()" in code
    assert "assert data['name'] == 'Keyboard'" in code
    assert "repository_double.assert_called_once_with(" in code
    assert "# evidence: openapi.responses.201" in code
    assert "# evidence: handler.arguments" in code
    assert "status_code < 500" not in code
