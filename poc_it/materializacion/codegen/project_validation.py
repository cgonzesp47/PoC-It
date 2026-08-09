from __future__ import annotations

import ast
from typing import Any, Dict, List

from poc_it.generador.file_contracts_validation import (
    validate_generated_python_against_file_contracts,
)

from .ast_utils import function_defs_by_name, imported_symbols, parse_python_module
from .models import CodegenIssue, GeneratedFile
from .semantic_validation import RouteDeclaration
from .test_planning import build_endpoint_test_contracts

_DIRECT_NETWORK_CALLS: set[tuple[str, str]] = {
    ("requests", "get"),
    ("requests", "post"),
    ("requests", "put"),
    ("requests", "patch"),
    ("requests", "delete"),
    ("httpx", "get"),
    ("httpx", "post"),
    ("httpx", "put"),
    ("httpx", "patch"),
    ("httpx", "delete"),
    ("urllib.request", "urlopen"),
    ("socket", "create_connection"),
}


def validate_generated_project(
    *,
    generated_files: List[GeneratedFile],
    file_contracts: List[Dict[str, Any]],
    spec: Dict[str, Any],
) -> List[CodegenIssue]:
    files_by_path = {item.path.replace("\\", "/"): item for item in generated_files}
    contracts_by_path = {
        str(item.get("path") or "").replace("\\", "/"): item for item in file_contracts
    }
    interfaces_by_module_and_symbol: dict[tuple[str, str], dict[str, Any]] = {}

    for contract in file_contracts:
        if not isinstance(contract, dict):
            continue
        module = _module_from_path(str(contract.get("path") or ""))
        for item in contract.get("provided_interfaces") or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip()
            if module and symbol:
                interfaces_by_module_and_symbol[(module, symbol)] = item

    issues: List[CodegenIssue] = []
    issues.extend(
        _validate_internal_interfaces(
            files_by_path=files_by_path,
            contracts_by_path=contracts_by_path,
            interfaces_by_module_and_symbol=interfaces_by_module_and_symbol,
        )
    )
    issues.extend(
        _validate_required_actions(
            spec=spec,
            files_by_path=files_by_path,
            contracts_by_path=contracts_by_path,
            interfaces_by_module_and_symbol=interfaces_by_module_and_symbol,
        )
    )
    issues.extend(
        _validate_integrations(
            spec=spec,
            files_by_path=files_by_path,
            contracts_by_path=contracts_by_path,
        )
    )
    issues.extend(
        _validate_configuration_usage(
            files_by_path=files_by_path,
            contracts_by_path=contracts_by_path,
        )
    )
    issues.extend(
        _validate_declared_errors(
            spec=spec,
            files_by_path=files_by_path,
            contracts_by_path=contracts_by_path,
        )
    )
    issues.extend(
        _validate_project_routes(
            spec=spec,
            files_by_path=files_by_path,
            contracts_by_path=contracts_by_path,
        )
    )
    issues.extend(
        _validate_generated_tests(
            spec=spec,
            files_by_path=files_by_path,
            file_contracts=file_contracts,
        )
    )
    issues.extend(
        [
            _issue(
                error.code,
                error.path,
                error.message,
                details=error.details,
            )
            for error in validate_generated_python_against_file_contracts(
                files_by_path={
                    path: file.content
                    for path, file in files_by_path.items()
                    if path.endswith(".py")
                },
                file_contracts=file_contracts,
            )
        ]
    )
    return issues


