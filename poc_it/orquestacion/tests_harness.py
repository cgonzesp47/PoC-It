from __future__ import annotations

"""
Harness determinista de tests (conftest.py) basado en runtime_contracts.

Problemas reales observados (tu PoC301_4):
- El LLM genera tests async (`@pytest.mark.asyncio` + `await client.get/post/...`) aunque
  runtime_contracts.tests_style="sync" y el fixture usa TestClient (sync) => TypeError.
- El sanitizer/repair puede comentar líneas de dependency_overrides, rompiendo el harness.
- Aunque se overridee get_db, si el override no se aplica (porque fue comentado), se ejecuta
  SQLAlchemy real y falla (UnboundExecutionError).

Objetivo:
- Generar SIEMPRE un conftest.py *determinista* y robusto:
  - fixture client sync o async
  - seteo best-effort de env vars explícitas
  - dependency_overrides con callables reales
  - stubs de deps según runtime_contracts.allowed_dependency_overrides
  - hace los overrides en *dos formas*:
      app.dependency_overrides[callable] = override
      y fallback por import-paths para cubrir sanitizers que mutan AST.

Este módulo SOLO renderiza conftest.py. Los tests deben alinearse en el generador/repair
para respetar tests_style (se corrige en pytest_fixers/tests_sanitizer, fuera de este módulo).
"""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional
import re


@dataclass(frozen=True)
class HarnessPlan:
    tests_style: str  # "sync" | "async"
    hermetic: bool
    allowed_overrides: List[str]  # FQNs
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
    """
    Map: dep_fqn -> list of call dicts (method_name, arg_names, awaited, receiver_param)
    Heurística agnóstica: asigna calls a deps get_*service si existen; si no, a deps no-db.
    """
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

        service_deps = [d for d in deps if re.search(r"\.get_.*service$", d)]
        non_db_deps = [d for d in deps if not d.endswith(".get_db")]

        target_deps = service_deps or non_db_deps or deps
        for d in target_deps:
            by_dep.setdefault(d, []).extend(calls)

    # De-dup por firma
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


def _collect_response_required_fields(plan: HarnessPlan) -> List[str]:
    fields: List[str] = []
    for ep in plan.endpoints:
        fr = ep.get("response_model_required_fields") or []
        if isinstance(fr, list):
            for f in fr:
                s = str(f).strip()
                if s and s not in fields:
                    fields.append(s)
    return fields


def _collect_response_contract_hints(plan: HarnessPlan) -> Dict[str, dict]:
    """Best-effort response schema hints per (METHOD PATH).

    runtime_probe añade:
      - response_json_shape
      - response_json_required_keys
    Usado por los tests para evitar asserts inventados.
    """
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


def _render_expected_response_asserts(*, method: str, path: str, hints_by_ep: Dict[str, dict]) -> List[str]:
    key = f"{method.upper()} {path}"
    h = hints_by_ep.get(key) or {}
    shape = str(h.get("shape") or "").lower().strip()
    keys = h.get("keys") or []
    if not isinstance(keys, list):
        keys = []
    keys = [str(k) for k in keys if str(k).strip()]

    if shape == "object" and keys:
        out = ["    data = response.json()", "    assert isinstance(data, dict)"]
        for k in keys[:8]:
            out.append(f"    assert {k!r} in data")
        return out

    if shape == "object":
        return ["    assert isinstance(response.json(), dict)"]

    if shape == "array":
        return ["    assert isinstance(response.json(), list)"]

    if shape == "string":
        return ["    assert isinstance(response.json(), str)"]

    if shape == "number":
        return ["    assert isinstance(response.json(), (int, float))"]

    if shape == "boolean":
        return ["    assert isinstance(response.json(), bool)"]

    return []


