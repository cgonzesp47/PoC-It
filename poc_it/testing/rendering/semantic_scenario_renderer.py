from __future__ import annotations

from typing import Any, Dict, List


def render_semantic_scenarios(plan: Dict[str, Any]) -> str:
    scenarios = [
        scenario
        for scenario in (plan.get("scenario_plans") or [])
        if isinstance(scenario, dict) and _is_renderable_scenario(scenario)
    ]

    if not scenarios:
        return "# No stateful scenarios were applicable for this project.\n"

    blocks = [*_render_header_blocks(), *[_render_scenario_block(scenario) for scenario in scenarios]]
    return "\n\n\n".join(blocks).rstrip() + "\n"


def _render_header_blocks() -> List[str]:
    return [
        "import pytest",
        (
            "class InMemoryEntityStore:\n"
            "    def __init__(self):\n"
            "        self.items = {}\n"
            "        self.next_id = 1\n"
            "\n"
            "    def create(self, payload):\n"
            "        item = dict(payload)\n"
            "        item['id'] = self.next_id\n"
            "        self.items[self.next_id] = item\n"
            "        self.next_id += 1\n"
            "        return dict(item)\n"
            "\n"
            "    def get(self, item_id):\n"
            "        return self.items.get(item_id)\n"
            "\n"
            "    def update(self, item_id, payload):\n"
            "        if item_id not in self.items:\n"
            "            return None\n"
            "        current = dict(self.items[item_id])\n"
            "        current.update(dict(payload))\n"
            "        current['id'] = item_id\n"
            "        self.items[item_id] = current\n"
            "        return dict(current)\n"
            "\n"
            "    def delete(self, item_id):\n"
            "        return self.items.pop(item_id, None)\n"
            "\n"
            "    def list(self):\n"
            "        return [dict(item) for _, item in sorted(self.items.items())]\n"
        ),
        (
            "def _interpolate_template(value, context):\n"
            "    if isinstance(value, str):\n"
            "        try:\n"
            "            return value.format(**context)\n"
            "        except KeyError as exc:\n"
            "            missing = exc.args[0]\n"
            "            raise AssertionError(f'missing captured value for placeholder: {missing}') from exc\n"
            "    if isinstance(value, list):\n"
            "        return [_interpolate_template(item, context) for item in value]\n"
            "    if isinstance(value, dict):\n"
            "        return {key: _interpolate_template(item, context) for key, item in value.items()}\n"
            "    return value\n"
        ),
    ]


def _is_renderable_scenario(scenario: Dict[str, Any]) -> bool:
    operations = [step for step in (scenario.get("operations") or []) if isinstance(step, dict)]
    if not operations:
        return False
    confidence = scenario.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except Exception:
        return False
    if confidence < 0.6:
        return False
    shared_dependencies = [str(item).strip() for item in (scenario.get("shared_dependencies") or []) if str(item).strip()]
    if len(operations) > 1 and not shared_dependencies:
        return False
    if not any(_step_has_observable_assertions(step) for step in operations):
        return False
    return True


def _scenario_id(value: str) -> str:
    base = str(value or "").strip().replace("/", "_").replace("{", "").replace("}", "")
    base = "".join(ch for ch in base if ch.isalnum() or ch == "_")
    while "__" in base:
        base = base.replace("__", "_")
    return base.strip("_").lower() or "scenario"


def _py_literal(obj: Any) -> str:
    return repr(obj)


def _render_scenario_block(scenario: Dict[str, Any]) -> str:
    sid = _scenario_id(str(scenario.get("scenario_id") or scenario.get("name") or "scenario"))
    name = str(scenario.get("name") or sid)
    evidence = ", ".join(str(item) for item in (scenario.get("evidence") or []) if str(item).strip())
    confidence = float(scenario.get("confidence", 0.0))
    shared_dependencies = [str(item) for item in (scenario.get("shared_dependencies") or []) if str(item).strip()]

    lines: List[str] = [
        "@pytest.mark.scenario",
        f"def test_scenario_{sid}(client, dependency_overrides_guard):",
        f'    """{name} | evidence: {evidence} | confidence: {confidence}"""',
        "    context = {}",
        f"    declared_shared_dependencies = {_py_literal(shared_dependencies)}",
        "    dependency_overrides_guard.clear()",
        "    try:",
    ]

    for dependency in shared_dependencies:
        lines.extend(
            [
                "        repository_fake = InMemoryEntityStore()",
                f"        dependency_overrides_guard.bind({dependency!r}, repository_fake)",
            ]
        )

    if not shared_dependencies:
        lines.append("        assert declared_shared_dependencies == []")

    operations = [step for step in (scenario.get("operations") or []) if isinstance(step, dict)]
    for index, step in enumerate(operations, start=1):
        lines.extend(_render_step_lines(step, index=index))

    lines.extend(
        [
            "    finally:",
            "        dependency_overrides_guard.clear()",
            "        context.clear()",
        ]
    )
    return "\n".join(lines)


