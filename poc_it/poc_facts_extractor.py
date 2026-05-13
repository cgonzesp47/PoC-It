"""
PoC-it – Extractor determinista de "facts" desde el código generado (sin LLM).

Objetivo
--------
Generar un resumen estructurado (JSON/dict) del proyecto generado para:

- Alinear tests con el CÓDIGO real (evitar monkeypatch de símbolos inexistentes).
- Generar README_MANUAL coherente con stubs detectados y variables de entorno realmente usadas.
- Ser robusto frente a no-determinismo: el LLM puede generar distintos códigos para el mismo SPEC.

Este extractor NO intenta "entender" semántica completa; prioriza:
- Endpoints reales (FastAPI/APIRouter)
- Dependencias declaradas (Depends)
- Heurísticas de stubs (TODO/placeholder, dependencia inyectada sin uso, returns triviales)
- Variables de entorno explícitas (os.getenv)

Se recomienda ejecutarlo sobre `estructura_generada: Dict[path, content]` que ya tiene PoC-it
tras materializar los archivos, pero también soporta lectura desde disco si se desea.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple


# ----------------------------
# Modelos internos (facts)
# ----------------------------

@dataclass
class EndpointFact:
    path: str
    method: str
    func_name: str
    module_path: str  # path del fichero, p.ej. app/endpoints/upload.py
    request_model: Optional[str] = None  # nombre de clase Pydantic si se detecta
    depends: List[str] = None  # símbolos Depends(x)
    injected_params: List[str] = None  # nombres de params del handler que parecen DI
    uses_injected: bool = False  # si se usa alguna variable inyectada
    stub_signals: List[str] = None  # evidencias de stub

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["depends"] = d["depends"] or []
        d["injected_params"] = d["injected_params"] or []
        d["stub_signals"] = d["stub_signals"] or []
        return d


@dataclass
class StubFact:
    file: str
    symbol: str  # función o clase
    signals: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {"file": self.file, "symbol": self.symbol, "signals": list(self.signals)}


@dataclass
class POCFacts:
    endpoints: List[EndpointFact]
    env_vars_explicit: List[str]
    stubs: List[StubFact]
    imports: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "endpoints": [e.to_dict() for e in self.endpoints],
            "env_vars_explicit": sorted(set(self.env_vars_explicit)),
            "stubs": [s.to_dict() for s in self.stubs],
            "imports": sorted(set(self.imports)),
        }


# ----------------------------
# Helpers AST / parsing
# ----------------------------

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)  # type: ignore[attr-defined]
    except Exception:
        return ""


def _extract_env_vars_from_source(source: str) -> List[str]:
    """
    Heurística: detectar os.getenv("X") y os.environ.get("X").
    """
    envs: List[str] = []
    # Nota: no escapar el backslash en raw-string salvo que quieras un backslash literal.
    for m in re.finditer(r"os\.getenv\(\s*['\"]([A-Z0-9_]+)['\"]", source):
        envs.append(m.group(1))
    for m in re.finditer(r"os\.environ\.get\(\s*['\"]([A-Z0-9_]+)['\"]", source):
        envs.append(m.group(1))
    return envs


def _collect_imports(tree: ast.AST) -> List[str]:
    out: List[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out.append(a.name)
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            for a in n.names:
                if mod:
                    out.append(f"{mod}.{a.name}")
                else:
                    out.append(a.name)
    return out


def _is_depends_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name) and node.func.id == "Depends":
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr == "Depends":
        return True
    return False


def _depends_symbol_from_call(node: ast.Call) -> Optional[str]:
    if not node.args:
        return None
    arg0 = node.args[0]
    if isinstance(arg0, ast.Name):
        return arg0.id
    if isinstance(arg0, ast.Attribute):
        return _safe_unparse(arg0) or None
    return _safe_unparse(arg0) or None


def _router_decorator_info(dec: ast.AST) -> Optional[Tuple[str, str]]:
    """
    Devuelve (method, path) si el decorator es router.<method>("<path>").
    """
    if not isinstance(dec, ast.Call):
        return None
    if not isinstance(dec.func, ast.Attribute):
        return None
    method = dec.func.attr.lower()
    if method not in _HTTP_METHODS:
        return None
    # primer arg normalmente es path
    if not dec.args:
        return None
    if isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
        return method.upper(), dec.args[0].value
    return None


def _has_stub_comment(source: str) -> bool:
    # Heurística barata: TODO / Implement ... logic / placeholder / stub
    patterns = [
        r"\\bTODO\\b",
        r"\\bFIXME\\b",
        r"\\bplaceholder\\b",
        r"\\bstub\\b",
        r"Implement\\s+.*\\s+logic",
        r"not implemented",
    ]
    return any(re.search(p, source, flags=re.IGNORECASE) for p in patterns)


def _function_returns_trivial_dict(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for n in ast.walk(func):
        if isinstance(n, ast.Return):
            v = n.value
            if isinstance(v, ast.Dict) and len(v.keys) == 0:
                return True
    return False


def _function_uses_name(func: ast.AST, name: str) -> bool:
    for n in ast.walk(func):
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load):
            return True
    return False


def extract_poc_facts_from_structure(estructura_generada: Dict[str, str]) -> POCFacts:
    """
    Entrada: estructura_generada { "app/main.py": "...", ... }
    Salida: POCFacts con endpoints/env vars/stubs.

    Nota: solo analiza ficheros .py.
    """
    endpoints: List[EndpointFact] = []
    stubs: List[StubFact] = []
    env_vars: List[str] = []
    imports: List[str] = []

    for path, content in estructura_generada.items():
        norm_path = path.replace("\\\\", "/")
        if not norm_path.endswith(".py"):
            continue
        if not isinstance(content, str):
            continue

        env_vars.extend(_extract_env_vars_from_source(content))

        try:
            tree = ast.parse(content)
        except Exception:
            # No romper generación por un fichero inválido
            continue

        imports.extend(_collect_imports(tree))

        # detectar endpoints: funciones decoradas con router.*
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            decs = getattr(node, "decorator_list", []) or []
            for dec in decs:
                info = _router_decorator_info(dec)
                if not info:
                    continue
                method, route_path = info

                # detectar request model: primer arg tipado con BaseModel
                request_model: Optional[str] = None
                injected_params: List[str] = []
                depends_syms: List[str] = []

                # En AST, args.args contiene solo definiciones de argumentos.
                # Los valores por defecto están en node.args.defaults (alineados con los últimos N args).
                defaults = list(node.args.defaults or [])
                args_list = list(node.args.args or [])
                defaults_by_arg: Dict[str, ast.AST] = {}
                if defaults:
                    for a, d in zip(args_list[-len(defaults) :], defaults):
                        defaults_by_arg[a.arg] = d

                for arg in args_list:
                    # arg.annotation puede ser Name("UploadRequest")
                    if arg.annotation and request_model is None:
                        if isinstance(arg.annotation, ast.Name):
                            # muy heurístico: el primer arg tipado suele ser request model
                            if arg.arg in ("request", "body", "payload"):
                                request_model = arg.annotation.id

                    default_node = defaults_by_arg.get(arg.arg)
                    if default_node and _is_depends_call(default_node):
                        injected_params.append(arg.arg)
                        sym = _depends_symbol_from_call(default_node)  # type: ignore[arg-type]
                        if sym:
                            depends_syms.append(sym)

                # ¿usa inyectados?
                uses_injected = any(_function_uses_name(node, p) for p in injected_params)

                stub_signals: List[str] = []
                if injected_params and not uses_injected:
                    stub_signals.append("dependency_injected_but_unused")
                if _function_returns_trivial_dict(node):
                    stub_signals.append("returns_empty_dict")
                if _has_stub_comment(content):
                    stub_signals.append("stub_comment_detected")

                ep = EndpointFact(
                    path=route_path,
                    method=method,
                    func_name=node.name,
                    module_path=norm_path,
                    request_model=request_model,
                    depends=depends_syms,
                    injected_params=injected_params,
                    uses_injected=uses_injected,
                    stub_signals=stub_signals,
                )
                endpoints.append(ep)

                if stub_signals:
                    stubs.append(StubFact(file=norm_path, symbol=node.name, signals=stub_signals))

    return POCFacts(endpoints=endpoints, env_vars_explicit=env_vars, stubs=stubs, imports=imports)
