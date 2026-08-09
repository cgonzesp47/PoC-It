from __future__ import annotations

import pytest

from poc_it.testing.domain.models import AssertionSpec
from poc_it.testing.rendering.assertion_renderer import render_assertion_lines


def test_render_assertion_lines_renders_concrete_status_and_required_keys_with_evidence():
    rendered = render_assertion_lines(
        [
            AssertionSpec(kind="STATUS_EQUALS", expected=201, metadata={"evidence": "openapi.responses.201"}),
            AssertionSpec(kind="JSON_HAS_KEYS", expected=["id", "name"], metadata={"evidence": "response.required"}),
        ],
        response_name="resp",
    )

    assert "assert resp.status_code == 201" in rendered
    assert "status_code < 500" not in rendered
    assert "status_code in" not in rendered
    assert "data = resp.json()" in rendered
    assert "assert isinstance(data, dict)" in rendered
    assert "for _key in ['id', 'name']:" in rendered
    assert "# evidence: openapi.responses.201" in rendered
    assert "# evidence: response.required" in rendered


def test_render_assertion_lines_renders_partial_field_and_dynamic_field_checks():
    rendered = render_assertion_lines(
        [
            AssertionSpec(kind="JSON_FIELD_EQUALS", target="name", expected="Keyboard", metadata={"evidence": "contract.example.name"}),
            AssertionSpec(
                kind="JSON_FIELD_DYNAMIC",
                target="id",
                expected=True,
                metadata={"evidence": "response.required.id", "dynamic_mode": "integer"},
            ),
        ]
    )

    assert "assert data['name'] == 'Keyboard'" in rendered
    assert "assert isinstance(data['id'], int)" in rendered
    assert "# evidence: contract.example.name" in rendered
    assert "# evidence: response.required.id" in rendered


def test_render_assertion_lines_renders_type_and_interaction_assertions():
    rendered = render_assertion_lines(
        [
            AssertionSpec(kind="JSON_FIELD_TYPE", target="price", expected="number", metadata={"evidence": "schema.price"}),
            AssertionSpec(
                kind="CALL_COUNT",
                target="repository_double",
                expected=1,
                metadata={"evidence": "runtime.call_graph", "method_name": "create"},
            ),
            AssertionSpec(
                kind="CALL_ARGS_PARTIAL",
                target="repository_double",
                expected={"name": "Keyboard"},
                metadata={"evidence": "handler.arguments", "method_name": "create"},
            ),
        ]
    )

    assert "assert isinstance(data['price'], (int, float))" in rendered
    assert "repository_double.assert_call_count('create', expected_count=1)" in rendered
    assert "repository_double.assert_called_once_with(" in rendered
    assert "    'create'," in rendered
    assert "    expected_subset={'name': 'Keyboard'}," in rendered
    assert "# evidence: runtime.call_graph" in rendered
    assert "# evidence: handler.arguments" in rendered


def test_render_assertion_lines_rejects_unknown_assertion_kind():
    with pytest.raises(ValueError, match="Unsupported assertion kind"):
        render_assertion_lines([{"kind": "NOT_SUPPORTED", "expected": True}])


def test_render_assertion_lines_requires_method_name_for_partial_call_args():
    with pytest.raises(ValueError, match="CALL_ARGS_PARTIAL requires metadata.method_name"):
        render_assertion_lines(
            [
                AssertionSpec(
                    kind="CALL_ARGS_PARTIAL",
                    target="repository_double",
                    expected={"name": "Keyboard"},
                )
            ]
        )