def _render_step_lines(step: Dict[str, Any], *, index: int) -> List[str]:
    request = step.get("request") if isinstance(step.get("request"), dict) else {}
    method = str(request.get("method") or "GET").upper()
    path_template = str(request.get("path_template") or request.get("path") or "/")
    response_name = f"response_{index}"
    label = str(step.get("name") or step.get("operation_id") or f"step_{index}")

    lines: List[str] = [
        f"        # Step {index}: {label}",
        f"        step_{index}_path = _interpolate_template({_py_literal(path_template)}, context)",
    ]

    if "path_params" in request:
        lines.append(f"        step_{index}_path_params = _interpolate_template({_py_literal(request.get('path_params'))}, context)")
    if "query_params" in request:
        lines.append(f"        step_{index}_query = _interpolate_template({_py_literal(request.get('query_params'))}, context)")
    if "headers" in request:
        lines.append(f"        step_{index}_headers = _interpolate_template({_py_literal(request.get('headers'))}, context)")
    if "json_body" in request:
        lines.append(f"        step_{index}_json = _interpolate_template({_py_literal(request.get('json_body'))}, context)")
    elif "json" in request:
        lines.append(f"        step_{index}_json = _interpolate_template({_py_literal(request.get('json'))}, context)")

    call_kwargs: List[str] = []
    if "query_params" in request:
        call_kwargs.append(f"params=step_{index}_query")
    if "headers" in request:
        call_kwargs.append(f"headers=step_{index}_headers")
    if "json_body" in request or "json" in request:
        call_kwargs.append(f"json=step_{index}_json")
    kwargs_suffix = ", " + ", ".join(call_kwargs) if call_kwargs else ""

    lines.append(f"        {response_name} = client.request({_py_literal(method)}, step_{index}_path{kwargs_suffix})")
    lines.extend(_render_step_assertions(step=step, response_name=response_name, index=index))
    lines.extend(_render_capture_lines(step.get("capture") or {}, response_name=response_name, index=index))
    return lines


def _step_has_observable_assertions(step: Dict[str, Any]) -> bool:
    observable_kinds = {
        "STATUS_EQUALS",
        "JSON_FIELD_EQUALS",
        "STATE_CONTAINS",
        "STATE_NOT_CONTAINS",
        "DEPENDENCY_CALLED",
        "CALL_COUNT_EQUALS",
    }
    assertions = [item for item in (step.get("assertions") or []) if isinstance(item, dict)]
    return any(str(assertion.get("kind") or "").strip() in observable_kinds for assertion in assertions)


def _render_step_assertions(*, step: Dict[str, Any], response_name: str, index: int) -> List[str]:
    assertions = [item for item in (step.get("assertions") or []) if isinstance(item, dict)]
    lines: List[str] = []
    for assertion in assertions:
        kind = str(assertion.get("kind") or "").strip()
        if kind == "STATUS_EQUALS":
            lines.append(
                f"        assert {response_name}.status_code == {int(assertion.get('expected'))}, "
                f"'Scenario step {index} failed: expected status {int(assertion.get('expected'))}'"
            )
        elif kind == "JSON_HAS_KEYS":
            keys = [str(item) for item in (assertion.get("expected") or [])]
            lines.append(f"        data_{index} = {response_name}.json()")
            for key in keys:
                lines.append(
                    f"        assert {key!r} in data_{index}, "
                    f"'Scenario step {index} failed: missing response key {key}'"
                )
        elif kind == "JSON_FIELD_EQUALS":
            field_name = str(assertion.get("field") or "")
            expected = assertion.get("expected")
            lines.append(f"        data_{index} = {response_name}.json()")
            lines.append(
                f"        assert data_{index}[{field_name!r}] == {_py_literal(expected)}, "
                f"'Scenario step {index} failed: unexpected value for {field_name}'"
            )
    return lines


def _render_capture_lines(capture: Dict[str, Any], *, response_name: str, index: int) -> List[str]:
    lines: List[str] = []
    for source, alias in capture.items():
        source_name = str(source or "").strip()
        alias_name = str(alias or "").strip()
        if not source_name or not alias_name:
            continue
        if source_name.startswith("response."):
            field_name = source_name.split(".", 1)[1]
            lines.append(f"        capture_data_{index} = {response_name}.json()")
            lines.append(f"        context[{alias_name!r}] = capture_data_{index}[{field_name!r}]")
    return lines
