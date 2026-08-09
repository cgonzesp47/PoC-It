from __future__ import annotations

from poc_it.testing.rendering.assertion_renderer import (
    render_assertion_lines as _v2_render_assertion_lines,
)
from poc_it.testing.rendering.contract_test_renderer import render_tests_from_test_plan
from poc_it.testing.rendering.request_renderer import (
    render_request_call as _v2_render_request_call,
)


def _render_assertion_lines(assertions):
    return _v2_render_assertion_lines(
        assertions,
        response_name="resp",
        response_data_name="data",
    )


def _render_request_call(request, method=None, path_template=None):
    request_dict = dict(request or {})
    if method is not None and "method" not in request_dict:
        request_dict["method"] = method
    if path_template is not None and "path_template" not in request_dict:
        request_dict["path_template"] = path_template
    return _v2_render_request_call(
        request_dict,
        client_name="client",
        response_name="resp",
    )


__all__ = [
    "render_tests_from_test_plan",
    "_render_assertion_lines",
    "_render_request_call",
]
