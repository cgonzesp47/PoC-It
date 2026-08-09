import json

from poc_it.orquestacion.test_plan import FileSpec, RequestSpec
from poc_it.testing.rendering.request_renderer import materialize_path, render_request_call


def test_request_spec_is_serializable_with_all_http_components():
    request = RequestSpec(
        method="POST",
        path_template="/items/{item_id}",
        path_params={"item_id": 123},
        query_params={"verbose": "true"},
        headers={"Authorization": "Bearer token"},
        cookies={"session": "abc"},
        json_body={"name": "demo"},
        form_data={},
        files={"attachment": FileSpec(filename="a.txt", content="hello", content_type="text/plain")},
        expected_status=201,
        allowed_statuses=[201],
        response_media_type="application/json",
    )

    payload = json.loads(json.dumps(request, default=lambda obj: obj.__dict__))
    assert payload["method"] == "POST"
    assert payload["path_template"] == "/items/{item_id}"
    assert payload["path_params"]["item_id"] == 123
    assert payload["query_params"]["verbose"] == "true"
    assert payload["headers"]["Authorization"] == "Bearer token"
    assert payload["cookies"]["session"] == "abc"
    assert payload["files"]["attachment"]["filename"] == "a.txt"


def test_materialize_path_uses_path_params():
    assert materialize_path("/items/{item_id}", {"item_id": 7}) == "/items/7"
    assert materialize_path("/users/{slug}", {"slug": "alice"}) == "/users/alice"


def test_render_request_call_does_not_send_json_when_body_is_empty():
    code = render_request_call(
        {
            "method": "GET",
            "path_template": "/items/{item_id}",
            "path_params": {"item_id": 9},
            "query_params": {"include": "details"},
            "headers": {"Authorization": "Bearer token"},
            "cookies": {"session": "abc"},
            "json_body": None,
            "form_data": {},
            "files": {},
        },
        client_name="client",
        response_name="response",
    )

    assert "response = client.get(" in code
    assert "/items/9" in code
    assert "{item_id}" not in code
    assert "params={'include': 'details'}" in code
    assert "headers={'Authorization': 'Bearer token'}" in code
    assert "cookies={'session': 'abc'}" in code
    assert "json=" not in code


def test_render_request_call_uses_data_for_form_requests():
    code = render_request_call(
        {
            "method": "POST",
            "path_template": "/login",
            "path_params": {},
            "query_params": {},
            "headers": {},
            "cookies": {},
            "json_body": None,
            "form_data": {"username": "u", "password": "p"},
            "files": {},
        },
        client_name="client",
        response_name="response",
    )

    assert "response = client.post(" in code
    assert "/login" in code
    assert "data={'username': 'u', 'password': 'p'}" in code
    assert "json=" not in code
    assert "files=" not in code


def test_render_request_call_uses_files_and_does_not_mix_with_json():
    code = render_request_call(
        {
            "method": "POST",
            "path_template": "/upload",
            "path_params": {},
            "query_params": {},
            "headers": {},
            "cookies": {},
            "json_body": {"unexpected": True},
            "form_data": {"folder": "docs"},
            "files": {
                "file": {
                    "filename": "report.txt",
                    "content": "hello",
                    "content_type": "text/plain",
                }
            },
        },
        client_name="client",
        response_name="response",
    )

    assert "response = client.post(" in code
    assert "/upload" in code
    assert "files={'file': ('report.txt', 'hello', 'text/plain')}" in code
    assert "data={'folder': 'docs'}" in code
    assert "json=" not in code
