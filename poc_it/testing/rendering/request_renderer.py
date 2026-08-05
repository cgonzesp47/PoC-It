from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from poc_it.testing.domain.models import FileSpec, RequestSpec

_SUPPORTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_PATH_PARAM_PATTERN = re.compile(r"\{([^{}]+)\}")


def render_request_call(request: RequestSpec | dict[str, Any], *, client_name: str = "client", response_name: str = "response") -> str:
    spec = _coerce_request_spec(request)
    method = spec.method.upper().strip()
    if method not in _SUPPORTED_METHODS:
        raise ValueError(f"Unsupported HTTP method: {method}")

    call_path = materialize_path(spec.path_template, spec.path_params)
    args = [repr(call_path)]

    if spec.query_params:
        args.append(f"params={repr(spec.query_params)}")
    if spec.headers:
        args.append(f"headers={repr(_sanitize_mapping(spec.headers))}")
    if spec.cookies:
        args.append(f"cookies={repr(_sanitize_mapping(spec.cookies))}")

    has_files = bool(spec.files)
    has_form = bool(spec.form_data)
    has_json = spec.json_body is not None

    if has_files:
        args.append(f"files={repr(_render_files_payload(spec.files))}")
        if has_form:
            args.append(f"data={repr(spec.form_data)}")
    elif has_form:
        args.append(f"data={repr(spec.form_data)}")
    elif has_json:
        args.append(f"json={repr(spec.json_body)}")

    rendered_args = ",\n    ".join(args)
    method_call = method.lower()
    return (
        f"{response_name} = {client_name}.{method_call}(\n"
        f"    {rendered_args},\n"
        ")\n"
    )


def materialize_path(path_template: str, path_params: dict[str, Any]) -> str:
    path = str(path_template or "")

    for key, value in (path_params or {}).items():
        encoded = quote(str(value), safe="")
        path = path.replace(
            "{" + str(key) + "}",
            encoded,
        )

    unresolved = _PATH_PARAM_PATTERN.findall(path)

    if unresolved:
        raise ValueError(
            "Missing values for path parameters: "
            + ", ".join(sorted(set(unresolved)))
            + f" in path {path_template!r}"
        )

    return path


def _coerce_request_spec(request: RequestSpec | dict[str, Any]) -> RequestSpec:
    if isinstance(request, RequestSpec):
        return request

    files = request.get("files") or {}
    coerced_files = {
        str(name): _coerce_file_spec(spec)
        for name, spec in files.items()
    }

    return RequestSpec(
        method=str(request.get("method") or "GET"),
        path_template=str(request.get("path_template") or "/"),
        path_params=dict(request.get("path_params") or {}),
        query_params=dict(request.get("query_params") or {}),
        headers=dict(request.get("headers") or {}),
        cookies=dict(request.get("cookies") or {}),
        json_body=request.get("json_body"),
        form_data=dict(request.get("form_data") or {}),
        files=coerced_files,
        expected_status=request.get("expected_status"),
        allowed_statuses=list(request.get("allowed_statuses") or []),
        response_media_type=request.get("response_media_type"),
    )


def _coerce_file_spec(value: FileSpec | dict[str, Any]) -> FileSpec:
    if isinstance(value, FileSpec):
        return value
    return FileSpec(
        filename=str(value.get("filename") or "upload.bin"),
        content=value.get("content", ""),
        content_type=str(value.get("content_type") or "application/octet-stream"),
    )


def _render_files_payload(files: dict[str, FileSpec]) -> dict[str, tuple[Any, Any, str]]:
    return {
        field_name: (
            file_spec.filename,
            file_spec.content,
            file_spec.content_type,
        )
        for field_name, file_spec in files.items()
    }


def _sanitize_mapping(values: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            sanitized[str(key)] = "REDACTED"
            continue
        text = str(value)
        if "{env:" in text.lower() or text.startswith("$"):
            sanitized[str(key)] = "REDACTED"
        else:
            sanitized[str(key)] = value
    return sanitized
