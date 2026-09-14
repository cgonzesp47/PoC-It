from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class HarnessPlan:
    tests_style: str
    hermetic: bool
    allowed_overrides: List[str]
    env_vars_explicit: List[str]
    endpoints: List[dict]


def _normalize_tests_style(runtime_contracts: dict) -> str:
    style = str(runtime_contracts.get("tests_style") or "sync").lower().strip()
    return "async" if style == "async" else "sync"


def build_harness_plan(runtime_contracts: dict) -> HarnessPlan:
    endpoints = runtime_contracts.get("endpoints") or []
    if not isinstance(endpoints, list):
        endpoints = []

    allowed = runtime_contracts.get("allowed_dependency_overrides") or []
    if not isinstance(allowed, list):
        allowed = []
    allowed_overrides = [str(x).strip() for x in allowed if str(x).strip()]

    envs = runtime_contracts.get("env_vars_explicit") or []
    if not isinstance(envs, list):
        envs = []
    env_vars_explicit = [str(x).strip() for x in envs if str(x).strip()]

    hermetic = bool(runtime_contracts.get("hermetic", True))

    return HarnessPlan(
        tests_style=_normalize_tests_style(runtime_contracts),
        hermetic=hermetic,
        allowed_overrides=allowed_overrides,
        env_vars_explicit=env_vars_explicit,
        endpoints=[ep for ep in endpoints if isinstance(ep, dict)],
    )


