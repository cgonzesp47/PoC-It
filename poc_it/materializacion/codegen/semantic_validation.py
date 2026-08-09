from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Dict, List, Set

from poc_it.generador.file_contracts_validation import (
    SensitiveLiteralAssignment,
    ValueShape,
    _extract_literal_assignments,
    _literal_string_value,
    _parameter_expected_type,
    _resolve_expression_shape,
    _secret_configuration_keys,
    _shape_from_return_spec,
    _shapes_are_compatible,
)

from .ast_utils import (
    called_symbol_names,
    contract_parameter_names,
    function_defs_by_name,
    function_is_stub,
    imported_symbols,
    parse_python_module,
    required_contract_parameters,
    resolve_call_arguments,
)
from .models import CodegenIssue, GeneratedFile


@dataclass(frozen=True)
class RouteDeclaration:
    method: str
    path: str
    function_name: str


@dataclass(frozen=True)
class ConfigurationFieldAccess:
    object_name: str
    field_name: str
    line: int | None


def validate_generated_file_semantics(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
) -> List[CodegenIssue]:
    kind = str(file_contract.get("kind") or "").strip()
    validators = {
        "endpoint": _validate_endpoint,
        "integration": _validate_integration,
        "config": _validate_config,
        "requirements": _validate_requirements,
        "service": _validate_service,
        "repository": _validate_repository,
        "test": _validate_test,
    }
    validator = validators.get(kind)
    if validator is None:
        return []
    return validator(generated_file=generated_file, file_contract=file_contract)


def _issue(
    code: str,
    generated_file: GeneratedFile,
    message: str,
    *,
    severity: str = "error",
    action_ref: str | None = None,
    symbol: str | None = None,
    details: Dict[str, Any] | None = None,
) -> CodegenIssue:
    return CodegenIssue(
        code=code,
        path=generated_file.path,
        message=message,
        severity=severity,
        action_ref=action_ref,
        symbol=symbol,
        details=dict(details or {}),
    )


def _python_tree(generated_file: GeneratedFile) -> ast.Module | None:
    if not generated_file.path.endswith(".py"):
        return None
    return parse_python_module(generated_file.content)


def _extract_attribute_accesses(source: str) -> list[ConfigurationFieldAccess]:
    tree = ast.parse(source)
    result: list[ConfigurationFieldAccess] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        result.append(
            ConfigurationFieldAccess(
                object_name=node.value.id,
                field_name=node.attr,
                line=getattr(node, "lineno", None),
            )
        )

    return result


def _extract_provider_bindings(
    source: str,
    *,
    provider_symbol: str,
) -> set[str]:
    tree = ast.parse(source)
    bindings: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == provider_symbol:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bindings.add(target.id)
        if isinstance(node, ast.AnnAssign):
            if not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            if (
                isinstance(func, ast.Name)
                and func.id == provider_symbol
                and isinstance(node.target, ast.Name)
            ):
                bindings.add(node.target.id)

    return bindings


def _validate_configuration_access(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
) -> List[CodegenIssue]:
    configuration_access = file_contract.get("configuration_access") or {}
    provider_symbol = str(configuration_access.get("provider_symbol") or "").strip()
    if not provider_symbol:
        return []

    allowed_fields = {
        str(field).strip()
        for field in (configuration_access.get("allowed_fields") or [])
        if str(field).strip()
    }
    config_bindings = _extract_provider_bindings(
        generated_file.content,
        provider_symbol=provider_symbol,
    )
    if not config_bindings:
        return []

    issues: List[CodegenIssue] = []
    for access in _extract_attribute_accesses(generated_file.content):
        if access.object_name not in config_bindings:
            continue
        if access.field_name not in allowed_fields:
            issues.append(
                _issue(
                    "CONFIGURATION_FIELD_MISSING",
                    generated_file,
                    (
                        "Generated code references configuration field "
                        f"{access.field_name!r} not allowed by FileContract."
                    ),
                    details={
                        "field": access.field_name,
                        "allowed_fields": sorted(allowed_fields),
                    },
                )
            )

    return issues


