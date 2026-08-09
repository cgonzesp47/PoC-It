from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from poc_it.testing.execution.test_doubles import DependencyBehavior, DependencyProtocol, MethodProtocol


@dataclass(frozen=True)
class RenderableDependency:
    fixture_name: str
    dependency_fqn: str
    import_fqn: str
    protocol: DependencyProtocol
    behaviors: List[DependencyBehavior] = field(default_factory=list)
    stateful: bool = False


def render_dependency_fixtures(
    *,
    dependencies: List[RenderableDependency],
) -> str:
    lines: List[str] = [
        "import importlib",
        "import pytest",
        "",
        "from poc_it.testing.execution.test_doubles import (",
        "    DependencyBehavior,",
        "    DependencyProtocol,",
        "    MethodProtocol,",
        "    StrictDouble,",
        ")",
        "",
        "",
        "def _import_callable(fqn: str):",
        "    module_name, attr_name = fqn.rsplit('.', 1)",
        "    module = importlib.import_module(module_name)",
        "    return getattr(module, attr_name)",
        "",
    ]

    for dep in dependencies:
        lines.extend(_render_dependency_fixture(dep))

    if not dependencies:
        lines.append("@pytest.fixture")
        lines.append("def dependency_overrides_registry():")
        lines.append("    return {}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_dependency_override_fixture(
    *,
    fixture_name: str = "dependency_overrides_registry",
    app_fixture_name: str = "app",
    dependencies: List[RenderableDependency],
) -> str:
    if not dependencies:
        return (
            "import pytest\n\n\n"
            f"@pytest.fixture\n"
            f"def {fixture_name}({app_fixture_name}):\n"
            "    try:\n"
            "        yield {}\n"
            "    finally:\n"
            f"        {app_fixture_name}.dependency_overrides.clear()\n"
        )

    params = ", ".join([app_fixture_name] + [dep.fixture_name for dep in dependencies])
    lines = [
        "import pytest",
        "",
        "",
        "@pytest.fixture",
        f"def {fixture_name}({params}):",
        "    _registry = {}",
        "    try:",
    ]

    for dep in dependencies:
        override_name = f"_{dep.fixture_name}_override"
        if _protocol_requires_async_override(dep.protocol):
            lines.append(f"        async def {override_name}(_instance={dep.fixture_name}):")
            lines.append("            return _instance")
        else:
            lines.append(f"        def {override_name}(_instance={dep.fixture_name}):")
            lines.append("            return _instance")
        lines.append(f"        _callable = _import_callable({dep.import_fqn!r})")
        lines.append(f"        {app_fixture_name}.dependency_overrides[_callable] = {override_name}")
        lines.append(f"        _registry[{dep.dependency_fqn!r}] = {dep.fixture_name}")

    lines.extend(
        [
            "        yield _registry",
            "    finally:",
            f"        {app_fixture_name}.dependency_overrides.clear()",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_dependency_fixture(dep: RenderableDependency) -> List[str]:
    protocol_literal = _render_protocol_literal(dep.protocol)
    behaviors_literal = _render_behaviors_literal(dep.behaviors)

    lines = ["@pytest.fixture"]
    lines.append(f"def {dep.fixture_name}():")
    if dep.stateful:
        lines.append("    _state = {}")
        lines.append(
            f"    return StrictDouble(protocol={protocol_literal}, behaviors={behaviors_literal}, state=_state)"
        )
    else:
        lines.append(f"    return StrictDouble(protocol={protocol_literal}, behaviors={behaviors_literal})")
    lines.append("")
    return lines


def _render_protocol_literal(protocol: DependencyProtocol) -> str:
    methods = ", ".join(
        [
            f"MethodProtocol(method_name={method.method_name!r}, is_async={method.is_async!r})"
            for method in protocol.methods
        ]
    )
    return (
        "DependencyProtocol("
        f"dependency_fqn={protocol.dependency_fqn!r}, "
        f"methods=[{methods}], "
        f"strict={protocol.strict!r}"
        ")"
    )


def _render_behaviors_literal(behaviors: List[DependencyBehavior]) -> str:
    rendered = ", ".join(
        [
            "DependencyBehavior("
            f"dependency_fqn={behavior.dependency_fqn!r}, "
            f"method_name={behavior.method_name!r}, "
            f"action={behavior.action!r}, "
            f"value={behavior.value!r}"
            ")"
            for behavior in behaviors
        ]
    )
    return f"[{rendered}]"


def _protocol_requires_async_override(protocol: DependencyProtocol) -> bool:
    return any(method.is_async for method in protocol.methods)