def render_conftest_py(runtime_contracts: dict, runtime_facts: Optional[dict] = None) -> str:
    plan = build_harness_plan(runtime_contracts)

    allowed = _dedup_keep_order(plan.allowed_overrides)
    observed_calls = _collect_observed_calls_by_dependency(plan)
    resp_required_fields = _collect_response_required_fields(plan)
    resp_hints_by_ep = _collect_response_contract_hints(plan)

    db_deps = [fqn for fqn in allowed if fqn.endswith(".get_db")]
    service_deps = [fqn for fqn in allowed if re.search(r"\.get_.*service$", fqn)]
    other_deps = [fqn for fqn in allowed if fqn not in db_deps and fqn not in service_deps]

    # ------------------------------------------------------------
    # PARCIAL: detectar uso directo de app.crud.* desde endpoints
    # ------------------------------------------------------------
    # runtime_contracts.endpoints[*].observed_calls contiene dicts con:
    # - receiver_param: nombre local (p.ej. "crud")
    # - method_name: nombre (p.ej. "create_product")
    #
    # Si detectamos receiver_param="crud" o method_name que empieza con "crud.",
    # generaremos monkeypatches en conftest para aislar persistencia.
    crud_calls: List[str] = []
    try:
        for ep in plan.endpoints:
            calls = ep.get("observed_calls") or []
            if not isinstance(calls, list):
                continue
            for c in calls:
                if not isinstance(c, dict):
                    continue
                receiver = str(c.get("receiver_param") or "").strip().lower()
                m = str(c.get("method_name") or "").strip()
                if not m:
                    continue
                if receiver == "crud":
                    crud_calls.append(m)
                elif m.startswith("crud."):
                    crud_calls.append(m.split(".", 1)[1])
    except Exception:
        crud_calls = []

    crud_calls = _dedup_keep_order([str(x).strip() for x in crud_calls if str(x).strip()])

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
    lines.append("# Helpers herméticos (PARCIAL)")
    lines.append("def _product_dict(**overrides):")
    lines.append("    data = {")
    lines.append("        'id': 1,")
    lines.append("        'nombre': 'Producto de prueba',")
    lines.append("        'descripcion': 'Descripción de prueba',")
    lines.append("        'precio': 10.0,")
    lines.append("        'disponible': True,")
    lines.append("    }")
    lines.append("    data.update(overrides)")
    lines.append("    return data")
    lines.append("")
    lines.append("def _model_to_dict(obj):")
    lines.append("    if obj is None:")
    lines.append("        return {}")
    lines.append("    if hasattr(obj, 'model_dump'):")
    lines.append("        return obj.model_dump()")
    lines.append("    if isinstance(obj, dict):")
    lines.append("        return dict(obj)")
    lines.append("    # best-effort: attrs")
    lines.append("    out = {}")
    lines.append("    for k in ('nombre', 'descripcion', 'precio', 'disponible'):")
    lines.append("        if hasattr(obj, k):")
    lines.append("            out[k] = getattr(obj, k)")
    lines.append("    return out")
    lines.append("")
    lines.append("class FakeAsyncSession:")
    lines.append("    \"\"\"DB fake mínimo: evita I/O y soporta `await db.*` usado por SQLAlchemy async.\"\"\"")
    lines.append("    def __init__(self):")
    lines.append("        self._store = {}")
    lines.append("")
    lines.append("    def add(self, obj):")
    lines.append("        # si viene con id, lo persistimos")
    lines.append("        try:")
    lines.append("            _id = getattr(obj, 'id', None)")
    lines.append("            if _id is not None:")
    lines.append("                self._store[int(_id)] = obj")
    lines.append("        except Exception:")
    lines.append("            pass")
    lines.append("")
    lines.append("    async def commit(self):")
    lines.append("        return None")
    lines.append("")
    lines.append("    async def refresh(self, obj):")
    lines.append("        # En muchos ORMs, refresh no asigna id si no hay flush real; dejamos id=1 si falta")
    lines.append("        try:")
    lines.append("            if getattr(obj, 'id', None) is None:")
    lines.append("                setattr(obj, 'id', 1)")
    lines.append("        except Exception:")
    lines.append("            pass")
    lines.append("        return None")
    lines.append("")
    lines.append("    async def delete(self, obj):")
    lines.append("        try:")
    lines.append("            _id = getattr(obj, 'id', None)")
    lines.append("            if _id is not None and int(_id) in self._store:")
    lines.append("                del self._store[int(_id)]")
    lines.append("        except Exception:")
    lines.append("            pass")
    lines.append("        return None")
    lines.append("")
    lines.append("    async def execute(self, stmt):")
    lines.append("        # No intentamos emular SQL; devolvemos un result compatible con `.scalars().first()`")
    lines.append("        class _Scalar:")
    lines.append("            def __init__(self, v):")
    lines.append("                self._v = v")
    lines.append("            def first(self):")
    lines.append("                return self._v")
    lines.append("        class _Result:")
    lines.append("            def __init__(self, v):")
    lines.append("                self._v = v")
    lines.append("            def scalars(self):")
    lines.append("                return _Scalar(self._v)")
    lines.append("            @property")
    lines.append("            def rowcount(self):")
    lines.append("                return 1 if self._v is not None else 0")
    lines.append("        return _Result(None)")
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

    lines.append("def _build_service_stub(dep_fqn: str):")
    lines.append("    svc = MagicMock()")
    lines.append(f"    calls = {calls_literal!r}")
    lines.append(f"    req_fields = {resp_required_fields!r}")
    lines.append("")
    lines.append("    def _dummy_response():")
    lines.append("        out = {}")
    lines.append("        for f in req_fields:")
    lines.append("            if f == 'id':")
    lines.append("                out.setdefault('id', 1)")
    lines.append("            elif f in ('name', 'nombre', 'title', 'titulo', 'descripcion', 'description'):")
    lines.append("                out.setdefault(f, 'DUMMY')")
    lines.append("            elif f in ('available', 'disponible', 'enabled', 'activo'):")
    lines.append("                out.setdefault(f, True)")
    lines.append("            elif f in ('price', 'precio', 'amount', 'importe', 'total', 'count', 'cantidad'):")
    lines.append("                out.setdefault(f, 0.0)")
    lines.append("            else:")
    lines.append("                # Fallback: evita None en campos requeridos típicos")
    lines.append("                out.setdefault(f, 'DUMMY')")
    lines.append("        return out")
    lines.append("")
    lines.append("    for c in calls.get(dep_fqn, []):")
    lines.append("        m = str(c.get('method_name') or '').strip()")
    lines.append("        if not m:")
    lines.append("            continue")
    lines.append("        awaited = bool(c.get('awaited'))")
    lines.append("        if awaited:")
    lines.append("            async def _fn(*args, **kwargs):")
    lines.append("                return _dummy_response()")
    lines.append("            setattr(svc, m, _fn)")
    lines.append("        else:")
    lines.append("            def _fn(*args, **kwargs):")
    lines.append("                return _dummy_response()")
    lines.append("            setattr(svc, m, _fn)")
    lines.append("    return svc")
    lines.append("")

    # Exportar hints de response para que los tests (LLM o repair) puedan usarlo determinísticamente
    lines.append(f"RESPONSE_HINTS_BY_EP = {resp_hints_by_ep!r}")
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
        lines.append("    # Dependency overrides (keys must be callables)")
        for dep in db_deps:
            lines.append(f"    dep_callable = _import_callable({dep!r})")
            lines.append("    # DB override: nunca conectar a DB real en modo hermético")
            lines.append("    async def _override_get_db():")
            lines.append("        yield FakeAsyncSession()")
            lines.append("    app.dependency_overrides[dep_callable] = _override_get_db")
            lines.append("")
        # Monkeypatch de app.crud.* si detectamos llamadas directas (receiver_param='crud')")
        lines.append(f"    _crud_calls = {crud_calls!r}")
        lines.append("    if _crud_calls:")
        lines.append("        try:")
        lines.append("            crud_mod = importlib.import_module('app.crud')")
        lines.append("            for name in _crud_calls:")
        lines.append("                if not hasattr(crud_mod, name):")
        lines.append("                    continue")
        lines.append("                if name.startswith('create_'):")
        lines.append("                    async def _fn(db, obj, _name=name):")
        lines.append("                        data = _model_to_dict(obj)")
        lines.append("                        return _product_dict(**data, id=1)")
        lines.append("                elif name.startswith('get_'):")
        lines.append("                    async def _fn(db, id, _name=name):")
        lines.append("                        return _product_dict(id=int(id))")
        lines.append("                elif name.startswith('update_'):")
        lines.append("                    async def _fn(db, id, obj, _name=name):")
        lines.append("                        data = _model_to_dict(obj)")
        lines.append("                        return _product_dict(**data, id=int(id))")
        lines.append("                elif name.startswith('delete_'):")
        lines.append("                    async def _fn(db, id, _name=name):")
        lines.append("                        return True")
        lines.append("                else:")
        lines.append("                    async def _fn(*args, **kwargs):")
        lines.append("                        return _product_dict()")
        lines.append("                monkeypatch.setattr(crud_mod, name, _fn, raising=True)")
        lines.append("        except Exception:")
        lines.append("            pass")
        lines.append("")
        for dep in service_deps:
            lines.append(f"    dep_callable = _import_callable({dep!r})")
            lines.append(f"    svc = _build_service_stub({dep!r})")
            lines.append("    def _override_service():")
            lines.append("        return svc")
            lines.append("    app.dependency_overrides[dep_callable] = _override_service")
            lines.append("")
        for dep in other_deps:
            lines.append(f"    dep_callable = _import_callable({dep!r})")
            lines.append("    def _override_dep():")
            lines.append("        return MagicMock()")
            lines.append("    app.dependency_overrides[dep_callable] = _override_dep")
            lines.append("")
        lines.append("    with TestClient(app, raise_server_exceptions=False) as c:")
        lines.append("        yield c")
        lines.append("    app.dependency_overrides.clear()")
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
        lines.append("")
        lines.append("    # Dependency overrides (keys must be callables)")
        for dep in db_deps:
            lines.append(f"    dep_callable = _import_callable({dep!r})")
            lines.append("    # DB override: nunca conectar a DB real en modo hermético")
            lines.append("    async def _override_get_db():")
            lines.append("        yield FakeAsyncSession()")
            lines.append("    app.dependency_overrides[dep_callable] = _override_get_db")
            lines.append("")
        for dep in service_deps:
            lines.append(f"    dep_callable = _import_callable({dep!r})")
            lines.append(f"    svc = _build_service_stub({dep!r})")
            lines.append("    def _override_service():")
            lines.append("        return svc")
            lines.append("    app.dependency_overrides[dep_callable] = _override_service")
            lines.append("")
        for dep in other_deps:
            lines.append(f"    dep_callable = _import_callable({dep!r})")
            lines.append("    def _override_dep():")
            lines.append("        return MagicMock()")
            lines.append("    app.dependency_overrides[dep_callable] = _override_dep")
            lines.append("")
        lines.append("    async with httpx.AsyncClient(app=app, base_url='http://test') as c:")
        lines.append("        yield c")
        lines.append("    app.dependency_overrides.clear()")
        lines.append("")

    lines.append("")
    return "\n".join(lines)
