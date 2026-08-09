from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class DependencyUsage:
    endpoint_id: str
    endpoint_path: str
    endpoint_method: str
    parameter_name: str
    callable_name: str
    callable_fqn: Optional[str]
    lifecycle: str
    return_annotation: Optional[str]
    methods_used: List[str] = field(default_factory=list)
    classification: str = "UNKNOWN"
    instantiated_outside_depends: List[str] = field(default_factory=list)
    import_side_effects: List[str] = field(default_factory=list)
    lifespan_side_effects: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class EndpointDependencyReport:
    endpoint_id: str
    endpoint_path: str
    endpoint_method: str
    dependencies: List[DependencyUsage] = field(default_factory=list)
    uncontrolled_external_dependencies: List[str] = field(default_factory=list)
    import_side_effects: List[str] = field(default_factory=list)
    lifespan_side_effects: List[str] = field(default_factory=list)


def inspect_fastapi_dependencies(
    *,
    routes: List[dict],
    source_by_module: Optional[Dict[str, str]] = None,
    allowed_dependency_overrides: Optional[List[str]] = None,
) -> List[EndpointDependencyReport]:
    allowed = {str(x).strip() for x in (allowed_dependency_overrides or []) if str(x).strip()}
    source_by_module = source_by_module or {}

    reports: List[EndpointDependencyReport] = []
    for route in routes:
        endpoint = route.get("endpoint")
        if endpoint is None:
            continue

        endpoint_id = str(route.get("endpoint_id") or getattr(endpoint, "__name__", "endpoint"))
        endpoint_path = str(route.get("path") or "")
        endpoint_method = str(route.get("method") or "GET").upper()
        endpoint_module = str(getattr(endpoint, "__module__", "") or "")
        endpoint_source = source_by_module.get(endpoint_module)
        endpoint_ast = _parse_source(endpoint_source)

        dependency_calls = route.get("dependency_calls") or []
        dependencies: List[DependencyUsage] = []

        for dep in dependency_calls:
            dependency_callable = dep.get("call")
            if dependency_callable is None:
                continue

            callable_fqn = _callable_fqn(dependency_callable)
            lifecycle = _lifecycle_of_callable(dependency_callable)
            return_annotation = _return_annotation_of_callable(dependency_callable)
            methods_used = _methods_used_for_parameter(
                endpoint,
                parameter_name=str(dep.get("parameter_name") or ""),
                tree=endpoint_ast,
            )
            classification = _classify_dependency(
                dependency_callable=dependency_callable,
                callable_fqn=callable_fqn,
                allowed_dependency_overrides=allowed,
                methods_used=methods_used,
            )

            dep_module = str(getattr(dependency_callable, "__module__", "") or "")
            dep_source = source_by_module.get(dep_module)
            dep_ast = _parse_source(dep_source)

            dependencies.append(
                DependencyUsage(
                    endpoint_id=endpoint_id,
                    endpoint_path=endpoint_path,
                    endpoint_method=endpoint_method,
                    parameter_name=str(dep.get("parameter_name") or ""),
                    callable_name=str(getattr(dependency_callable, "__name__", "dependency")),
                    callable_fqn=callable_fqn,
                    lifecycle=lifecycle,
                    return_annotation=return_annotation,
                    methods_used=methods_used,
                    classification=classification,
                    instantiated_outside_depends=_detect_instantiated_outside_depends(endpoint_ast, methods_used),
                    import_side_effects=_detect_import_side_effects(dep_ast),
                    lifespan_side_effects=_detect_lifespan_side_effects(endpoint_ast),
                )
            )

        reports.append(
            EndpointDependencyReport(
                endpoint_id=endpoint_id,
                endpoint_path=endpoint_path,
                endpoint_method=endpoint_method,
                dependencies=dependencies,
                uncontrolled_external_dependencies=sorted(
                    {
                        item
                        for dep in dependencies
                        if dep.classification == "UNCONTROLLED_EXTERNAL_DEPENDENCY"
                        for item in dep.instantiated_outside_depends
                    }
                ),
                import_side_effects=sorted({item for dep in dependencies for item in dep.import_side_effects}),
                lifespan_side_effects=sorted({item for dep in dependencies for item in dep.lifespan_side_effects}),
            )
        )

    return reports


def _parse_source(source: Optional[str]) -> Optional[ast.AST]:
    if not source:
        return None
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _callable_fqn(fn: Any) -> Optional[str]:
    module = str(getattr(fn, "__module__", "") or "").strip()
    qualname = str(getattr(fn, "__qualname__", "") or getattr(fn, "__name__", "") or "").strip()
    if not module or not qualname:
        return None
    return f"{module}.{qualname}"


def _return_annotation_of_callable(fn: Any) -> Optional[str]:
    try:
        ann = inspect.signature(fn).return_annotation
    except (TypeError, ValueError):
        return None
    if ann is inspect.Signature.empty:
        return None
    if isinstance(ann, str):
        return ann
    return getattr(ann, "__name__", repr(ann))


def _lifecycle_of_callable(fn: Any) -> str:
    if inspect.isasyncgenfunction(fn):
        return "async_generator"
    if inspect.isgeneratorfunction(fn):
        return "sync_generator"
    if inspect.iscoroutinefunction(fn):
        return "async_value"
    return "value"


def _methods_used_for_parameter(endpoint: Any, parameter_name: str, tree: Optional[ast.AST]) -> List[str]:
    if not parameter_name or tree is None:
        return []
    collector = _AttributeCollector(target_name=parameter_name)
    collector.visit(tree)
    return sorted(collector.names)


def _classify_dependency(
    *,
    dependency_callable: Any,
    callable_fqn: Optional[str],
    allowed_dependency_overrides: set[str],
    methods_used: List[str],
) -> str:
    module = str(getattr(dependency_callable, "__module__", "") or "")
    if callable_fqn and callable_fqn in allowed_dependency_overrides:
        return "OVERRIDEABLE_DEPENDENCY"
    if module.startswith("app.") or module.startswith("poc_it."):
        return "PURE_INTERNAL_DEPENDENCY" if not methods_used else "PATCHABLE_SYMBOL"
    if callable_fqn:
        return "PATCHABLE_SYMBOL"
    return "UNKNOWN"


def _detect_instantiated_outside_depends(tree: Optional[ast.AST], methods_used: List[str]) -> List[str]:
    if tree is None:
        return []
    detector = _InstantiationCollector()
    detector.visit(tree)
    results: List[str] = []
    for call_name in detector.calls:
        lowered = call_name.lower()
        if call_name in methods_used:
            continue
        if any(token in lowered for token in ("client", "session", "engine", "service", "repository", "connector")):
            results.append(call_name)
    return sorted(set(results))


def _detect_import_side_effects(tree: Optional[ast.AST]) -> List[str]:
    if tree is None:
        return []
    side_effects: List[str] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            side_effects.append(_call_name(node.value.func))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            side_effects.append(_call_name(node.value.func))
    return sorted({name for name in side_effects if name})


def _detect_lifespan_side_effects(tree: Optional[ast.AST]) -> List[str]:
    if tree is None:
        return []
    effects: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "lifespan":
            collector = _InstantiationCollector()
            collector.visit(node)
            effects.extend(collector.calls)
    return sorted({name for name in effects if name})


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


class _AttributeCollector(ast.NodeVisitor):
    def __init__(self, target_name: str) -> None:
        self.target_name = target_name
        self.names: set[str] = set()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and node.value.id == self.target_name:
            self.names.add(node.attr)
        self.generic_visit(node)


class _InstantiationCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: List[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name:
            self.calls.append(name)
        self.generic_visit(node)