def _build_local_value_shapes(
    tree: ast.Module,
    file_contract: Dict[str, Any],
) -> dict[str, ValueShape]:
    provider_shapes: dict[tuple[str, str], ValueShape] = {}
    for item in file_contract.get("required_internal_calls") or []:
        if not isinstance(item, dict):
            continue
        module = str(item.get("module") or "").strip()
        symbol = str(item.get("symbol") or "").strip()
        if not module or not symbol:
            continue
        provider_shapes[(module, symbol)] = _shape_from_return_spec(item.get("returns"))

    bindings: dict[str, ValueShape] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            shape = ValueShape(kind="unknown")
            if isinstance(func, ast.Name):
                for module_name, symbol_name in provider_shapes:
                    if symbol_name == func.id:
                        shape = provider_shapes[(module_name, symbol_name)]
                        break
            elif isinstance(func, ast.Attribute):
                for module_name, symbol_name in provider_shapes:
                    if symbol_name == func.attr:
                        shape = provider_shapes[(module_name, symbol_name)]
                        break
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = shape
    return bindings


def _validate_required_internal_call_data_flow(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
    tree: ast.Module,
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    required_calls = file_contract.get("required_internal_calls") or []
    bindings = _build_local_value_shapes(tree, file_contract)

    for required_call in required_calls:
        if not isinstance(required_call, dict):
            continue
        symbol_name = str(required_call.get("symbol") or "").strip()
        if not symbol_name:
            continue

        interface_parameters = [
            parameter
            for parameter in (required_call.get("parameters") or [])
            if isinstance(parameter, dict)
        ]
        parameter_names = contract_parameter_names(interface_parameters)
        required_parameters = required_contract_parameters(interface_parameters)
        expected_types = {
            str(parameter.get("name") or "").strip(): _parameter_expected_type(
                parameter,
                data_contracts=dict(file_contract.get("data_contracts") or {}),
            )
            for parameter in interface_parameters
            if str(parameter.get("name") or "").strip()
        }
        if not required_parameters and not any(expected_types.values()):
            continue

        matching_calls: List[ast.Call] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == symbol_name:
                matching_calls.append(node)
            elif isinstance(func, ast.Attribute) and func.attr == symbol_name:
                matching_calls.append(node)

        if not matching_calls:
            continue

        for call in matching_calls:
            resolved_arguments = resolve_call_arguments(
                call,
                parameter_names=parameter_names,
            )
            provided_arguments = set(resolved_arguments.provided_parameter_names)
            missing = required_parameters - provided_arguments
            if missing and not (
                resolved_arguments.has_star_args or resolved_arguments.has_star_kwargs
            ):
                issues.append(
                    _issue(
                        "FILE_CONTRACT_DATA_FLOW_MISMATCH",
                        generated_file,
                        (
                            f"The call to {symbol_name} omits required data: "
                            f"{sorted(missing)}."
                        ),
                        symbol=symbol_name,
                    )
                )
                break

            for parameter_name, argument_node in resolved_arguments.arguments_by_parameter_name.items():
                expected_type = expected_types.get(parameter_name)
                if not expected_type:
                    continue
                actual_shape = _resolve_expression_shape(
                    argument_node,
                    bindings=bindings,
                    provider_shapes={},
                    symbol_imports={},
                )
                compatibility = _shapes_are_compatible(actual_shape, expected_type)
                if compatibility is False:
                    issues.append(
                        _issue(
                            "FILE_CONTRACT_DATA_FLOW_TYPE_MISMATCH",
                            generated_file,
                            (
                                f"The call to {symbol_name} provides {actual_shape.kind} "
                                f"for parameter {parameter_name}, expected {expected_type}."
                            ),
                            symbol=symbol_name,
                        )
                    )
                    break

    return issues


def _validate_provided_interfaces(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
    tree: ast.Module | None,
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    if tree is None:
        return issues

    functions = function_defs_by_name(tree)
    provided_interfaces = file_contract.get("provided_interfaces") or []
    for item in provided_interfaces:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        kind = str(item.get("kind") or "").strip()
        action_ref = str(item.get("action_ref") or "").strip() or None
        interface_required = bool(item.get("interface_required"))
        if not symbol:
            continue

        function_node = functions.get(symbol)
        if function_node is None:
            issues.append(
                _issue(
                    "CODE_REQUIRED_INTERFACE_MISSING",
                    generated_file,
                    f"No existe la interfaz requerida {symbol}.",
                    action_ref=action_ref,
                    symbol=symbol,
                )
            )
            continue

        if kind == "function" and not isinstance(
            function_node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            issues.append(
                _issue(
                    "CODE_REQUIRED_INTERFACE_WRONG_KIND",
                    generated_file,
                    f"El símbolo {symbol} no es una función.",
                    action_ref=action_ref,
                    symbol=symbol,
                )
            )

        required_params = [
            str(param.get("name") or "").strip()
            for param in (item.get("parameters") or [])
            if isinstance(param, dict) and param.get("required", True)
        ]
        declared_params = {arg.arg for arg in function_node.args.args} | {
            arg.arg for arg in function_node.args.kwonlyargs
        }
        if function_node.args.vararg is not None:
            declared_params.add(function_node.args.vararg.arg)
        if function_node.args.kwarg is not None:
            declared_params.add(function_node.args.kwarg.arg)

        for param_name in required_params:
            if param_name and param_name not in declared_params:
                issues.append(
                    _issue(
                        "CODE_REQUIRED_INTERFACE_PARAMETER_MISSING",
                        generated_file,
                        f"La interfaz {symbol} no acepta el parámetro requerido {param_name}.",
                        action_ref=action_ref,
                        symbol=symbol,
                    )
                )

        if action_ref and interface_required and function_is_stub(function_node):
            issues.append(
                _issue(
                    "CODE_REQUIRED_ACTION_STUB",
                    generated_file,
                    f"La acción requerida {symbol} es un stub.",
                    action_ref=action_ref,
                    symbol=symbol,
                )
            )

    return issues


def _extract_route_declarations(tree: ast.Module) -> List[RouteDeclaration]:
    routes: List[RouteDeclaration] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr.lower() not in {
                "get",
                "post",
                "put",
                "delete",
                "patch",
                "options",
            }:
                continue
            if not decorator.args:
                continue
            first = decorator.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            routes.append(
                RouteDeclaration(
                    method=func.attr.upper(),
                    path=first.value,
                    function_name=node.name,
                )
            )
    return routes


def _module_aliases_for_required_call(
    imported: Dict[str, tuple[str, str | None]],
    module_name: str,
    symbol_name: str,
) -> set[str]:
    aliases: set[str] = set()
    for local_name, (module, original) in imported.items():
        if module == module_name and original == symbol_name:
            aliases.add(local_name)
        if module == module_name and original is None:
            aliases.add(local_name)
    return aliases


def _function_body_invokes_required_call(
    function_node: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: set[str],
    symbol_name: str,
) -> bool:
    for node in ast.walk(function_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in aliases | {symbol_name}:
            return True
        if isinstance(func, ast.Attribute):
            if func.attr == symbol_name:
                if isinstance(func.value, ast.Name):
                    if func.value.id in aliases or func.value.id == symbol_name:
                        return True
                else:
                    return True
    return False


def _contract_methods_paths(file_contract: Dict[str, Any]) -> List[tuple[str, str]]:
    result: List[tuple[str, str]] = []
    endpoint_meta = file_contract.get("endpoint") or {}
    methods = endpoint_meta.get("methods") or endpoint_meta.get("method") or []
    if isinstance(methods, str):
        methods = [methods]
    path = str(endpoint_meta.get("path") or "").strip()
    for method in methods:
        method_value = str(method or "").strip().upper()
        if method_value and path:
            result.append((method_value, path))
    return result


def _validate_integration(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
) -> List[CodegenIssue]:
    tree = _python_tree(generated_file)
    if tree is None:
        return []

    issues = _validate_provided_interfaces(
        generated_file=generated_file,
        file_contract=file_contract,
        tree=tree,
    )
    issues.extend(
        _validate_configuration_access(
            generated_file=generated_file,
            file_contract=file_contract,
        )
    )

    imported = imported_symbols(tree)
    imported_modules = {module for module, _ in imported.values()}

    if "fastapi" in imported_modules:
        issues.append(
            _issue(
                "CODE_INTEGRATION_IMPORTS_FASTAPI",
                generated_file,
                "Un módulo de integración no debe importar fastapi.",
            )
        )

    if "HTTPException" in generated_file.content:
        issues.append(
            _issue(
                "CODE_INTEGRATION_USES_HTTP_EXCEPTION",
                generated_file,
                "Un módulo de integración no debe depender de HTTPException.",
            )
        )

    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call_names = called_symbol_names(node.value)
            if "build_client" in call_names or "client" in "".join(call_names).lower():
                issues.append(
                    _issue(
                        "CODE_EXTERNAL_CLIENT_CREATED_AT_IMPORT",
                        generated_file,
                        "El cliente externo no debe construirse en import-time.",
                    )
                )

    allowed_imports = file_contract.get("allowed_imports") or []
    import_roots = [
        str(item.get("import_root") or "").strip()
        for item in allowed_imports
        if isinstance(item, dict) and item.get("import_root")
    ]
    if import_roots and not any(root in generated_file.content for root in import_roots):
        issues.append(
            _issue(
                "CODE_INTEGRATION_SDK_UNUSED",
                generated_file,
                "La integración no utiliza ningún SDK/import_root permitido.",
            )
        )

    settings_tokens = ["settings", "get_settings", "config"]
    if not any(token in generated_file.content for token in settings_tokens):
        issues.append(
            _issue(
                "CODE_REQUIRED_CONFIGURATION_UNUSED",
                generated_file,
                "La integración no consume configuración asignada.",
            )
        )

    secret_keys = _secret_configuration_keys(file_contract)
    for assignment in _extract_literal_assignments(generated_file.content):
        if assignment.name not in secret_keys:
            continue
        issues.append(
            _issue(
                "EMBEDDED_SECRET_FORBIDDEN",
                generated_file,
                f"Secret configuration {assignment.name} is assigned a literal value.",
            )
        )

    provided_by_symbol = {
        str(item.get("symbol") or "").strip(): item
        for item in (file_contract.get("provided_interfaces") or [])
        if isinstance(item, dict)
    }
    functions = function_defs_by_name(tree)
    implementation_levels = {
        str(item).strip() for item in (file_contract.get("implementation_levels") or [])
    }
    for symbol, item in provided_by_symbol.items():
        function_node = functions.get(symbol)
        if function_node is None:
            continue
        if function_is_stub(function_node):
            issues.append(
                _issue(
                    "CODE_EXTERNAL_OPERATION_CONSTANT_RETURN",
                    generated_file,
                    f"La operación externa {symbol} es un stub o retorno constante.",
                    action_ref=str(item.get("action_ref") or "") or None,
                    symbol=symbol,
                )
            )
            continue
        if "integration_skeleton" in implementation_levels:
            call_names = called_symbol_names(function_node)
            if not call_names:
                issues.append(
                    _issue(
                        "CODE_EXTERNAL_OPERATION_MISSING",
                        generated_file,
                        f"La operación externa {symbol} no realiza ninguna llamada no trivial.",
                        action_ref=str(item.get("action_ref") or "") or None,
                        symbol=symbol,
                    )
                )

    return issues


def _validate_endpoint(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
) -> List[CodegenIssue]:
    tree = _python_tree(generated_file)
    if tree is None:
        return []

    issues = _validate_provided_interfaces(
        generated_file=generated_file,
        file_contract=file_contract,
        tree=tree,
    )
    issues.extend(
        _validate_configuration_access(
            generated_file=generated_file,
            file_contract=file_contract,
        )
    )
    issues.extend(
        _validate_required_internal_call_data_flow(
            generated_file=generated_file,
            file_contract=file_contract,
            tree=tree,
        )
    )

    imported = imported_symbols(tree)
    imported_modules = {module for module, _ in imported.values()}
    if "APIRouter" not in generated_file.content:
        issues.append(
            _issue(
                "CODE_ENDPOINT_ROUTE_MISSING",
                generated_file,
                "El endpoint debe declarar APIRouter.",
            )
        )

    contract_routes = _contract_methods_paths(file_contract)
    declared_routes = _extract_route_declarations(tree)
    declared_pairs = {(item.method, item.path) for item in declared_routes}

    for route in contract_routes:
        if route not in declared_pairs:
            issues.append(
                _issue(
                    "CODE_ENDPOINT_ROUTE_MISMATCH",
                    generated_file,
                    f"Falta la ruta requerida {route[0]} {route[1]}.",
                )
            )

    if len(declared_pairs) != len(declared_routes):
        issues.append(
            _issue(
                "CODE_ENDPOINT_ROUTE_MISMATCH",
                generated_file,
                "Hay rutas duplicadas en el endpoint.",
            )
        )

    route_by_function = {item.function_name: item for item in declared_routes}
    function_map = function_defs_by_name(tree)

    required_calls = file_contract.get("required_internal_calls") or []
    for required_call in required_calls:
        if not isinstance(required_call, dict):
            continue
        module_name = str(required_call.get("module") or "").strip()
        symbol_name = str(required_call.get("symbol") or "").strip()
        aliases = _module_aliases_for_required_call(imported, module_name, symbol_name)
        if not aliases and not any(
            module == module_name and original is None
            for module, original in imported.values()
        ):
            issues.append(
                _issue(
                    "CODE_INTERNAL_CALL_IMPORT_MISSING",
                    generated_file,
                    f"No se importa {symbol_name} desde {module_name}.",
                    action_ref=str(required_call.get("action_ref") or "") or None,
                    symbol=symbol_name,
                )
            )
            continue

        invoked = False
        for function_name, route in route_by_function.items():
            if route.path and function_name in function_map:
                if _function_body_invokes_required_call(
                    function_map[function_name],
                    aliases,
                    symbol_name,
                ):
                    invoked = True
                    break
        if not invoked:
            issues.append(
                _issue(
                    "CODE_REQUIRED_INTERNAL_CALL_NOT_INVOKED",
                    generated_file,
                    f"La llamada requerida {symbol_name} no se invoca dentro del handler.",
                    action_ref=str(required_call.get("action_ref") or "") or None,
                    symbol=symbol_name,
                )
            )

    allowed_imports = file_contract.get("allowed_imports") or []
    allowed_modules = {
        str(item.get("module") or "").strip()
        for item in allowed_imports
        if isinstance(item, dict)
    }
    sdk_roots = [
        str(item.get("import_root") or "").strip()
        for item in allowed_imports
        if isinstance(item, dict) and item.get("import_root")
    ]
    if sdk_roots and any(root in generated_file.content for root in sdk_roots):
        if not any(module.startswith("app.integrations") for module in allowed_modules):
            issues.append(
                _issue(
                    "CODE_ENDPOINT_IMPORTS_EXTERNAL_SDK",
                    generated_file,
                    "El endpoint no debe importar directamente SDKs externos.",
                )
            )

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route = route_by_function.get(node.name)
        if route is None:
            continue
        if function_is_stub(node):
            issues.append(
                _issue(
                    "CODE_FAKE_SUCCESS_RESPONSE",
                    generated_file,
                    f"El handler {node.name} devuelve éxito constante.",
                )
            )

    endpoint_meta = file_contract.get("endpoint") or {}
    errors = endpoint_meta.get("errors") or []
    if errors:
        for err in errors:
            status_code = str((err or {}).get("status_code") or "").strip()
            if status_code and status_code not in generated_file.content:
                issues.append(
                    _issue(
                        "CODE_DECLARED_ERROR_UNHANDLED",
                        generated_file,
                        f"No aparece manejo visible para el error {status_code}.",
                    )
                )

    request = endpoint_meta.get("request") or {}
    request_type = str(request.get("content_type") or request.get("type") or "").lower()
    if "json" in request_type and (
        "UploadFile" in generated_file.content or "File(" in generated_file.content
    ):
        issues.append(
            _issue(
                "CODE_ENDPOINT_REQUEST_TYPE_MISMATCH",
                generated_file,
                "Un endpoint JSON no debe usar UploadFile ni multipart.",
            )
        )
    if "multipart" in request_type and (
        "UploadFile" not in generated_file.content and "File(" not in generated_file.content
    ):
        issues.append(
            _issue(
                "CODE_ENDPOINT_REQUEST_TYPE_MISMATCH",
                generated_file,
                "Un endpoint multipart debe aceptar archivo.",
            )
        )

    route_paths = [path for _, path in contract_routes]
    if route_paths == ["/health"] and "app.integrations" not in " ".join(allowed_modules):
        if any(module.startswith("app.integrations") for module in imported_modules):
            issues.append(
                _issue(
                    "CODE_HEALTH_IMPORTS_INTEGRATION",
                    generated_file,
                    "El endpoint /health aislado no debe importar integraciones.",
                )
            )

    return issues


def _declared_external_import_roots(
    file_contract: Dict[str, Any],
) -> set[str]:
    roots: set[str] = set()

    for dependency in (file_contract.get("external_dependencies") or []):
        if not isinstance(dependency, dict):
            continue
        direct_root = str(dependency.get("import_root") or "").strip()
        if direct_root:
            roots.add(direct_root)
        for root in (dependency.get("import_roots") or []):
            normalized = str(root or "").strip()
            if normalized:
                roots.add(normalized)

    return roots


def _imported_module_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    imported = imported_symbols(tree)
    for module_name, _original in imported.values():
        normalized = str(module_name or "").strip()
        if not normalized:
            continue
        roots.add(normalized.split(".", 1)[0])

    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            module_name = str(alias.name or "").strip()
            if module_name:
                roots.add(module_name.split(".", 1)[0])

    return roots


def _is_trivial_config_assignment_call(node: ast.Assign) -> bool:
    if not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    if not isinstance(func, ast.Name):
        return False
    return func.id in {"Settings", "BaseSettings"}


def _validate_config(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
) -> List[CodegenIssue]:
    tree = _python_tree(generated_file)
    if tree is None:
        return []

    issues: List[CodegenIssue] = []
    content = generated_file.content
    config_keys = {
        str(item.get("name") or "").strip()
        for item in (file_contract.get("config_keys") or [])
        if isinstance(item, dict)
    }

    for key in config_keys:
        if key and key not in content:
            issues.append(
                _issue(
                    "CODE_CONFIG_KEY_MISSING",
                    generated_file,
                    f"No aparece la clave de configuración {key}.",
                )
            )

    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            issues.append(
                _issue(
                    "CODE_CONFIG_IMPORT_TIME_FAILURE",
                    generated_file,
                    "config no debe ejecutar lógica obligatoria en import-time.",
                )
            )
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if not _is_trivial_config_assignment_call(node):
                issues.append(
                    _issue(
                        "CODE_CONFIG_IMPORT_TIME_FAILURE",
                        generated_file,
                        "config no debe crear objetos con lógica no trivial en import-time.",
                    )
                )

    external_roots = {
        root.split(".", 1)[0]
        for root in _declared_external_import_roots(file_contract)
        if root
    }
    if external_roots:
        imported_roots = _imported_module_roots(tree)
        forbidden = sorted(imported_roots & external_roots)
        if forbidden:
            issues.append(
                _issue(
                    "CODE_CONFIG_EXTERNAL_IMPORT",
                    generated_file,
                    "config no debe importar tecnologías externas declaradas: "
                    + ", ".join(forbidden),
                )
            )

    dependencies = " ".join(str(item) for item in (file_contract.get("dependencies") or []))
    if (
        ("pydantic-settings" in dependencies or "pydantic>=" in dependencies or "pydantic==" in dependencies)
        and "from pydantic import BaseSettings" in content
    ):
        issues.append(
            _issue(
                "CODE_CONFIG_BASESETTINGS_INCOMPATIBLE",
                generated_file,
                "BaseSettings desde pydantic es incompatible con esta configuración.",
            )
        )

    if "def get_settings" not in content:
        issues.append(
            _issue(
                "CODE_CONFIG_GET_SETTINGS_MISSING",
                generated_file,
                "Debe existir get_settings lazy/cacheada.",
            )
        )

    return issues


def _validate_requirements(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    lines = [line.rstrip() for line in generated_file.content.splitlines()]
    dependencies = [
        str(item).strip()
        for item in (file_contract.get("dependencies") or [])
        if str(item).strip()
    ]

    normalized_lines = [line.strip() for line in lines if line.strip()]
    for dep in dependencies:
        if dep not in normalized_lines:
            issues.append(
                _issue(
                    "CODE_REQUIREMENT_MISSING",
                    generated_file,
                    f"Falta la dependencia {dep}.",
                )
            )

    if any(line.lstrip().startswith(("#", "-", "*")) for line in lines):
        issues.append(
            _issue(
                "CODE_REQUIREMENTS_MARKDOWN",
                generated_file,
                "requirements no debe contener Markdown.",
            )
        )

    allowed_imports = file_contract.get("allowed_imports") or []
    for item in allowed_imports:
        if not isinstance(item, dict):
            continue
        import_root = str(item.get("import_root") or "").strip()
        package = str(item.get("package") or "").strip()
        if import_root and package and import_root != package and import_root in normalized_lines:
            issues.append(
                _issue(
                    "CODE_REQUIREMENTS_IMPORT_ROOT_USED",
                    generated_file,
                    f"Se usó import_root {import_root} en lugar del package {package}.",
                )
            )

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if " " in stripped and not any(op in stripped for op in ("==", ">=", "<=", "~=", ">", "<")):
            issues.append(
                _issue(
                    "CODE_REQUIREMENTS_INVALID_LINE",
                    generated_file,
                    f"Línea inválida en requirements: {stripped}",
                )
            )

    return issues


def _validate_service(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
) -> List[CodegenIssue]:
    tree = _python_tree(generated_file)
    if tree is None:
        return []
    return _validate_provided_interfaces(
        generated_file=generated_file,
        file_contract=file_contract,
        tree=tree,
    )


def _validate_repository(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
) -> List[CodegenIssue]:
    tree = _python_tree(generated_file)
    if tree is None:
        return []
    return _validate_provided_interfaces(
        generated_file=generated_file,
        file_contract=file_contract,
        tree=tree,
    )


def _validate_test(
    *,
    generated_file: GeneratedFile,
    file_contract: Dict[str, Any],
) -> List[CodegenIssue]:
    return []