def _dedup_keep_order(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for it in items:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def _collect_observed_calls_by_dependency(plan: HarnessPlan) -> Dict[str, List[dict]]:
    by_dep: Dict[str, List[dict]] = {}
    for ep in plan.endpoints:
        deps = ep.get("depends_imports") or []
        if not isinstance(deps, list):
            deps = []
        deps = [str(x).strip() for x in deps if str(x).strip()]

        calls = ep.get("observed_calls") or []
        if not isinstance(calls, list):
            calls = []
        calls = [c for c in calls if isinstance(c, dict) and str(c.get("method_name") or "").strip()]

        if not deps or not calls:
            continue

        for dep in deps:
            by_dep.setdefault(dep, []).extend(calls)

    for dep, calls in list(by_dep.items()):
        uniq = []
        seen = set()
        for c in calls:
            key = (
                str(c.get("method_name") or ""),
                bool(c.get("awaited")),
                tuple([str(x) for x in (c.get("arg_names") or [])]) if isinstance(c.get("arg_names"), list) else (),
            )
            if key in seen:
                continue
            seen.add(key)
            uniq.append(c)
        by_dep[dep] = uniq

    return by_dep


def _collect_response_contract_hints(plan: HarnessPlan) -> Dict[str, dict]:
    by_ep: Dict[str, dict] = {}
    for ep in plan.endpoints:
        method = str(ep.get("method") or "").upper().strip()
        path = str(ep.get("path") or "").strip()
        if not method or not path:
            continue

        shape = str(ep.get("response_json_shape") or "").lower().strip() or None
        keys = ep.get("response_json_required_keys") or []
        if not isinstance(keys, list):
            keys = []
        keys = [str(k).strip() for k in keys if str(k).strip()]
        by_ep[f"{method} {path}"] = {"shape": shape, "keys": keys}
    return by_ep


def render_conftest_py(runtime_contracts: dict, runtime_facts: Optional[dict] = None) -> str:
    plan = build_harness_plan(runtime_contracts)
    allowed = _dedup_keep_order(plan.allowed_overrides)
    observed_calls = _collect_observed_calls_by_dependency(plan)
    response_hints_by_ep = _collect_response_contract_hints(plan)

    calls_literal = {
        dep: [
            {
                "method_name": c.get("method_name"),
                "awaited": bool(c.get("awaited")),
                "arg_names": c.get("arg_names"),
            }
            for c in calls
        ]
        for dep, calls in observed_calls.items()
    }

    lines: List[str] = []
    lines.append("import importlib")
    lines.append("import os")
    lines.append("from unittest.mock import MagicMock")
    lines.append("")
    lines.append("import pytest")
    lines.append("")
    if plan.tests_style == "sync":
        lines.append("from fastapi.testclient import TestClient")
        lines.append("")
    else:
        lines.append("import httpx")
        lines.append("")

    lines.append("def _import_callable(fqn: str):")
    lines.append("    mod_name, attr = fqn.rsplit('.', 1)")
    lines.append("    mod = importlib.import_module(mod_name)")
    lines.append("    return getattr(mod, attr)")
    lines.append("")

    lines.append("def _ensure_env(monkeypatch):")
    if plan.env_vars_explicit:
        for v in plan.env_vars_explicit:
            lines.append(f"    monkeypatch.setenv({v!r}, os.getenv({v!r}, 'DUMMY'))")
    else:
        lines.append("    return")
    lines.append("")

    lines.append("def _allowed_methods_by_dependency():")
    lines.append(f"    calls = {calls_literal!r}")
    lines.append("    return {")
    lines.append("        dep: [")
    lines.append("            str(item.get('method_name') or '').strip()")
    lines.append("            for item in items")
    lines.append("            if str(item.get('method_name') or '').strip()")
    lines.append("        ]")
    lines.append("        for dep, items in calls.items()")
    lines.append("    }")
    lines.append("")

    lines.append("ALLOWED_DEPENDENCY_OVERRIDES = " + repr(allowed))
    lines.append("")

    lines.append("class GeneratedStrictDouble:")
    lines.append("    def __init__(self, methods):")
    lines.append("        self._allowed_methods = set(methods or [])")
    lines.append("        self._behaviors = {}")
    lines.append("        self.calls = []")
    lines.append("")
    lines.append("    def _validate_method(self, method_name):")
    lines.append("        if method_name not in self._allowed_methods:")
    lines.append("            raise AttributeError(f\"Method {method_name!r} not allowed in GeneratedStrictDouble\")")
    lines.append("")
    lines.append("    def configure_return(self, method_name, value):")
    lines.append("        self._validate_method(method_name)")
    lines.append("        self._behaviors[method_name] = ('return', value)")
    lines.append("")
    lines.append("    def configure_raise(self, method_name, error):")
    lines.append("        self._validate_method(method_name)")
    lines.append("        self._behaviors[method_name] = ('raise', error)")
    lines.append("")
    lines.append("    def configure_async_return(self, method_name, value):")
    lines.append("        self._validate_method(method_name)")
    lines.append("        self._behaviors[method_name] = ('async_return', value)")
    lines.append("")
    lines.append("    def configure_behavior(self, behavior):")
    lines.append("        if not isinstance(behavior, dict):")
    lines.append("            raise TypeError('behavior must be a dict')")
    lines.append("        method_name = str(behavior.get('method_name') or '').strip()")
    lines.append("        action = str(behavior.get('action') or '').strip()")
    lines.append("        if action == 'return':")
    lines.append("            self.configure_return(method_name, behavior.get('value'))")
    lines.append("            return")
    lines.append("        if action == 'async_return':")
    lines.append("            self.configure_async_return(method_name, behavior.get('value'))")
    lines.append("            return")
    lines.append("        if action == 'raise':")
    lines.append("            exc_type = behavior.get('exception_type') or 'RuntimeError'")
    lines.append("            exc_message = behavior.get('exception_message') or 'configured dependency failure'")
    lines.append("            exc_cls = getattr(__builtins__, str(exc_type), RuntimeError)")
    lines.append("            if not isinstance(exc_cls, type) or not issubclass(exc_cls, BaseException):")
    lines.append("                exc_cls = RuntimeError")
    lines.append("            self.configure_raise(method_name, exc_cls(str(exc_message)))")
    lines.append("            return")
    lines.append("        raise ValueError(f'Unsupported dependency behavior action: {action!r}')")
    lines.append("")
    lines.append("    def __getattr__(self, method_name):")
    lines.append("        self._validate_method(method_name)")
    lines.append("")
    lines.append("        async def _async_method(*args, **kwargs):")
    lines.append("            self.calls.append({'method_name': method_name, 'args': args, 'kwargs': kwargs})")
    lines.append("            if method_name not in self._behaviors:")
    lines.append("                raise AssertionError(f'No behavior configured for async method {method_name!r}')")
    lines.append("            action, payload = self._behaviors[method_name]")
    lines.append("            if action == 'raise':")
    lines.append("                raise payload")
    lines.append("            if action == 'async_return':")
    lines.append("                return payload")
    lines.append("            if action == 'return':")
    lines.append("                return payload")
    lines.append("            raise AssertionError(f'Unsupported action for async method {method_name!r}: {action!r}')")
    lines.append("")
    lines.append("        def _method(*args, **kwargs):")
    lines.append("            self.calls.append({'method_name': method_name, 'args': args, 'kwargs': kwargs})")
    lines.append("            if method_name not in self._behaviors:")
    lines.append("                raise AssertionError(f'No behavior configured for method {method_name!r}')")
    lines.append("            action, payload = self._behaviors[method_name]")
    lines.append("            if action == 'raise':")
    lines.append("                raise payload")
    lines.append("            if action in ('return', 'async_return'):")
    lines.append("                return payload")
    lines.append("            raise AssertionError(f'Unsupported action for method {method_name!r}: {action!r}')")
    lines.append("")
    lines.append("        return _async_method if method_name.startswith('async_') else _method")
    lines.append("")

    lines.append("@pytest.fixture")
    lines.append("def dependency_double_factory():")
    lines.append("    created = {}")
    lines.append("    methods_by_dependency = _allowed_methods_by_dependency()")
    lines.append("")
    lines.append("    def build(name, methods=None, behaviors=None):")
    lines.append("        merged_methods = list(dict.fromkeys(list(methods or []) + list(methods_by_dependency.get(name, []))))")
    lines.append("        double = GeneratedStrictDouble(merged_methods)")
    lines.append("        for behavior in behaviors or []:")
    lines.append("            double.configure_behavior(behavior)")
    lines.append("        created[name] = double")
    lines.append("        return double")
    lines.append("")
    lines.append("    build.created = created")
    lines.append("    return build")
    lines.append("")

    lines.append("@pytest.fixture")
    lines.append("def dependency_overrides_guard(app, dependency_double_factory):")
    lines.append("    created = {}")
    lines.append("")
    lines.append("    class DependencyOverridesGuard:")
    lines.append("        def get_double(self, dep_fqn, *, methods=None):")
    lines.append("            if dep_fqn not in created:")
    lines.append("                dep_callable = _import_callable(dep_fqn)")
    lines.append("                double = dependency_double_factory(dep_fqn, methods=methods or [], behaviors=[])")
    lines.append("")
    lines.append("                def _override_dep(_dep_double=double):")
    lines.append("                    return _dep_double")
    lines.append("")
    lines.append("                app.dependency_overrides[dep_callable] = _override_dep")
    lines.append("                created[dep_fqn] = double")
    lines.append("")
    lines.append("            return created[dep_fqn]")
    lines.append("")
    lines.append("        def bind_value(self, dep_fqn, value):")
    lines.append("            dep_callable = _import_callable(dep_fqn)")
    lines.append("")
    lines.append("            def _override_value(_value=value):")
    lines.append("                return _value")
    lines.append("")
    lines.append("            app.dependency_overrides[dep_callable] = _override_value")
    lines.append("            created[dep_fqn] = value")
    lines.append("            return value")
    lines.append("")
    lines.append("        def bind(self, dep_fqn, value):")
    lines.append("            return self.bind_value(dep_fqn, value)")
    lines.append("")
    lines.append("        def bind_auto_double(self, dep_fqn):")
    lines.append("            if dep_fqn not in created:")
    lines.append("                dep_callable = _import_callable(dep_fqn)")
    lines.append("                double = MagicMock(name=dep_fqn.rsplit('.', 1)[-1])")
    lines.append("")
    lines.append("                def _override_auto(_dep_double=double):")
    lines.append("                    return _dep_double")
    lines.append("")
    lines.append("                app.dependency_overrides[dep_callable] = _override_auto")
    lines.append("                created[dep_fqn] = double")
    lines.append("")
    lines.append("            return created[dep_fqn]")
    lines.append("")
    lines.append("        def bind_auto_double_raising(self, dep_fqn, exc):")
    lines.append("            if dep_fqn not in created:")
    lines.append("                dep_callable = _import_callable(dep_fqn)")
    lines.append("")
    lines.append("                class _RaisingDouble:")
    lines.append("                    def __getattr__(self, _name):")
    lines.append("                        def _raise(*_args, **_kwargs):")
    lines.append("                            raise exc")
    lines.append("                        return _raise")
    lines.append("")
    lines.append("                double = _RaisingDouble()")
    lines.append("")
    lines.append("                def _override_raising(_dep_double=double):")
    lines.append("                    return _dep_double")
    lines.append("")
    lines.append("                app.dependency_overrides[dep_callable] = _override_raising")
    lines.append("                created[dep_fqn] = double")
    lines.append("")
    lines.append("            return created[dep_fqn]")
    lines.append("")
    lines.append("    try:")
    lines.append("        yield DependencyOverridesGuard()")
    lines.append("    finally:")
    lines.append("        app.dependency_overrides.clear()")
    lines.append("")
    lines.append(f"RESPONSE_HINTS_BY_EP = {response_hints_by_ep!r}")
    lines.append("")

    if plan.tests_style == "sync":
        lines.append("@pytest.fixture")
        lines.append("def app():")
        lines.append("    app_main = importlib.import_module('app.main')")
        lines.append("    return getattr(app_main, 'app')")
        lines.append("")
        lines.append("@pytest.fixture")
        lines.append("def client(app, monkeypatch):")
        lines.append("    _ensure_env(monkeypatch)")
        lines.append("")
        lines.append("    with TestClient(")
        lines.append("        app,")
        lines.append("        raise_server_exceptions=False,")
        lines.append("    ) as client:")
        lines.append("        yield client")
        lines.append("")
    else:
        lines.append("@pytest.fixture")
        lines.append("def app():")
        lines.append("    app_main = importlib.import_module('app.main')")
        lines.append("    return getattr(app_main, 'app')")
        lines.append("")
        lines.append("@pytest.fixture")
        lines.append("async def client(app, monkeypatch):")
        lines.append("    _ensure_env(monkeypatch)")
        lines.append("    async with httpx.AsyncClient(app=app, base_url='http://test') as client:")
        lines.append("        yield client")
        lines.append("")

    return "\n".join(lines)


__all__ = [
    "HarnessPlan",
    "build_harness_plan",
    "render_conftest_py",
]
