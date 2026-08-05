from __future__ import annotations

from typing import Any, Iterable

from poc_it.testing.domain.models import AssertionSpec


def render_assertion_lines(
    assertions: Iterable[AssertionSpec | dict[str, Any]],
    *,
    response_name: str = "response",
    response_data_name: str = "data",
) -> str:
    normalized = [_coerce_assertion(assertion) for assertion in assertions]
    lines: list[str] = []
    needs_json_payload = any(_assertion_needs_json_payload(assertion) for assertion in normalized)

    if needs_json_payload:
        lines.extend(
            [
                f"{response_data_name} = {response_name}.json()",
                f"assert isinstance({response_data_name}, dict)",
            ]
        )

    for assertion in normalized:
        lines.extend(_render_single_assertion(assertion, response_name=response_name, response_data_name=response_data_name))

    return "\n".join(lines) + ("\n" if lines else "")


def _coerce_assertion(assertion: AssertionSpec | dict[str, Any]) -> AssertionSpec:
    if isinstance(assertion, AssertionSpec):
        return assertion
    return AssertionSpec(
        kind=str(assertion.get("kind") or "").strip(),
        target=assertion.get("target"),
        expected=assertion.get("expected"),
        metadata=dict(assertion.get("metadata") or {}),
    )


def _assertion_needs_json_payload(assertion: AssertionSpec) -> bool:
    return assertion.kind in {"JSON_HAS_KEYS", "JSON_FIELD_EQUALS", "JSON_FIELD_TYPE", "JSON_FIELD_DYNAMIC"}


def _render_single_assertion(assertion: AssertionSpec, *, response_name: str, response_data_name: str) -> list[str]:
    metadata = dict(assertion.metadata or {})
    lines: list[str] = []

    if assertion.kind == "STATUS_EQUALS":
        evidence = _require_evidence(assertion, default_target="status_code")
        lines.append(f"assert {response_name}.status_code == {repr(assertion.expected)}")
        lines.append(f"# evidence: {evidence}")
        return lines

    if assertion.kind == "JSON_HAS_KEYS":
        evidence = _require_evidence(assertion, default_target="response.required_fields")
        expected_keys = list(assertion.expected or [])
        lines.append(f"for _key in {repr(expected_keys)}:")
        lines.append(f"    assert _key in {response_data_name}")
        lines.append(f"# evidence: {evidence}")
        return lines

    if assertion.kind == "JSON_FIELD_EQUALS":
        evidence = _require_evidence(assertion, default_target=str(assertion.target or "response.field"))
        lines.append(f"assert {response_data_name}[{repr(assertion.target)}] == {repr(assertion.expected)}")
        lines.append(f"# evidence: {evidence}")
        return lines

    if assertion.kind == "JSON_FIELD_TYPE":
        evidence = _require_evidence(assertion, default_target=str(assertion.target or "response.field_type"))
        type_expr = _python_type_expression(assertion.expected)
        lines.append(f"assert isinstance({response_data_name}[{repr(assertion.target)}], {type_expr})")
        lines.append(f"# evidence: {evidence}")
        return lines

    if assertion.kind == "JSON_FIELD_DYNAMIC":
        evidence = _require_evidence(assertion, default_target=str(assertion.target or "response.dynamic_field"))
        dynamic_mode = str(metadata.get("dynamic_mode") or "non_empty")
        field_access = f"{response_data_name}[{repr(assertion.target)}]"
        if dynamic_mode == "uuid":
            lines.append(f"assert isinstance({field_access}, str)")
            lines.append(f"assert len({field_access}) >= 8")
        elif dynamic_mode == "integer":
            lines.append(f"assert isinstance({field_access}, int)")
        else:
            lines.append(f"assert {field_access} is not None")
        lines.append(f"# evidence: {evidence}")
        return lines

    if assertion.kind == "CALL_COUNT":
        evidence = _require_evidence(assertion, default_target=str(assertion.target or "dependency.call_count"))
        target_expr = _require_target(assertion)
        method_name = metadata.get("method_name")
        if method_name:
            lines.append(f"{target_expr}.assert_call_count({repr(method_name)}, expected_count={repr(assertion.expected)})")
        else:
            lines.append(f"assert {target_expr}.call_count == {repr(assertion.expected)}")
        lines.append(f"# evidence: {evidence}")
        return lines

    if assertion.kind == "CALL_ARGS_PARTIAL":
        evidence = _require_evidence(assertion, default_target=str(assertion.target or "dependency.call_args"))
        target_expr = _require_target(assertion)
        method_name = metadata.get("method_name")
        if not method_name:
            raise ValueError("CALL_ARGS_PARTIAL requires metadata.method_name")
        lines.append(
            f"{target_expr}.assert_called_once_with("
        )
        lines.append(f"    {repr(method_name)},")
        lines.append(f"    expected_subset={repr(assertion.expected)},")
        lines.append(")")
        lines.append(f"# evidence: {evidence}")
        return lines

    raise ValueError(f"Unsupported assertion kind: {assertion.kind}")


def _require_evidence(assertion: AssertionSpec, *, default_target: str) -> str:
    evidence = assertion.metadata.get("evidence")
    if evidence:
        return str(evidence)
    return default_target


def _require_target(assertion: AssertionSpec) -> str:
    if assertion.target is None or not str(assertion.target).strip():
        raise ValueError(f"{assertion.kind} requires target")
    return str(assertion.target).strip()


def _python_type_expression(expected: Any) -> str:
    if expected in {"str", str, "string"}:
        return "str"
    if expected in {"int", int, "integer"}:
        return "int"
    if expected in {"float", float, "number"}:
        return "(int, float)"
    if expected in {"bool", bool, "boolean"}:
        return "bool"
    if expected in {"dict", dict, "object"}:
        return "dict"
    if expected in {"list", list, "array"}:
        return "list"
    raise ValueError(f"Unsupported JSON_FIELD_TYPE expectation: {expected!r}")