def classify_generated_project(
    *,
    generated_files: List[GeneratedFile],
    file_contracts: List[Dict[str, Any]],
    spec: Dict[str, Any],
    static_validation_passed: bool,
    runtime_tests_passed: bool | None,
) -> Dict[str, Any]:
    issues = validate_generated_project(
        generated_files=generated_files,
        file_contracts=file_contracts,
        spec=spec,
    )
    blocking = [issue for issue in issues if issue.severity == "error"]
    has_external_required = _spec_has_required_external_integrations(spec)

    if blocking or not static_validation_passed:
        return {
            "status": "invalid",
            "external_connectivity_verified": False,
            "runtime_tests_passed": runtime_tests_passed,
            "issues": issues,
        }

    if runtime_tests_passed is None:
        return {
            "status": "pending_runtime_tests",
            "external_connectivity_verified": False,
            "runtime_tests_passed": None,
            "issues": issues,
            "message": (
                "El código generado ha superado las validaciones estáticas. "
                "Falta ejecutar los tests runtime de la PoC."
            ),
        }

    if runtime_tests_passed is False:
        return {
            "status": "invalid",
            "external_connectivity_verified": False,
            "runtime_tests_passed": False,
            "issues": issues,
            "message": (
                "La validación estática pasó, pero los tests runtime fallaron."
            ),
        }

    if has_external_required:
        return {
            "status": "valid_integration_skeleton",
            "external_connectivity_verified": False,
            "runtime_tests_passed": True,
            "issues": issues,
            "message": (
                "La base de integración está implementada y validada mediante "
                "tests herméticos. La conexión real requiere configurar los "
                "recursos y credenciales indicados en la documentación."
            ),
        }

    return {
        "status": "valid_local",
        "external_connectivity_verified": False,
        "runtime_tests_passed": True,
        "issues": issues,
    }


def _issue(
    code: str,
    path: str,
    message: str,
    *,
    severity: str = "error",
    action_ref: str | None = None,
    symbol: str | None = None,
    details: Dict[str, Any] | None = None,
) -> CodegenIssue:
    return CodegenIssue(
        code=code,
        path=path,
        message=message,
        severity=severity,
        action_ref=action_ref,
        symbol=symbol,
        details=dict(details or {}),
    )


def _normalize_import_root(value: Any) -> str:
    return str(value or "").strip()


def _configuration_key_name(item: Dict[str, Any]) -> str:
    return str(item.get("key") or item.get("name") or item.get("env_name") or "").strip()


def _module_matches_import_root(module_name: str, import_root: str) -> bool:
    module = str(module_name or "").strip()
    root = str(import_root or "").strip()

    if not module or not root:
        return False

    return module == root or module.startswith(f"{root}.")


def _imports_declared_external_module(
    *,
    imported_modules: set[str],
    external_import_roots: set[str],
) -> bool:
    return any(
        _module_matches_import_root(module_name, import_root)
        for module_name in imported_modules
        for import_root in external_import_roots
    )


def _external_import_roots(file_contracts: List[Dict[str, Any]]) -> set[str]:
    roots: set[str] = set()

    for contract in file_contracts:
        if not isinstance(contract, dict):
            continue

        for dependency in contract.get("external_dependencies") or []:
            if not isinstance(dependency, dict):
                continue

            direct_root = _normalize_import_root(dependency.get("import_root"))
            if direct_root:
                roots.add(direct_root)

            for root in dependency.get("import_roots") or []:
                normalized = _normalize_import_root(root)
                if normalized:
                    roots.add(normalized)

        source = contract.get("source") or {}
        if isinstance(source, dict):
            technology_signals = source.get("technology_signals") or []
            for signal in technology_signals:
                if not isinstance(signal, dict):
                    continue

                direct_root = _normalize_import_root(signal.get("import_root"))
                if direct_root:
                    roots.add(direct_root)

                for root in signal.get("import_roots") or []:
                    normalized = _normalize_import_root(root)
                    if normalized:
                        roots.add(normalized)

        for allowed_import in contract.get("allowed_imports") or []:
            if not isinstance(allowed_import, dict):
                continue

            root = _normalize_import_root(allowed_import.get("import_root"))
            if root:
                roots.add(root)

    return {
        root
        for root in roots
        if root
        and not root.startswith("app.")
        and root != "app"
        and not root.startswith("tests.")
        and root != "tests"
        and not root.startswith("poc_it.")
        and root != "poc_it"
    }


