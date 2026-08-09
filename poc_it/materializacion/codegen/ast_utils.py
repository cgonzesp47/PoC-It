from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Dict, List, Set


@dataclass(frozen=True)
class ResolvedCallArguments:
    provided_parameter_names: frozenset[str]
    arguments_by_parameter_name: dict[str, ast.AST]
    has_star_args: bool = False
    has_star_kwargs: bool = False


def contract_parameter_names(
    parameters: object,
) -> tuple[str, ...]:
    result: list[str] = []
    for parameter in parameters or []:
        if not isinstance(parameter, dict):
            continue
        name = str(parameter.get("name") or "").strip()
        if name:
            result.append(name)
    return tuple(result)


def required_contract_parameters(
    parameters: object,
) -> frozenset[str]:
    result: set[str] = set()
    for parameter in parameters or []:
        if not isinstance(parameter, dict):
            continue
        name = str(parameter.get("name") or "").strip()
        if not name:
            continue
        if parameter.get("required", True):
            result.add(name)
    return frozenset(result)


def resolve_call_arguments(
    call: ast.Call,
    *,
    parameter_names: list[str] | tuple[str, ...],
) -> ResolvedCallArguments:
    ordered_parameters = [
        str(name).strip()
        for name in parameter_names
        if str(name).strip()
    ]

    provided: set[str] = set()
    arguments_by_parameter_name: dict[str, ast.AST] = {}
    has_star_args = False
    has_star_kwargs = False
    positional_index = 0

    for argument in call.args:
        if isinstance(argument, ast.Starred):
            has_star_args = True
            continue
        if positional_index < len(ordered_parameters):
            parameter_name = ordered_parameters[positional_index]
            provided.add(parameter_name)
            arguments_by_parameter_name[parameter_name] = argument
        positional_index += 1

    for keyword in call.keywords:
        if keyword.arg is None:
            has_star_kwargs = True
            continue
        name = str(keyword.arg).strip()
        if name:
            provided.add(name)
            arguments_by_parameter_name[name] = keyword.value

    return ResolvedCallArguments(
        provided_parameter_names=frozenset(provided),
        arguments_by_parameter_name=arguments_by_parameter_name,
        has_star_args=has_star_args,
        has_star_kwargs=has_star_kwargs,
    )


def parse_python_module(content: str) -> ast.Module | None:
    try:
        return ast.parse(content or "")
    except SyntaxError:
        return None


def function_defs_by_name(
    tree: ast.Module,
) -> Dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    result: Dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name] = node
    return result


def imported_symbols(
    tree: ast.Module,
) -> Dict[str, tuple[str, str | None]]:
    result: Dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".")[0]
                result[local_name] = (alias.name, None)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local_name = alias.asname or alias.name
                result[local_name] = (module, alias.name)
    return result


def called_symbol_names(
    node: ast.AST,
) -> Set[str]:
    names: Set[str] = set()
    for current in ast.walk(node):
        if not isinstance(current, ast.Call):
            continue
        func = current.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def literal_strings(
    node: ast.AST,
) -> List[str]:
    strings: List[str] = []
    for current in ast.walk(node):
        if isinstance(current, ast.Constant) and isinstance(current.value, str):
            strings.append(current.value)
    return strings


def function_is_stub(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    meaningful_ops = 0

    for statement in function.body:
        if isinstance(statement, ast.Pass):
            continue

        if isinstance(statement, ast.Expr):
            value = statement.value
            if isinstance(value, ast.Constant) and value.value == Ellipsis:
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                continue

        if isinstance(statement, ast.Raise):
            exc = statement.exc
            if exc is None:
                meaningful_ops += 1
                continue
            if isinstance(exc, ast.Call):
                func = exc.func
                if isinstance(func, ast.Name) and func.id == "NotImplementedError":
                    continue
            if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                continue

        if isinstance(statement, ast.Return):
            value = statement.value
            if value is None:
                continue
            if isinstance(value, ast.Constant) and value.value is None:
                continue
            if isinstance(value, ast.Dict):
                keys = value.keys
                values = value.values
                if (
                    len(keys) == 1
                    and len(values) == 1
                    and isinstance(keys[0], ast.Constant)
                    and keys[0].value == "status"
                    and isinstance(values[0], ast.Constant)
                    and values[0].value == "success"
                ):
                    continue
            meaningful_ops += 1
            continue

        meaningful_ops += 1

    return meaningful_ops == 0