def _external_configuration_keys(file_contracts: List[Dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    external_kinds = {"integration", "service", "repository"}

    for contract in file_contracts:
        if not isinstance(contract, dict):
            continue

        kind = str(contract.get("kind") or "").strip()
        if kind not in external_kinds:
            continue

        for item in contract.get("configuration") or []:
            if not isinstance(item, dict):
                continue
            key = _configuration_key_name(item)
            if key:
                keys.add(key)

        for item in contract.get("config_keys") or []:
            if not isinstance(item, dict):
                continue
            key = _configuration_key_name(item)
            if key:
                keys.add(key)

    return keys


def _contract_integration_refs(contract: Dict[str, Any]) -> set[str]:
    refs: set[str] = set()

    for value in contract.get("integration_refs") or []:
        normalized = str(value or "").strip()
        if normalized:
            refs.add(normalized)

    direct = str(contract.get("integration_ref") or "").strip()
    if direct:
        refs.add(direct)

    source = contract.get("source") or {}
    if isinstance(source, dict):
        integration = source.get("integration")
        if isinstance(integration, dict):
            integration_id = str(integration.get("id") or "").strip()
            if integration_id:
                refs.add(integration_id)

    return refs


def _module_from_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    if not normalized.endswith(".py"):
        return ""
    module = normalized[:-3].replace("/", ".")
    if module.endswith(".__init__"):
        module = module[: -len(".__init__")]
    return module


def _parse_tree_map(files_by_path: dict[str, GeneratedFile]) -> dict[str, ast.Module]:
    result: dict[str, ast.Module] = {}
    for path, file in files_by_path.items():
        tree = parse_python_module(file.content) if path.endswith(".py") else None
        if tree is not None:
            result[path] = tree
    return result


def _consumer_imports_symbol(tree: ast.Module, module_name: str, symbol_name: str) -> bool:
    imported = imported_symbols(tree)
    for _local_name, (module, original) in imported.items():
        if module == module_name and original == symbol_name:
            return True
        if module == module_name and original is None:
            return True
    return False


def _consumer_calls_symbol(tree: ast.Module, symbol_name: str) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == symbol_name:
            return True
        if isinstance(func, ast.Attribute) and func.attr == symbol_name:
            return True
    return False


def _validate_internal_interfaces(
    *,
    files_by_path: dict[str, GeneratedFile],
    contracts_by_path: dict[str, Dict[str, Any]],
    interfaces_by_module_and_symbol: dict[tuple[str, str], dict[str, Any]],
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    trees = _parse_tree_map(files_by_path)

    for consumer_path, contract in contracts_by_path.items():
        required_calls = contract.get("required_internal_calls") or []
        consumer_tree = trees.get(consumer_path)
        for call in required_calls:
            if not isinstance(call, dict):
                continue
            module_name = str(call.get("module") or "").strip()
            symbol_name = str(call.get("symbol") or "").strip()
            owner_path = next(
                (path for path in contracts_by_path if _module_from_path(path) == module_name),
                "",
            )
            if not owner_path:
                issues.append(
                    _issue(
                        "PROJECT_INTERNAL_MODULE_MISSING",
                        consumer_path,
                        f"No existe el módulo interno {module_name}.",
                    )
                )
                continue

            if (module_name, symbol_name) not in interfaces_by_module_and_symbol:
                issues.append(
                    _issue(
                        "PROJECT_INTERNAL_SYMBOL_CONTRACT_MISSING",
                        consumer_path,
                        f"El contrato de {module_name} no ofrece {symbol_name}.",
                        action_ref=str(call.get("action_ref") or "").strip() or None,
                        symbol=symbol_name or None,
                    )
                )

            owner_tree = trees.get(owner_path)
            owner_functions = function_defs_by_name(owner_tree) if owner_tree is not None else {}
            if symbol_name not in owner_functions:
                issues.append(
                    _issue(
                        "PROJECT_INTERNAL_SYMBOL_CODE_MISSING",
                        owner_path or consumer_path,
                        f"El código de {module_name} no define {symbol_name}.",
                        action_ref=str(call.get("action_ref") or "").strip() or None,
                        symbol=symbol_name or None,
                    )
                )

            if consumer_tree is None:
                continue
            if not _consumer_imports_symbol(consumer_tree, module_name, symbol_name):
                issues.append(
                    _issue(
                        "PROJECT_INTERNAL_SYMBOL_NOT_IMPORTED",
                        consumer_path,
                        f"El consumidor no importa {symbol_name} desde {module_name}.",
                        action_ref=str(call.get("action_ref") or "").strip() or None,
                        symbol=symbol_name or None,
                    )
                )
            if not _consumer_calls_symbol(consumer_tree, symbol_name):
                issues.append(
                    _issue(
                        "PROJECT_INTERNAL_SYMBOL_NOT_CALLED",
                        consumer_path,
                        f"El consumidor no invoca {symbol_name}.",
                        action_ref=str(call.get("action_ref") or "").strip() or None,
                        symbol=symbol_name or None,
                    )
                )

    return issues


def _iter_required_actions(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for endpoint in spec.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        for action in endpoint.get("actions") or []:
            if isinstance(action, dict) and action.get("required", True):
                action_copy = dict(action)
                action_copy["_endpoint"] = endpoint
                actions.append(action_copy)
    return actions


def _consumer_path_for_action(
    *,
    action_ref: str,
    symbol: str | None,
    contracts_by_path: dict[str, Dict[str, Any]],
) -> str | None:
    normalized_action = str(action_ref or "").strip()
    normalized_symbol = str(symbol or "").strip()

    for path, contract in contracts_by_path.items():
        calls = contract.get("required_internal_calls") or []
        for call in calls:
            if not isinstance(call, dict):
                continue
            call_action = str(call.get("action_ref") or "").strip()
            call_symbol = str(call.get("symbol") or "").strip()
            if normalized_action and call_action == normalized_action:
                return path
            if normalized_symbol and call_symbol == normalized_symbol:
                return path

    return None


def _validate_required_actions(
    *,
    spec: Dict[str, Any],
    files_by_path: dict[str, GeneratedFile],
    contracts_by_path: dict[str, Dict[str, Any]],
    interfaces_by_module_and_symbol: dict[tuple[str, str], dict[str, Any]],
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    trees = _parse_tree_map(files_by_path)

    for action in _iter_required_actions(spec):
        action_id = str(action.get("id") or "").strip()
        owner_contract = None
        owner_path = ""
        for path, contract in contracts_by_path.items():
            owned_refs = {str(item).strip() for item in (contract.get("owned_action_refs") or [])}
            provided_refs = {
                str(item.get("action_ref") or "").strip()
                for item in (contract.get("provided_interfaces") or [])
                if isinstance(item, dict)
            }
            if action_id in owned_refs or action_id in provided_refs:
                owner_contract = contract
                owner_path = path
                break

        if owner_contract is None:
            issues.append(
                _issue(
                    "PROJECT_REQUIRED_ACTION_OWNER_MISSING",
                    "",
                    f"No existe propietario para la acción requerida {action_id}.",
                )
            )
            continue

        owner_module = _module_from_path(owner_path)
        owner_interfaces = [
            item
            for item in (owner_contract.get("provided_interfaces") or [])
            if isinstance(item, dict) and str(item.get("action_ref") or "").strip() == action_id
        ]
        if not owner_interfaces:
            issues.append(
                _issue(
                    "PROJECT_REQUIRED_ACTION_INTERFACE_MISSING",
                    owner_path,
                    f"Falta interfaz para la acción requerida {action_id}.",
                )
            )
            continue

        symbol_name = str(owner_interfaces[0].get("symbol") or "").strip()
        if (owner_module, symbol_name) not in interfaces_by_module_and_symbol:
            issues.append(
                _issue(
                    "PROJECT_REQUIRED_ACTION_INTERFACE_MISSING",
                    owner_path,
                    f"La interfaz {symbol_name} no está indexada para {action_id}.",
                )
            )

        owner_tree = trees.get(owner_path)
        owner_functions = function_defs_by_name(owner_tree) if owner_tree is not None else {}
        if symbol_name not in owner_functions:
            issues.append(
                _issue(
                    "PROJECT_REQUIRED_ACTION_IMPLEMENTATION_MISSING",
                    owner_path,
                    f"El símbolo {symbol_name} no está implementado para {action_id}.",
                    action_ref=action_id or None,
                    symbol=symbol_name or None,
                )
            )

        needs_consumer = str(action.get("kind") or "").strip() in {"external_call", "notification"}
        if needs_consumer:
            reachable = False
            for consumer_path, consumer_contract in contracts_by_path.items():
                for call in consumer_contract.get("required_internal_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    if str(call.get("action_ref") or "").strip() != action_id:
                        continue
                    tree = trees.get(consumer_path)
                    if tree is not None and _consumer_calls_symbol(tree, symbol_name):
                        reachable = True
                        break
                if reachable:
                    break
            if not reachable:
                consumer_path = _consumer_path_for_action(
                    action_ref=action_id,
                    symbol=symbol_name,
                    contracts_by_path=contracts_by_path,
                )
                issue_path = consumer_path or owner_path
                issues.append(
                    _issue(
                        "PROJECT_REQUIRED_ACTION_NOT_REACHABLE",
                        issue_path,
                        f"La acción requerida {action_id} no es alcanzable desde consumidores.",
                        action_ref=action_id or None,
                        symbol=symbol_name or None,
                    )
                )

    return issues


def _validate_integrations(
    *,
    spec: Dict[str, Any],
    files_by_path: dict[str, GeneratedFile],
    contracts_by_path: dict[str, Dict[str, Any]],
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    endpoint_service_paths = {
        path
        for path, contract in contracts_by_path.items()
        if str(contract.get("kind") or "").strip() in {"endpoint", "service", "repository"}
    }

    integration_refs: set[str] = set()
    for endpoint in spec.get("endpoints") or []:
        for action in (endpoint or {}).get("actions") or []:
            if isinstance(action, dict):
                integration_ref = str(action.get("integration_ref") or "").strip()
                if integration_ref:
                    integration_refs.add(integration_ref)

    for path, contract in contracts_by_path.items():
        if str(contract.get("kind") or "").strip() != "integration":
            continue
        module_text = files_by_path.get(path, GeneratedFile(path=path, content="")).content
        if not (contract.get("provided_interfaces") or []):
            issues.append(
                _issue(
                    "PROJECT_INTEGRATION_OPERATION_MISSING",
                    path,
                    "La integración no ofrece operaciones.",
                )
            )
        if not any(token in module_text for token in ("settings", "get_settings", "config")):
            issues.append(
                _issue(
                    "PROJECT_INTEGRATION_CONFIGURATION_UNUSED",
                    path,
                    "La integración no utiliza configuración.",
                )
            )

        allowed_imports = contract.get("allowed_imports") or []
        import_roots = [
            str(item.get("import_root") or "").strip()
            for item in allowed_imports
            if isinstance(item, dict) and item.get("import_root")
        ]
        if import_roots and not any(root in module_text for root in import_roots):
            issues.append(
                _issue(
                    "PROJECT_INTEGRATION_TECHNOLOGY_UNUSED",
                    path,
                    "La integración no utiliza la tecnología declarada.",
                )
            )

        referenced = False
        module_name = _module_from_path(path)
        for consumer_path in endpoint_service_paths:
            consumer_text = files_by_path.get(
                consumer_path, GeneratedFile(path=consumer_path, content="")
            ).content
            if module_name and module_name in consumer_text:
                referenced = True
                break
            for provided in contract.get("provided_interfaces") or []:
                if isinstance(provided, dict):
                    symbol = str(provided.get("symbol") or "").strip()
                    if symbol and symbol in consumer_text:
                        referenced = True
                        break
            if referenced:
                break
        if not referenced:
            issues.append(
                _issue(
                    "PROJECT_INTEGRATION_NOT_REFERENCED_BY_CODE",
                    path,
                    "La integración no está referenciada por código consumidor.",
                )
            )

    for integration_ref in integration_refs:
        found = False
        for contract in contracts_by_path.values():
            if integration_ref in _contract_integration_refs(contract):
                found = True
                break
        if not found:
            issues.append(
                _issue(
                    "PROJECT_INTEGRATION_MODULE_MISSING",
                    "",
                    f"No existe módulo propietario para la integración {integration_ref}.",
                )
            )

    return issues


def _validate_configuration_usage(
    *,
    files_by_path: dict[str, GeneratedFile],
    contracts_by_path: dict[str, Dict[str, Any]],
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    config_contract = next(
        (
            contract
            for contract in contracts_by_path.values()
            if str(contract.get("kind") or "").strip() == "config"
        ),
        None,
    )
    if config_contract is None:
        return issues

    config_path = str(config_contract.get("path") or "")
    config_text = files_by_path.get(config_path, GeneratedFile(path=config_path, content="")).content
    key_names = [
        str(item.get("name") or "").strip()
        for item in (config_contract.get("config_keys") or [])
        if isinstance(item, dict)
    ]
    project_text = {
        path: file.content
        for path, file in files_by_path.items()
        if path != config_path and not path.endswith(".md")
    }
    readme_text = "\n".join(
        file.content for path, file in files_by_path.items() if path.lower().endswith(".md")
    )

    for key in key_names:
        if key and key not in config_text:
            issues.append(
                _issue(
                    "PROJECT_CONFIGURATION_DECLARATION_MISSING",
                    config_path,
                    f"La configuración {key} no está declarada en config.",
                )
            )
        if key and not any(key in content for content in project_text.values()):
            if key in readme_text:
                issues.append(
                    _issue(
                        "PROJECT_CONFIGURATION_CONSUMER_MISSING",
                        config_path,
                        f"La configuración {key} solo aparece en README.",
                    )
                )
            else:
                issues.append(
                    _issue(
                        "PROJECT_CONFIGURATION_CONSUMER_MISSING",
                        config_path,
                        f"La configuración {key} no es consumida por el proyecto.",
                    )
                )
    return issues


def _status_code_appears_in_code(tree: ast.Module, status_code: int) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            func_name = ""
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr
            if func_name == "HTTPException":
                for keyword in node.keywords:
                    if keyword.arg == "status_code" and isinstance(keyword.value, ast.Constant):
                        if keyword.value.value == status_code:
                            return True
            for keyword in node.keywords:
                if keyword.arg in {"status_code", "status"} and isinstance(keyword.value, ast.Constant):
                    if keyword.value.value == status_code:
                        return True
    return False


def _validate_declared_errors(
    *,
    spec: Dict[str, Any],
    files_by_path: dict[str, GeneratedFile],
    contracts_by_path: dict[str, Dict[str, Any]],
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    for path, contract in contracts_by_path.items():
        if str(contract.get("kind") or "").strip() != "endpoint":
            continue
        endpoint_meta = contract.get("endpoint") or {}
        errors = endpoint_meta.get("errors") or []
        tree = parse_python_module(files_by_path.get(path, GeneratedFile(path=path, content="")).content)
        if tree is None:
            continue
        for err in errors:
            if not isinstance(err, dict):
                continue
            status_code = err.get("status_code")
            if isinstance(status_code, int) and not _status_code_appears_in_code(tree, status_code):
                issues.append(
                    _issue(
                        "PROJECT_DECLARED_ERROR_UNHANDLED",
                        path,
                        f"El error declarado {status_code} no está manejado en el flujo.",
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
            method = func.attr.upper()
            if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"}:
                continue
            if not decorator.args:
                continue
            first = decorator.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                routes.append(
                    RouteDeclaration(
                        method=method,
                        path=first.value,
                        function_name=node.name,
                    )
                )
    return routes


def _extract_include_prefixes(tree: ast.Module) -> List[str]:
    prefixes: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "include_router":
            for keyword in node.keywords:
                if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        prefixes.append(keyword.value.value)
    return prefixes


def _normalize_joined_route(prefix: str, path: str) -> str:
    left = (prefix or "").rstrip("/")
    right = (path or "").lstrip("/")
    if not left and not right:
        return "/"
    if not left:
        return f"/{right}".replace("//", "/")
    if not right:
        return left if left.startswith("/") else f"/{left}"
    joined = f"{left}/{right}"
    return joined if joined.startswith("/") else f"/{joined}"


def _validate_project_routes(
    *,
    spec: Dict[str, Any],
    files_by_path: dict[str, GeneratedFile],
    contracts_by_path: dict[str, Dict[str, Any]],
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    actual_routes: list[tuple[str, str, str]] = []
    seen_paths: set[tuple[str, str]] = set()

    for path, contract in contracts_by_path.items():
        if str(contract.get("kind") or "").strip() != "endpoint":
            continue
        tree = parse_python_module(files_by_path.get(path, GeneratedFile(path=path, content="")).content)
        if tree is None:
            continue
        routes = _extract_route_declarations(tree)
        prefixes = _extract_include_prefixes(tree)
        endpoint_meta = contract.get("endpoint") or {}
        contract_path = str(endpoint_meta.get("path") or "").strip()
        if not prefixes and contract_path:
            prefixes = [""]

        for route in routes:
            if not prefixes:
                full_path = route.path
                actual_routes.append((route.method, full_path, path))
                key = (route.method, full_path)
                if key in seen_paths:
                    issues.append(
                        _issue(
                            "PROJECT_ROUTE_DUPLICATED_PREFIX",
                            path,
                            f"Ruta duplicada por prefijo: {route.method} {full_path}",
                        )
                    )
                seen_paths.add(key)
                continue

            for prefix in prefixes:
                full_path = _normalize_joined_route(prefix, route.path)
                actual_routes.append((route.method, full_path, path))
                if contract_path and full_path != contract_path and full_path.endswith(contract_path):
                    issues.append(
                        _issue(
                            "PROJECT_ROUTE_DUPLICATED_PREFIX",
                            path,
                            f"Ruta duplicada por prefijo: {full_path}",
                        )
                    )
                key = (route.method, full_path)
                if key in seen_paths:
                    issues.append(
                        _issue(
                            "PROJECT_ROUTE_DUPLICATED_PREFIX",
                            path,
                            f"Ruta duplicada por prefijo: {route.method} {full_path}",
                        )
                    )
                seen_paths.add(key)

    expected_routes = set()
    for endpoint in spec.get("endpoints") or []:
        methods = endpoint.get("methods") or endpoint.get("method") or []
        if isinstance(methods, str):
            methods = [methods]
        route_path = str(endpoint.get("path") or "").strip()
        for method in methods:
            expected_routes.add((str(method or "").upper(), route_path))

    actual_route_pairs = {(method, path) for method, path, _ in actual_routes}
    for route in expected_routes:
        if route not in actual_route_pairs:
            issues.append(
                _issue(
                    "PROJECT_ROUTE_MISSING",
                    "",
                    f"Falta la ruta esperada {route[0]} {route[1]}.",
                )
            )

    for route in actual_route_pairs:
        if route not in expected_routes:
            issues.append(
                _issue(
                    "PROJECT_UNDECLARED_ROUTE",
                    "",
                    f"Ruta no declarada en SPEC: {route[0]} {route[1]}.",
                )
            )

    return issues


def _test_functions(tree: ast.Module | None) -> List[ast.FunctionDef | ast.AsyncFunctionDef]:
    if tree is None:
        return []

    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    ]


def _function_calls_route(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    method: str,
    path: str,
) -> bool:
    expected_method = str(method or "").strip().lower()
    expected_path = str(path or "").strip()

    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr.lower() != expected_method:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == expected_path:
            return True

    return False


def _source_segment_for_node(*, content: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(content, node)
    return segment or ""


def _qualified_call_name(node: ast.Call) -> tuple[str, str] | None:
    func = node.func

    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            return (func.value.id, func.attr)

        if isinstance(func.value, ast.Attribute):
            parts: list[str] = []
            current: ast.AST = func.value

            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value

            if isinstance(current, ast.Name):
                parts.append(current.id)
                module = ".".join(reversed(parts))
                return (module, func.attr)

    if isinstance(func, ast.Name):
        return ("", func.id)

    return None


def _contains_direct_network_calls(tree: ast.Module | None, content: str) -> bool:
    if tree is None:
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        qualified = _qualified_call_name(node)
        if qualified is None:
            continue

        if qualified in _DIRECT_NETWORK_CALLS:
            return True

    return False


def _imported_modules_from_text(content: str) -> set[str]:
    tree = parse_python_module(content)
    if tree is None:
        return set()
    imported = imported_symbols(tree)
    modules = {module for module, _ in imported.values() if module}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
    return modules


def _validate_generated_tests(
    *,
    spec: Dict[str, Any],
    files_by_path: dict[str, GeneratedFile],
    file_contracts: List[Dict[str, Any]],
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    test_contracts = build_endpoint_test_contracts(spec=spec, file_contracts=file_contracts)
    test_files = {
        path: file
        for path, file in files_by_path.items()
        if path.startswith("tests/") and path.endswith(".py")
    }

    if not test_contracts or not test_files:
        return issues

    external_import_roots = _external_import_roots(file_contracts)
    external_config_keys = _external_configuration_keys(file_contracts)
    combined_test_content = "\n".join(file.content for file in test_files.values())
    combined_tree = parse_python_module(combined_test_content) if combined_test_content.strip() else None
    imported_modules = _imported_modules_from_text(combined_test_content)
    has_assert = "assert " in combined_test_content
    has_test_client = any(
        token in combined_test_content
        for token in ("TestClient", "AsyncClient", "httpx.AsyncClient", "httpx.Client")
    )
    uses_monkeypatch_or_mock = any(
        token in combined_test_content
        for token in ("monkeypatch", "Mock(", "MagicMock(", "patch(", "AsyncMock(")
    )
    test_functions = _test_functions(combined_tree)

    if not has_test_client:
        issues.append(
            _issue(
                "TEST_ENDPOINT_ROUTE_NOT_COVERED",
                "tests/",
                "Los tests no importan TestClient ni cliente compatible.",
            )
        )

    if not has_assert:
        issues.append(
            _issue(
                "TEST_ENDPOINT_ROUTE_NOT_COVERED",
                "tests/",
                "Los tests no contienen asserts verificables.",
            )
        )

    if _imports_declared_external_module(
        imported_modules=imported_modules,
        external_import_roots=external_import_roots,
    ):
        issues.append(
            _issue(
                "TEST_EXTERNAL_CALL_NOT_MOCKED",
                "tests/",
                "Los tests importan directamente una tecnología externa declarada por los contratos.",
            )
        )

    if _contains_direct_network_calls(combined_tree, combined_test_content):
        issues.append(
            _issue(
                "TEST_EXTERNAL_CALL_NOT_MOCKED",
                "tests/",
                "Los tests contienen llamadas de red reales.",
            )
        )

    for contract in test_contracts:
        route_method = contract.method.lower()
        route_covered = any(
            _function_calls_route(function, method=contract.method, path=contract.path)
            for function in test_functions
        )
        if not route_covered:
            issues.append(
                _issue(
                    "TEST_ENDPOINT_ROUTE_NOT_COVERED",
                    contract.endpoint_file,
                    f"No existe cobertura visible para {contract.method} {contract.path}.",
                )
            )

        if contract.external_calls_forbidden and contract.internal_calls and not uses_monkeypatch_or_mock:
            issues.append(
                _issue(
                    "TEST_EXTERNAL_CALL_NOT_MOCKED",
                    contract.endpoint_file,
                    "Las llamadas internas externas deben mockearse con monkeypatch/mock.",
                )
            )

        for call in contract.internal_calls:
            symbol = str(call.get("symbol") or "").strip()
            if not symbol:
                continue
            if symbol not in combined_test_content:
                issues.append(
                    _issue(
                        "TEST_EXTERNAL_CALL_NOT_MOCKED",
                        contract.endpoint_file,
                        f"No se mockea ni referencia la llamada requerida {symbol}.",
                        action_ref=str(call.get("action_ref") or "") or None,
                        symbol=symbol,
                    )
                )
                continue

            if not any(
                token in combined_test_content
                for token in (
                    f"assert {symbol}",
                    "assert calls",
                    ".assert_called",
                    ".assert_awaited",
                    "captured_args",
                )
            ):
                issues.append(
                    _issue(
                        "TEST_REQUIRED_CALL_NOT_ASSERTED",
                        contract.endpoint_file,
                        f"La llamada requerida {symbol} no está verificada en tests.",
                        action_ref=str(call.get("action_ref") or "") or None,
                        symbol=symbol,
                    )
                )

        for declared_error in contract.declared_errors:
            status_code = declared_error.get("status_code")
            if isinstance(status_code, int) and str(status_code) not in combined_test_content:
                issues.append(
                    _issue(
                        "TEST_DECLARED_ERROR_NOT_COVERED",
                        contract.endpoint_file,
                        f"Falta cobertura del error declarado {status_code}.",
                    )
                )

        if contract.path == "/health":
            health_functions = [
                function
                for function in test_functions
                if _function_calls_route(function, method=contract.method, path=contract.path)
            ]
            health_test_content = "\n".join(
                _source_segment_for_node(content=combined_test_content, node=function)
                for function in health_functions
            )
            used_external_keys = {
                key for key in external_config_keys if key and key in health_test_content
            }
            if used_external_keys:
                issues.append(
                    _issue(
                        "TEST_HEALTH_REQUIRES_EXTERNAL_CONFIG",
                        contract.endpoint_file,
                        "Los tests de health utilizan configuración externa: "
                        + ", ".join(sorted(used_external_keys)),
                    )
                )

    return issues


def _spec_has_required_external_integrations(spec: Dict[str, Any]) -> bool:
    for endpoint in spec.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        for action in endpoint.get("actions") or []:
            if not isinstance(action, dict):
                continue
            if not action.get("required", True):
                continue
            if str(action.get("kind") or "").strip() in {"external_call", "notification"}:
                return True
    return False
