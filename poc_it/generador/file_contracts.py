from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from poc_it.generador.utils_python_names import (
    module_path_from_file_path,
    safe_python_identifier,
)

# Nombre FIJO y determinista de la función proveedora del cliente de una integración.
# Debe ser exactamente el mismo en el File Contract del fichero de integración (que la define,
# ver `required_symbols` más abajo) y en el del endpoint consumidor (que la importa e inyecta
# vía `Depends(...)`). Antes solo el lado de la integración tenía este nombre fijado
# (`required_symbols = ["build_client"]`); el lado del endpoint solo recibía la instrucción
# genérica "expón/inyecta un proveedor", sin el nombre exacto — cada llamada al LLM (son
# generaciones independientes, un fichero por llamada) inventaba su propio nombre, y como no
# coincidían, el endpoint terminaba sin saber qué importar.
_INTEGRATION_CLIENT_PROVIDER_SYMBOL = "build_client"


@dataclass(frozen=True)
class FileContract:
    path: str
    kind: str  # main | router | endpoint | config | schema | service | repository | integration | test | docs | requirements | package_init | unknown
    responsibilities: List[str] = field(default_factory=list)
    required_symbols: List[str] = field(default_factory=list)
    allowed_imports: List[str] = field(default_factory=list)
    forbidden_imports: List[str] = field(default_factory=list)
    endpoints: List[Dict[str, Any]] = field(default_factory=list)
    endpoint_contracts: List[Dict[str, Any]] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    env: List[Any] = field(default_factory=list)
    persistence: Dict[str, Any] = field(default_factory=dict)
    test_strategy: Dict[str, Any] = field(default_factory=dict)
    source: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    implementation_contracts: List[Dict[str, Any]] = field(default_factory=list)
    must_implement: List[str] = field(default_factory=list)
    must_not: List[str] = field(default_factory=list)
    implementation_plan: List[str] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    integration_refs: List[str] = field(default_factory=list)
    external_dependencies: List[Dict[str, Any]] = field(default_factory=list)
    implementation_levels: List[str] = field(default_factory=list)
    configuration: List[Dict[str, Any]] = field(default_factory=list)
    configuration_access: Dict[str, Any] = field(default_factory=dict)
    authentication_constraints: Dict[str, Any] = field(default_factory=dict)
    authentication_runtime_contract: Dict[str, Any] = field(default_factory=dict)
    provided_interfaces: List[Dict[str, Any]] = field(default_factory=list)
    required_internal_calls: List[Dict[str, Any]] = field(default_factory=list)
    included_routers: List[Dict[str, Any]] = field(default_factory=list)
    routing_convention: Dict[str, Any] = field(default_factory=dict)
    owned_action_refs: List[str] = field(default_factory=list)
    data_contracts: Dict[str, Any] = field(default_factory=dict)
    data_flows: List[Dict[str, Any]] = field(default_factory=list)


def file_contracts_to_dict(contracts: List[FileContract]) -> List[dict]:
    return [
        {
            "path": c.path,
            "kind": c.kind,
            "responsibilities": list(c.responsibilities),
            "required_symbols": list(c.required_symbols),
            "allowed_imports": list(c.allowed_imports),
            "forbidden_imports": list(c.forbidden_imports),
            "endpoints": list(c.endpoints),
            "endpoint_contracts": list(c.endpoint_contracts),
            "dependencies": list(c.dependencies),
            "env": list(c.env),
            "persistence": dict(c.persistence),
            "test_strategy": dict(c.test_strategy),
            "source": dict(c.source),
            "notes": list(c.notes),
            "implementation_contracts": list(c.implementation_contracts),
            "must_implement": list(c.must_implement),
            "must_not": list(c.must_not),
            "implementation_plan": list(c.implementation_plan),
            "actions": list(c.actions),
            "errors": list(c.errors),
            "integration_refs": list(c.integration_refs),
            "external_dependencies": list(c.external_dependencies),
            "implementation_levels": list(c.implementation_levels),
            "configuration": list(c.configuration),
            "configuration_access": dict(c.configuration_access),
            "authentication_constraints": dict(c.authentication_constraints),
            "authentication_runtime_contract": dict(
                c.authentication_runtime_contract
            ),
            "provided_interfaces": list(c.provided_interfaces),
            "required_internal_calls": list(c.required_internal_calls),
            "included_routers": list(c.included_routers),
            "routing_convention": dict(c.routing_convention),
            "owned_action_refs": list(c.owned_action_refs),
            "data_contracts": dict(c.data_contracts),
            "data_flows": list(c.data_flows),
        }
        for c in contracts
    ]


def build_file_contracts_from_spec(
    spec: dict,
    *,
    implementation_contracts: List[Dict[str, Any]] | None = None,
) -> List[FileContract]:
    if not isinstance(spec, dict):
        raise TypeError("spec must be a dict")

    implementation_contracts = (
        implementation_contracts if isinstance(implementation_contracts, list) else []
    )

    files = spec.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("spec.files must be a non-empty list")

    spec_files = [str(p).replace("\\", "/") for p in files if p]
    if not spec_files:
        raise ValueError("spec.files has no usable paths")

    endpoints = spec.get("endpoints", []) or []
    if not isinstance(endpoints, list):
        raise ValueError("spec.endpoints must be a list")

    deps = spec.get("dependencies", []) or []
    env = spec.get("env", []) or []
    persistence = spec.get("persistence", {}) or {}
    test_strategy = spec.get("test_strategy", {}) or {}
    source = spec.get("source", {}) or {}
    configuration_by_key = _configuration_by_key(spec)
    integrations_by_id = _integrations_by_id(spec)
    technologies_by_name = _technology_signals_by_name(spec)
    implementation_files = spec.get("implementation_files", []) or []

    endpoints_by_file: Dict[str, List[Dict[str, Any]]] = {}
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        f = str(ep.get("file") or "").replace("\\", "/")
        if not f:
            continue
        endpoints_by_file.setdefault(f, []).append(ep)

    implementation_by_endpoint = _implementation_contracts_by_endpoint(
        implementation_contracts
    )
    implementation_files_by_path = _implementation_files_by_path(implementation_files)
    integration_module_by_ref = _integration_module_by_ref(implementation_files)

    contracts: List[FileContract] = []
    seen: Set[str] = set()

    endpoint_files = sorted([f for f in endpoints_by_file.keys() if f])
    endpoint_files_in_spec = [f for f in endpoint_files if f in spec_files]
    endpoint_modules_in_spec = [
        module_path_from_file_path(f) for f in endpoint_files_in_spec
    ]

    for path in spec_files:
        if path in seen:
            raise ValueError(f"duplicate spec.files path: {path}")
        seen.add(path)

        kind = _infer_kind(path)

        responsibilities: List[str] = []
        required_symbols: List[str] = []
        allowed_imports: List[str] = []
        forbidden_imports: List[str] = []
        eps_for_file = endpoints_by_file.get(path, [])

        implementation_for_file: List[Dict[str, Any]] = []
        must_implement: List[str] = []
        must_not: List[str] = []
        implementation_plan: List[str] = []
        actions: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        integration_refs: List[str] = []
        external_dependencies: List[Dict[str, Any]] = []
        implementation_levels: List[str] = []
        configuration: List[Dict[str, Any]] = []
        endpoint_contracts: List[Dict[str, Any]] = []
        configuration_access: Dict[str, Any] = {}
        authentication_constraints: Dict[str, Any] = {}
        authentication_runtime_contract: Dict[str, Any] = {}
        provided_interfaces: List[Dict[str, Any]] = []
        required_internal_calls: List[Dict[str, Any]] = []
        included_routers: List[Dict[str, Any]] = []
        routing_convention: Dict[str, Any] = {}
        owned_action_refs: List[str] = []
        data_contracts: Dict[str, Any] = {}
        data_flows: List[Dict[str, Any]] = []

        notes: List[str] = []
        file_source: Dict[str, Any] = {}
        file_env: List[Any] = []
        file_dependencies: List[str] = []
        file_test_strategy: Dict[str, Any] = {}
        file_persistence: Dict[str, Any] = {}

        if kind == "main":
            required_symbols = ["app"]
            responsibilities = [
                "crear instancia FastAPI",
                "incluir router principal",
                "no contener lógica de negocio",
            ]
            allowed_imports = ["fastapi", "app.api.router"]
            file_source = dict(source)
        elif kind == "router":
            required_symbols = ["api_router"]
            responsibilities = [
                "crear APIRouter principal",
                "incluir routers de endpoint files",
                "no contener lógica de negocio",
            ]
            allowed_imports = ["fastapi"] + endpoint_modules_in_spec
            included_routers = [
                {
                    "module": module_path_from_file_path(f),
                    "prefix": "",
                }
                for f in endpoint_files_in_spec
            ]
            routing_convention = {
                "endpoint_owns_full_path": True,
                "router_prefix_policy": "empty_prefix_for_included_endpoint_routers",
            }
            notes.append("Debe incluir routers de todos los endpoint files del SPEC")
            file_source = dict(source)
        elif kind == "endpoint":
            base_responsibilities = [
                "declarar APIRouter local",
                "implementar handlers para method/path del SPEC",
                "respetar request/response/errors/source",
                "no decidir arquitectura global",
                "no crear conexiones reales en import-time",
            ]
            required_symbols = ["router"]
            for ep in eps_for_file:
                fn = ep.get("func") or ep.get("function") or ep.get("handler")
                if isinstance(fn, str) and fn.strip():
                    required_symbols.append(fn.strip())
            required_symbols = list(dict.fromkeys(required_symbols).keys())

            if bool((persistence or {}).get("required")):
                notes.append(
                    "Si persistence.required=true, no abrir DB en endpoint; depender de servicios/repositorios/ports o fakes"
                )

            for endpoint in eps_for_file:
                key = _endpoint_key(endpoint.get("method"), endpoint.get("path"))
                implementation_for_file.extend(implementation_by_endpoint.get(key, []))

            implementation_for_file = _dedupe_contract_dicts(implementation_for_file)
            endpoint_contracts = [
                _endpoint_contract_from_endpoint(endpoint)
                for endpoint in eps_for_file
                if isinstance(endpoint, dict)
            ]

            must_implement = _dedupe_strings(
                [
                    item
                    for contract in implementation_for_file
                    for item in (contract.get("must_implement") or [])
                ]
            )
            must_not = _dedupe_strings(
                [
                    item
                    for contract in implementation_for_file
                    for item in (contract.get("must_not") or [])
                ]
            )
            implementation_plan = _dedupe_strings(
                [
                    item
                    for contract in implementation_for_file
                    for item in (contract.get("implementation_plan") or [])
                ]
            )
            integration_refs = _dedupe_strings(
                [
                    item
                    for contract in implementation_for_file
                    for item in (contract.get("integration_refs") or [])
                ]
                + [
                    item
                    for endpoint in eps_for_file
                    for item in (endpoint.get("integration_refs") or [])
                    if isinstance(item, str)
                ]
            )
            implementation_levels = _dedupe_strings(
                [
                    item
                    for contract in implementation_for_file
                    for item in (contract.get("implementation_levels") or [])
                ]
            )

            action_groups: List[List[Dict[str, Any]]] = []
            error_groups: List[List[Dict[str, Any]]] = []
            external_dependency_groups: List[List[Dict[str, Any]]] = []

            for contract in implementation_for_file:
                contract_actions = contract.get("actions")
                if isinstance(contract_actions, list) and contract_actions:
                    action_groups.append(contract_actions)
                else:
                    endpoint = _find_matching_endpoint(
                        endpoints=eps_for_file,
                        method=contract.get("method"),
                        path=contract.get("path"),
                    )
                    if endpoint:
                        action_groups.append(
                            [
                                item
                                for item in (endpoint.get("actions") or [])
                                if isinstance(item, dict)
                            ]
                        )

                contract_errors = contract.get("errors")
                if isinstance(contract_errors, list) and contract_errors:
                    error_groups.append(contract_errors)
                else:
                    endpoint = _find_matching_endpoint(
                        endpoints=eps_for_file,
                        method=contract.get("method"),
                        path=contract.get("path"),
                    )
                    if endpoint:
                        error_groups.append(
                            [
                                item
                                for item in (endpoint.get("errors") or [])
                                if isinstance(item, dict)
                            ]
                        )

                external_dependency_groups.append(
                    [
                        item
                        for item in (contract.get("external_dependencies") or [])
                        if isinstance(item, dict)
                    ]
                )

            for endpoint in eps_for_file:
                endpoint_actions = endpoint.get("actions") or []
                if isinstance(endpoint_actions, list) and endpoint_actions:
                    action_groups.append(
                        [item for item in endpoint_actions if isinstance(item, dict)]
                    )

                endpoint_errors = endpoint.get("errors") or []
                if isinstance(endpoint_errors, list) and endpoint_errors:
                    error_groups.append(
                        [item for item in endpoint_errors if isinstance(item, dict)]
                    )

            actions = _merge_dict_lists(action_groups, identity_fields=("id",))
            errors = _merge_dict_lists(
                error_groups,
                identity_fields=("status_code", "code"),
            )
            external_dependencies = _merge_external_dependencies(
                external_dependency_groups
            )

            configuration_refs = _dedupe_strings(
                [
                    ref
                    for dependency in external_dependencies
                    for ref in (dependency.get("configuration_refs") or [])
                ]
            )
            for endpoint in eps_for_file:
                configuration_refs.extend(
                    [
                        conf_ref
                        for conf_ref in _endpoint_configuration_refs(endpoint)
                        if isinstance(conf_ref, str)
                    ]
                )
            for ref in integration_refs:
                integration = integrations_by_id.get(ref)
                if not isinstance(integration, dict):
                    continue
                configuration_refs.extend(
                    [
                        conf_ref
                        for conf_ref in (integration.get("configuration_refs") or [])
                        if isinstance(conf_ref, str)
                    ]
                )

            configuration = _resolve_configuration(
                configuration_refs=configuration_refs,
                configuration_by_key=configuration_by_key,
            )
            configuration_access = _build_configuration_access(
                path=path,
                kind=kind,
                configuration=configuration,
            )
            configuration_names = {
                str(item.get("key") or "").strip()
                for item in configuration
                if isinstance(item, dict)
            }
            file_env = [
                dict(item)
                for item in env
                if isinstance(item, dict)
                and str(item.get("name") or "").strip() in configuration_names
            ]

            dedicated_modules = [
                integration_module_by_ref[ref]
                for ref in integration_refs
                if ref in integration_module_by_ref
            ]
            has_dedicated_integration_module = bool(dedicated_modules)
            responsibilities = _build_endpoint_responsibilities(
                base=base_responsibilities,
                implementation_contracts=implementation_for_file,
                has_dedicated_integration_module=has_dedicated_integration_module,
            )
            if integration_refs and not has_dedicated_integration_module:
                notes.append(
                    "Implementar provisionalmente la integración en este archivo, sin conexiones en import-time y manteniendo la construcción del cliente en una función lazy."
                )
            if any(
                bool(item.get("required"))
                and str(item.get("delivery") or "env").strip() == "env"
                for item in configuration
                if isinstance(item, dict)
            ):
                must_not.append(
                    "Do not instantiate required environment-backed settings at import time."
                )
            routing_convention = {
                "endpoint_owns_full_path": True,
                "router_prefix_policy": "empty_prefix_for_included_endpoint_routers",
            }

            endpoint_specific_obligations: List[str] = []
            should_add_generic_obligations = (
                not implementation_for_file
                or bool(actions)
                or bool(errors)
                or bool(integration_refs)
                or bool(external_dependencies)
                or bool(configuration)
            )
            if should_add_generic_obligations:
                if any(
                    isinstance(endpoint.get("request"), dict)
                    and str(endpoint.get("request", {}).get("type") or "").strip()
                    and str(endpoint.get("request", {}).get("type") or "").strip()
                    != "none"
                    for endpoint in eps_for_file
                ):
                    endpoint_specific_obligations.append("Validate the declared request")
                if actions:
                    endpoint_specific_obligations.append("Execute all required actions")
                if integration_refs or external_dependencies:
                    # IMPORTANTE: la acción se sigue invocando como llamada de función DIRECTA
                    # (import + llamada), exactamente igual que antes — required_internal_calls
                    # sigue validando eso sin cambios. Lo único que cambia es de dónde sale el
                    # cliente que esa llamada necesita: en vez de que la función de la integración
                    # lo construya por dentro (no overrideable), el handler lo recibe vía
                    # `Depends(...)` y lo pasa como argumento en la misma llamada directa.
                    endpoint_specific_obligations.append("Invoke the referenced service or integration")
                    # CRÍTICO: dar el nombre EXACTO y el módulo EXACTO, no una descripción
                    # genérica. El fichero de integración y el de endpoint se generan en llamadas
                    # LLM independientes; si cada uno "inventa" su propio nombre para esta
                    # función, no coinciden y el endpoint no tiene nada real que importar.
                    provider_fqns = [
                        f"{module_path_from_file_path(module_path)}.{_INTEGRATION_CLIENT_PROVIDER_SYMBOL}"
                        for module_path in dedicated_modules
                    ]
                    if provider_fqns:
                        endpoint_specific_obligations.append(
                            "Import `"
                            + _INTEGRATION_CLIENT_PROVIDER_SYMBOL
                            + "` from "
                            + ", ".join(f"`{fqn.rsplit('.', 1)[0]}`" for fqn in provider_fqns)
                            + " and inject it as a `Depends("
                            + _INTEGRATION_CLIENT_PROVIDER_SYMBOL
                            + ")` parameter of the handler; pass the resulting client as an "
                            "argument in the (still direct) call to the action function — do "
                            "not let the action function build its own client internally, and "
                            "do not invent a different name for this provider function"
                        )
                    else:
                        endpoint_specific_obligations.append(
                            f"Inject that integration's client provider function "
                            f"(`{_INTEGRATION_CLIENT_PROVIDER_SYMBOL}`) as a `Depends(...)` "
                            "parameter of the handler, and pass the resulting client as an "
                            "argument in the (still direct) call to the action function — do "
                            "not let the action function build its own client internally"
                        )
                else:
                    endpoint_specific_obligations.append("Keep invocation logic inside the endpoint boundary")
                if errors:
                    endpoint_specific_obligations.append("Map declared HTTP errors")
                if any(endpoint.get("response") for endpoint in eps_for_file):
                    endpoint_specific_obligations.append("Build the declared response")

            must_implement = _dedupe_strings(
                must_implement + endpoint_specific_obligations
            )
            must_not = _dedupe_strings(
                must_not
                + (
                    [
                        "Do not embed credentials or secret values",
                        "Do not open external connections at import time",
                        "Do not return a hard-coded success response",
                        "Do not execute real integrations in hermetic tests",
                    ]
                    if integration_refs or external_dependencies
                    else []
                )
                + (
                    ["Do not make this endpoint depend on external systems"]
                    if _is_health_endpoint(eps_for_file)
                    and not (integration_refs or external_dependencies)
                    else []
                )
            )

            allowed_imports = ["fastapi", "typing", "pydantic"]
            if configuration:
                allowed_imports.append("app.core.config")
            if dedicated_modules:
                allowed_imports.extend(
                    [
                        module_path_from_file_path(module_path)
                        for module_path in dedicated_modules
                    ]
                )
            else:
                allowed_imports.extend(
                    _import_roots_for_integrations(
                        integration_refs=integration_refs,
                        integrations_by_id=integrations_by_id,
                        technologies_by_name=technologies_by_name,
                    )
                )
            allowed_imports = _dedupe_strings(allowed_imports)

            file_persistence = dict(persistence)
            file_test_strategy = dict(test_strategy)
            file_source = {
                "spec_source": dict(source),
                "implementation_contracts": list(implementation_for_file),
                "integrations": [
                    dict(integrations_by_id[ref])
                    for ref in integration_refs
                    if isinstance(integrations_by_id.get(ref), dict)
                ],
                "configuration": list(configuration),
                "technology_signals": _technology_signals_for_integrations(
                    integration_refs=integration_refs,
                    integrations_by_id=integrations_by_id,
                    technologies_by_name=technologies_by_name,
                ),
            }
        elif kind == "integration":
            implementation_file = implementation_files_by_path.get(path, {})
            integration_ref = str(implementation_file.get("integration_ref") or "").strip()
            integration = integrations_by_id.get(integration_ref, {})
            technology_notes = _missing_import_root_notes(
                integration_refs=[integration_ref],
                integrations_by_id=integrations_by_id,
                technologies_by_name=technologies_by_name,
            )
            notes.extend(technology_notes)
            required_symbols = [_INTEGRATION_CLIENT_PROVIDER_SYMBOL]
            responsibilities = [
                "encapsular la integración externa asignada",
                "construir el cliente de forma lazy",
                "aplicar autenticación declarada",
                "leer configuración declarada",
                "exponer operaciones llamables",
                "traducir excepciones del proveedor a errores internos",
                "no ejecutar llamadas externas en import-time",
            ]
            authentication = (
                dict(integration.get("authentication") or {})
                if isinstance(integration, dict)
                else {}
            )
            authentication_constraints = _build_authentication_constraints(authentication)
            authentication_runtime_contract = _build_authentication_runtime_contract(
                authentication_constraints
            )
            if authentication:
                if not bool(authentication.get("allows_embedded_secret")):
                    must_not.append("No almacenar secretos embebidos en código.")
                if not bool(authentication.get("allows_static_credential_file", True)):
                    must_not.append("No depender de un fichero estático de credenciales.")
                credential_source = str(authentication.get("credential_source") or "").strip()
                if credential_source and credential_source != "unknown":
                    responsibilities.append(
                        "Obtener credenciales desde la fuente declarada por el contrato: "
                        f"{credential_source}."
                    )
            integration_refs = [integration_ref] if integration_ref else []
            external_dependencies = _integration_external_dependencies(
                integration_ref=integration_ref,
                integration=integration,
                implementation_contracts=implementation_contracts,
            )
            implementation_levels = _dedupe_strings(
                [
                    item
                    for contract in implementation_contracts
                    if isinstance(contract, dict)
                    and integration_ref in (contract.get("integration_refs") or [])
                    for item in (contract.get("implementation_levels") or [])
                ]
                + [str(integration.get("implementation_level") or "").strip()]
            )
            configuration = _resolve_configuration(
                configuration_refs=_dedupe_strings(
                    integration.get("configuration_refs") or []
                ),
                configuration_by_key=configuration_by_key,
            )
            configuration_access = _build_configuration_access(
                path=path,
                kind=kind,
                configuration=configuration,
            )
            configuration_names = {
                str(item.get("key") or "").strip()
                for item in configuration
                if isinstance(item, dict)
            }
            file_env = [
                dict(item)
                for item in env
                if isinstance(item, dict)
                and str(item.get("name") or "").strip() in configuration_names
            ]
            actions = _integration_actions_for_ref(
                integration_ref=integration_ref,
                endpoints=endpoints,
                implementation_contracts=implementation_contracts,
            )
            must_implement = _dedupe_strings(
                [
                    "Build the integration client lazily",
                    "Apply the declared authentication flow",
                    "Read the declared configuration",
                    "Raise internal or domain errors",
                    # Concreto y accionable (no solo "permitir mocks"): separa "construir el
                    # cliente" (una función proveedora sin argumentos requeridos, compatible con
                    # `Depends(...)`) de "usar el cliente para ejecutar la acción" (la función de
                    # la acción NUNCA debe construir el cliente por dentro; debe recibirlo como
                    # parámetro). Así el endpoint consumidor puede inyectar el cliente vía
                    # `Depends(...)` y overridearlo en tests herméticos, sin dejar de invocar la
                    # función de acción directamente (sigue siendo una llamada de función normal,
                    # solo que con el cliente ya construido pasado como argumento).
                    # Nombre EXACTO y fijo (ver required_symbols): el fichero de endpoint que
                    # consume esta integración recibe la MISMA instrucción con este mismo
                    # nombre, para que ambas generaciones (independientes) coincidan.
                    f"Expose the client construction as its own zero-required-argument "
                    f"provider function (FastAPI-Depends-compatible) named exactly "
                    f"`{_INTEGRATION_CLIENT_PROVIDER_SYMBOL}`, separate from the action "
                    f"function(s)",
                    "Each action function must RECEIVE the client as an explicit parameter "
                    "instead of constructing/importing it internally",
                ]
                + [
                    f"Implement external action '{action.get('id')}'"
                    for action in actions
                    if isinstance(action, dict)
                    and str(action.get("id") or "").strip()
                ]
            )
            must_not = _dedupe_strings(
                [
                    "Do not store credentials",
                    "Do not open connections at import time",
                    "Do not return a fake success response",
                    "Do not convert provider exceptions directly into FastAPI responses",
                    "Do not perform real calls in hermetic tests",
                ]
            )
            allowed_imports = _dedupe_strings(
                ["typing", "functools", "app.core.config"]
                + _import_roots_for_integrations(
                    integration_refs=integration_refs,
                    integrations_by_id=integrations_by_id,
                    technologies_by_name=technologies_by_name,
                )
            )
            file_source = {
                "spec_source": dict(source),
                "integration": dict(integration) if isinstance(integration, dict) else {},
                "implementation_file": dict(implementation_file),
                "actions": list(actions),
                "technology_signals": _technology_signals_for_integrations(
                    integration_refs=integration_refs,
                    integrations_by_id=integrations_by_id,
                    technologies_by_name=technologies_by_name,
                ),
            }
        elif kind == "config":
            required_symbols = ["Settings", "get_settings"]
            responsibilities = [
                "configuración lazy",
                "variables de entorno",
                "no validar credenciales en import-time",
            ]
            # "functools" es stdlib (no requiere package). El import de settings NO se hardcodea
            # como literal "pydantic_settings": se deriva del TechnologySignal cuyo `packages`
            # incluye "pydantic-settings" (spec_builder.py añade esa señal como baseline
            # arquitectónico de PoC-it). Si esa señal faltara en spec.technology_signals, el
            # gate DEPENDENCY_IMPORT_NOT_DECLARED (file_contracts_validation.py) lo atraparía
            # antes de codegen en vez de dejar un import sin package instalable declarado.
            allowed_imports = ["functools"] + _import_roots_for_package(
                package="pydantic-settings",
                technologies_by_name=technologies_by_name,
            )
            if bool((persistence or {}).get("required")):
                notes.append(
                    "Preparar settings de persistencia sin hardcodear vendor si SPEC no lo exige"
                )
            configuration = [
                dict(item)
                for item in (spec.get("configuration") or [])
                if isinstance(item, dict) and str(item.get("key") or "").strip()
            ]
            configuration_access = _build_configuration_access(
                path=path,
                kind=kind,
                configuration=configuration,
            )
            provided_interfaces = [
                {
                    "symbol": "get_settings",
                    "kind": "function",
                    "parameters": [],
                    "interface_ref": "app.core.config:get_settings",
                    "return_hint": "Settings",
                }
            ]
            file_env = list(env)
            must_implement = _build_config_must_implement(configuration)
            must_not = [
                "Do not validate external credentials at import time",
                "Do not embed secret values",
            ]
            file_persistence = dict(persistence)
            file_test_strategy = dict(test_strategy)
            file_source = {
                "spec_source": dict(source),
                "configuration": list(configuration),
                "env": list(file_env),
            }
        elif kind == "requirements":
            responsibilities = ["declarar dependencias runtime"]
            file_dependencies = list(deps)
            must_implement = [
                f"Declare runtime dependency '{dependency}'"
                for dependency in file_dependencies
                if str(dependency or "").strip()
            ]
            must_not = [
                "Do not place import module names when a different installable package is declared"
            ]
            file_source = {
                "spec_source": dict(source),
                "technology_signals": _technology_signals_for_dependencies(
                    dependencies=file_dependencies,
                    technologies_by_name=technologies_by_name,
                ),
            }
        elif kind == "test":
            responsibilities = [
                "materializar la estrategia de tests declarada",
                "cubrir el comportamiento observable del endpoint asociado cuando exista",
            ]
            file_test_strategy = dict(test_strategy)
            related_endpoints = _endpoints_for_test_file(path=path, endpoints=endpoints)
            related_integration_refs = _dedupe_strings(
                [
                    ref
                    for endpoint in related_endpoints
                    for ref in (endpoint.get("integration_refs") or [])
                    if isinstance(ref, str)
                ]
            )
            configuration_refs = []
            for ref in related_integration_refs:
                integration = integrations_by_id.get(ref)
                if isinstance(integration, dict):
                    configuration_refs.extend(
                        [
                            conf_ref
                            for conf_ref in (integration.get("configuration_refs") or [])
                            if isinstance(conf_ref, str)
                        ]
                    )
            configuration = _resolve_configuration(
                configuration_refs=_dedupe_strings(configuration_refs),
                configuration_by_key=configuration_by_key,
            )
            must_implement = _dedupe_strings(
                ["Reflect the declared test strategy"]
                + (
                    ["Cover endpoint integration scenarios without real external side effects"]
                    if related_integration_refs
                    else []
                )
            )
            must_not = _dedupe_strings(
                ["Do not execute real integrations in hermetic tests"]
                if related_integration_refs
                else []
            )
            file_source = {
                "spec_source": dict(source),
                "endpoints": list(related_endpoints),
                "integrations": [
                    dict(integrations_by_id[ref])
                    for ref in related_integration_refs
                    if isinstance(integrations_by_id.get(ref), dict)
                ],
                "configuration": list(configuration),
            }
        elif kind == "docs":
            responsibilities = ["documentación del proyecto"]
        elif kind == "package_init":
            responsibilities = ["marcar paquete python"]
        elif kind == "service":
            responsibilities = ["módulo interno de servicio"]
        elif kind == "repository":
            responsibilities = ["módulo interno de repositorio"]
        else:
            responsibilities = ["módulo del proyecto"]
            notes.append("Contrato derivado del SPEC sin reglas específicas")

        contracts.append(
            FileContract(
                path=path,
                kind=kind,
                responsibilities=responsibilities,
                required_symbols=required_symbols,
                allowed_imports=_dedupe_strings(allowed_imports),
                forbidden_imports=forbidden_imports,
                endpoints=list(eps_for_file),
                endpoint_contracts=list(endpoint_contracts),
                dependencies=list(file_dependencies),
                env=list(file_env),
                persistence=dict(file_persistence),
                test_strategy=dict(file_test_strategy),
                source=dict(file_source),
                notes=notes,
                implementation_contracts=list(implementation_for_file),
                must_implement=list(must_implement),
                must_not=list(must_not),
                implementation_plan=list(implementation_plan),
                actions=list(actions),
                errors=list(errors),
                integration_refs=list(integration_refs),
                external_dependencies=list(external_dependencies),
                implementation_levels=list(implementation_levels),
                configuration=list(configuration),
                configuration_access=dict(configuration_access),
                authentication_constraints=dict(authentication_constraints),
                authentication_runtime_contract=dict(
                    authentication_runtime_contract
                ),
                provided_interfaces=list(provided_interfaces),
                required_internal_calls=list(required_internal_calls),
                included_routers=list(included_routers),
                routing_convention=dict(routing_convention),
                owned_action_refs=list(owned_action_refs),
                data_contracts=dict(data_contracts),
                data_flows=list(data_flows),
            )
        )

    contracts = _apply_interfaces_to_contracts(
        spec=spec,
        contracts=contracts,
        integration_path_by_ref=integration_module_by_ref,
    )

    _validate_contracts_against_spec(
        spec_files=spec_files,
        endpoints=endpoints,
        contracts=contracts,
    )
    _validate_implementation_assignment(
        endpoints=endpoints,
        implementation_contracts=implementation_contracts,
        file_contracts=contracts,
    )

    return contracts


def _endpoint_key(method: Any, path: Any) -> tuple[str, str]:
    return (str(method or "").strip().upper(), str(path or "").strip())


def _implementation_contracts_by_endpoint(
    implementation_contracts: List[Dict[str, Any]],
) -> Dict[tuple[str, str], List[Dict[str, Any]]]:
    result: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for contract in implementation_contracts:
        if not isinstance(contract, dict):
            continue
        key = _endpoint_key(contract.get("method"), contract.get("path"))
        if not key[0] or not key[1]:
            continue
        result.setdefault(key, []).append(contract)
    return result


def _dedupe_strings(values: List[Any]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _merge_dict_lists(
    groups: List[List[Dict[str, Any]]],
    *,
    identity_fields: tuple[str, ...],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[tuple[Any, ...]] = set()
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            key = tuple(item.get(field) for field in identity_fields)
            if key in seen:
                continue
            seen.add(key)
            result.append(dict(item))
    return result


def _merge_external_dependencies(
    groups: List[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[tuple[Any, ...]] = set()
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            dep_id = item.get("id")
            dep_name = item.get("name")
            key = ("id", dep_id) if dep_id is not None else ("name", dep_name)
            if key in seen:
                continue
            seen.add(key)
            result.append(dict(item))
    return result


def _configuration_by_key(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in spec.get("configuration") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        sanitized = {
            k: v
            for k, v in dict(item).items()
            if str(k) not in {"value", "secret_value"}
        }
        result[key] = sanitized
    return result


def _integrations_by_id(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in spec.get("integrations") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip()
        if not key:
            continue
        result[key] = dict(item)
    return result


def _technology_signals_by_name(
    spec: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in spec.get("technology_signals") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("name") or "").strip()
        if not key:
            continue
        result[key] = dict(item)
    return result


def _implementation_files_by_path(
    implementation_files: List[Any],
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in implementation_files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").replace("\\", "/").strip()
        if not path:
            continue
        result[path] = dict(item)
    return result


def _integration_module_by_ref(
    implementation_files: List[Any],
) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in implementation_files:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "").strip() != "integration":
            continue
        ref = str(item.get("integration_ref") or "").strip()
        path = str(item.get("path") or "").replace("\\", "/").strip()
        if ref and path:
            result[ref] = path
    return result


def _build_endpoint_responsibilities(
    *,
    base: List[str],
    implementation_contracts: List[Dict[str, Any]],
    has_dedicated_integration_module: bool,
) -> List[str]:
    responsibilities = list(base)
    responsibilities.extend(
        [
            "validar el request declarado",
            "ejecutar todas las acciones requeridas",
            "construir la respuesta declarada",
            "mapear los errores declarados",
            "no devolver éxito antes de ejecutar las acciones",
        ]
    )
    if any(
        (contract.get("integration_refs") or [])
        or (contract.get("external_dependencies") or [])
        for contract in implementation_contracts
        if isinstance(contract, dict)
    ):
        responsibilities.append("invocar las integraciones referenciadas")
    if has_dedicated_integration_module:
        responsibilities.append(
            "delegar SDK, autenticación, cliente y configuración al módulo interno de integración"
        )
    return _dedupe_strings(responsibilities)


def _import_roots_for_integrations(
    *,
    integration_refs: List[str],
    integrations_by_id: Dict[str, Dict[str, Any]],
    technologies_by_name: Dict[str, Dict[str, Any]],
) -> List[str]:
    roots: List[str] = []
    for ref in integration_refs:
        integration = integrations_by_id.get(ref)
        if not isinstance(integration, dict):
            continue
        for technology_ref in integration.get("technology_refs") or []:
            technology = technologies_by_name.get(str(technology_ref or "").strip())
            if not isinstance(technology, dict):
                continue
            import_roots = technology.get("import_roots") or []
            if not isinstance(import_roots, (list, tuple)):
                continue
            for root in import_roots:
                if isinstance(root, str) and root.strip():
                    roots.append(root.strip())
    return _dedupe_strings(roots)


def _import_roots_for_package(
    *,
    package: str,
    technologies_by_name: Dict[str, Dict[str, Any]],
) -> List[str]:
    """Localiza los `import_roots` del TechnologySignal cuyo `packages` incluye `package`.

    Es el inverso de `_import_roots_for_integrations`: en vez de partir de una integración
    referenciada por el usuario, parte de un paquete que PoC-it necesita por decisión propia de
    arquitectura (p.ej. pydantic-settings para app/core/config.py) y localiza el import_root
    correspondiente a través del TechnologySignal — nunca hardcodeando el nombre del módulo.

    Si no existe ningún TechnologySignal que declare ese package, devuelve [] (el import no se
    añade a allowed_imports; el gate DEPENDENCY_IMPORT_NOT_DECLARED se encarga de reportar esa
    ausencia como defecto de generación en vez de dejarlo para el runtime).
    """
    target = package.strip().casefold()
    roots: List[str] = []
    for technology in technologies_by_name.values():
        if not isinstance(technology, dict):
            continue
        packages = technology.get("packages") or []
        if not isinstance(packages, (list, tuple)):
            continue
        if not any(str(p or "").strip().casefold() == target for p in packages):
            continue
        for root in technology.get("import_roots") or []:
            if isinstance(root, str) and root.strip():
                roots.append(root.strip())
    return _dedupe_strings(roots)


def _missing_import_root_notes(
    *,
    integration_refs: List[str],
    integrations_by_id: Dict[str, Dict[str, Any]],
    technologies_by_name: Dict[str, Dict[str, Any]],
) -> List[str]:
    notes: List[str] = []
    for ref in integration_refs:
        integration = integrations_by_id.get(ref)
        if not isinstance(integration, dict):
            continue
        for technology_ref in integration.get("technology_refs") or []:
            technology = technologies_by_name.get(str(technology_ref or "").strip())
            if not isinstance(technology, dict):
                continue
            import_roots = technology.get("import_roots") or []
            packages = technology.get("packages") or []
            if isinstance(import_roots, (list, tuple)) and import_roots:
                continue
            if not isinstance(packages, (list, tuple)):
                continue
            for package in packages:
                package_name = str(package or "").strip()
                if not package_name:
                    continue
                notes.append(
                    f"No import root was declared for package '{package_name}'; the generated code must use only imports supported by the integration contract or standard library."
                )
    return _dedupe_strings(notes)


def _resolve_configuration(
    *,
    configuration_refs: List[str],
    configuration_by_key: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for ref in _dedupe_strings(configuration_refs):
        conf = configuration_by_key.get(ref)
        if isinstance(conf, dict):
            result.append(dict(conf))
    return result


def _dedupe_contract_dicts(
    contracts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[tuple[str, str, str]] = set()
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        key = (
            str(contract.get("method") or "").strip().upper(),
            str(contract.get("path") or "").strip(),
            str(
                contract.get("capability")
                or contract.get("capability_id")
                or contract.get("operation")
                or ""
            ).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(contract))
    return result


def _find_matching_endpoint(
    *,
    endpoints: List[Dict[str, Any]],
    method: Any,
    path: Any,
) -> Dict[str, Any] | None:
    target = _endpoint_key(method, path)
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        if _endpoint_key(endpoint.get("method"), endpoint.get("path")) == target:
            return endpoint
    return None


def _technology_signals_for_integrations(
    *,
    integration_refs: List[str],
    integrations_by_id: Dict[str, Dict[str, Any]],
    technologies_by_name: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for ref in integration_refs:
        integration = integrations_by_id.get(ref)
        if not isinstance(integration, dict):
            continue
        for technology_ref in integration.get("technology_refs") or []:
            key = str(technology_ref or "").strip()
            if not key or key in seen:
                continue
            technology = technologies_by_name.get(key)
            if not isinstance(technology, dict):
                continue
            seen.add(key)
            result.append(dict(technology))
    return result


def _technology_signals_for_dependencies(
    *,
    dependencies: List[str],
    technologies_by_name: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    normalized_dependencies = {
        str(item or "").strip() for item in dependencies if str(item or "").strip()
    }
    result: List[Dict[str, Any]] = []
    for technology in technologies_by_name.values():
        packages = technology.get("packages") or technology.get("dependency_names") or []
        if not isinstance(packages, (list, tuple)):
            continue
        if normalized_dependencies.intersection(
            {str(item or "").strip() for item in packages if str(item or "").strip()}
        ):
            result.append(dict(technology))
    return result


def _build_config_must_implement(
    configuration: List[Dict[str, Any]],
) -> List[str]:
    result: List[str] = []
    for item in configuration:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        delivery = str(
            item.get("delivery") or item.get("kind") or item.get("via") or "unspecified"
        ).strip()
        result.append(f"Declare configuration '{key}' using delivery '{delivery}'")
        if bool(item.get("required")):
            result.append(f"Expose required configuration '{key}'")
        if bool(item.get("secret")):
            result.append(
                f"Treat configuration '{key}' as secret and never hard-code it"
            )
    return _dedupe_strings(result)


def _endpoints_for_test_file(
    *,
    path: str,
    endpoints: List[Any],
) -> List[Dict[str, Any]]:
    normalized_path = str(path or "").replace("\\", "/")
    result: List[Dict[str, Any]] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        endpoint_file = str(endpoint.get("file") or "").replace("\\", "/")
        endpoint_name = endpoint_file.rsplit("/", 1)[-1].replace(".py", "")
        if endpoint_name and endpoint_name in normalized_path:
            result.append(dict(endpoint))
    return result


def _integration_external_dependencies(
    *,
    integration_ref: str,
    integration: Dict[str, Any],
    implementation_contracts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    groups: List[List[Dict[str, Any]]] = []
    for contract in implementation_contracts:
        if not isinstance(contract, dict):
            continue
        if integration_ref not in (contract.get("integration_refs") or []):
            continue
        groups.append(
            [
                item
                for item in (contract.get("external_dependencies") or [])
                if isinstance(item, dict)
            ]
        )
    if isinstance(integration, dict) and integration_ref:
        groups.append(
            [
                {
                    "id": integration_ref,
                    "name": integration.get("name") or integration_ref,
                    "kind": integration.get("kind"),
                    "configuration_refs": list(
                        _dedupe_strings(integration.get("configuration_refs") or [])
                    ),
                    "authentication": dict(integration.get("authentication") or {}),
                }
            ]
        )
    return _merge_external_dependencies(groups)


def _integration_actions_for_ref(
    *,
    integration_ref: str,
    endpoints: List[Any],
    implementation_contracts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    groups: List[List[Dict[str, Any]]] = []
    for contract in implementation_contracts:
        if not isinstance(contract, dict):
            continue
        if integration_ref not in (contract.get("integration_refs") or []):
            continue
        groups.append(
            [
                item
                for item in (contract.get("actions") or [])
                if isinstance(item, dict)
                and str(item.get("integration_ref") or "").strip() == integration_ref
            ]
        )
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        groups.append(
            [
                item
                for item in (endpoint.get("actions") or [])
                if isinstance(item, dict)
                and str(item.get("integration_ref") or "").strip() == integration_ref
            ]
        )
    return _merge_dict_lists(groups, identity_fields=("id",))


def _is_health_endpoint(endpoints: List[Dict[str, Any]]) -> bool:
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        if str(endpoint.get("path") or "").strip() == "/health":
            return True
    return False


def _infer_kind(path: str) -> str:
    p = (path or "").replace("\\", "/")
    if not p:
        return "unknown"
    if p.endswith("/__init__.py") or p == "app/__init__.py":
        return "package_init"
    if p == "requirements.txt":
        return "requirements"
    if p.lower().endswith("readme.md") or p == "README.md":
        return "docs"
    if p.startswith("tests/") or p.startswith("app/tests/"):
        return "test"
    if p.startswith("app/integrations/") and p.endswith(".py"):
        return "integration"
    if p.startswith("app/services/") and p.endswith(".py"):
        return "service"
    if p.startswith("app/repositories/") and p.endswith(".py"):
        return "repository"
    if p.startswith("app/api/endpoints/") and p.endswith(".py"):
        return "endpoint"
    if p == "app/api/router.py":
        return "router"
    if p == "app/core/config.py":
        return "config"
    if p == "app/main.py":
        return "main"
    if p.startswith("app/api/") and p.endswith(".py"):
        return "router"
    if p.startswith("app/core/") and p.endswith(".py"):
        return "config"
    if p.startswith("app/") and p.endswith(".py"):
        return "unknown"
    return "unknown"


def _apply_interfaces_to_contracts(
    *,
    spec: Dict[str, Any],
    contracts: List[FileContract],
    integration_path_by_ref: Dict[str, str],
) -> List[FileContract]:
    mutable_by_path: Dict[str, Dict[str, Any]] = {
        contract.path: {
            "path": contract.path,
            "kind": contract.kind,
            "responsibilities": list(contract.responsibilities),
            "required_symbols": list(contract.required_symbols),
            "allowed_imports": list(contract.allowed_imports),
            "forbidden_imports": list(contract.forbidden_imports),
            "endpoints": list(contract.endpoints),
            "endpoint_contracts": list(contract.endpoint_contracts),
            "dependencies": list(contract.dependencies),
            "env": list(contract.env),
            "persistence": dict(contract.persistence),
            "test_strategy": dict(contract.test_strategy),
            "source": dict(contract.source),
            "notes": list(contract.notes),
            "implementation_contracts": list(contract.implementation_contracts),
            "must_implement": list(contract.must_implement),
            "must_not": list(contract.must_not),
            "implementation_plan": list(contract.implementation_plan),
            "actions": list(contract.actions),
            "errors": list(contract.errors),
            "integration_refs": list(contract.integration_refs),
            "external_dependencies": list(contract.external_dependencies),
            "implementation_levels": list(contract.implementation_levels),
            "configuration": list(contract.configuration),
            "configuration_access": dict(contract.configuration_access),
            "authentication_constraints": dict(contract.authentication_constraints),
            "authentication_runtime_contract": dict(
                contract.authentication_runtime_contract
            ),
            "provided_interfaces": list(contract.provided_interfaces),
            "required_internal_calls": list(contract.required_internal_calls),
            "included_routers": list(contract.included_routers),
            "routing_convention": dict(contract.routing_convention),
            "owned_action_refs": list(contract.owned_action_refs),
            "data_contracts": dict(contract.data_contracts),
            "data_flows": list(contract.data_flows),
        }
        for contract in contracts
    }

    endpoint_paths_with_repository = {
        str(endpoint.get("file") or "").replace("\\", "/")
        for endpoint in (spec.get("endpoints") or [])
        if isinstance(endpoint, dict)
        and any(
            str(path or "").replace("\\", "/").startswith("app/repositories/")
            for path in (spec.get("files") or [])
        )
    }

    for endpoint in spec.get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        endpoint_path = str(endpoint.get("file") or "").replace("\\", "/").strip()
        if endpoint_path not in mutable_by_path:
            continue

        symbol_by_action_ref: Dict[str, str] = {}
        for action in endpoint.get("actions") or []:
            if not isinstance(action, dict):
                continue
            if not bool(action.get("required", True)):
                continue
            action_id = str(action.get("id") or "").strip()
            if not action_id:
                continue
            symbol = _symbol_for_action(action)
            other_action_ref = next(
                (
                    ref
                    for ref, existing_symbol in symbol_by_action_ref.items()
                    if existing_symbol == symbol and ref != action_id
                ),
                None,
            )
            if other_action_ref is not None:
                raise ValueError(
                    "FILE_INTERFACE_SYMBOL_COLLISION: "
                    f"path={endpoint_path} symbol={symbol} actions={other_action_ref},{action_id}"
                )
            symbol_by_action_ref[action_id] = symbol

        endpoint_data_contracts = _data_contracts_for_endpoint(endpoint)
        if endpoint_data_contracts:
            existing = mutable_by_path[endpoint_path].get("data_contracts") or {}
            existing.update(endpoint_data_contracts)
            mutable_by_path[endpoint_path]["data_contracts"] = existing

        for action in endpoint.get("actions") or []:
            if not isinstance(action, dict):
                continue
            if not bool(action.get("required", True)):
                continue

            action_id = str(action.get("id") or "").strip()
            if not action_id:
                continue

            owner_path = _resolve_action_owner_path(
                action=action,
                endpoint_file=endpoint_path,
                integration_path_by_ref=integration_path_by_ref,
                repository_paths=[
                    path
                    for path in mutable_by_path
                    if path.startswith("app/repositories/")
                ]
                if endpoint_path in endpoint_paths_with_repository
                else [],
            )
            symbol = symbol_by_action_ref[action_id]
            owner_contract = mutable_by_path.get(owner_path)
            if owner_contract is None:
                continue

            data_contract_ref = _data_contract_ref_for_action(action)
            owner_contract["owned_action_refs"] = _dedupe_strings(
                list(owner_contract.get("owned_action_refs") or []) + [action_id]
            )
            interface_required = owner_path != endpoint_path
            owner_contract["provided_interfaces"].append(
                {
                    "symbol": symbol,
                    "kind": "function",
                    "action_ref": action.get("id"),
                    "parameters": _parameters_for_action(
                        action=action,
                        endpoint=endpoint,
                    ),
                    "returns": _returns_for_action(
                        action=action,
                        endpoint=endpoint,
                    ),
                    "returns_ref": data_contract_ref or "",
                    "return_hint": _return_hint_for_action(action),
                    "purpose": str(action.get("description") or "").strip(),
                    "interface_ref": f"{module_path_from_file_path(owner_path)}:{symbol}",
                    "interface_required": interface_required,
                    "owner_path": owner_path,
                    "consumer_path": endpoint_path,
                }
            )

            if owner_path == endpoint_path:
                continue

            endpoint_contract = mutable_by_path[endpoint_path]
            endpoint_contract["required_internal_calls"].append(
                {
                    "module": module_path_from_file_path(owner_path),
                    "symbol": symbol,
                    "action_ref": action.get("id"),
                    "required": action.get("required", True),
                    "interface_ref": f"{module_path_from_file_path(owner_path)}:{symbol}",
                    "parameters": _parameters_for_action(
                        action=action,
                        endpoint=endpoint,
                    ),
                    "arguments_ref": data_contract_ref or "",
                }
            )
            endpoint_contract["allowed_imports"] = _dedupe_strings(
                list(endpoint_contract.get("allowed_imports") or [])
                + [module_path_from_file_path(owner_path)]
            )
    for contract in mutable_by_path.values():
        contract["provided_interfaces"] = _dedupe_interface_items(
            contract.get("provided_interfaces") or []
        )
        contract["required_internal_calls"] = _dedupe_internal_call_items(
            contract.get("required_internal_calls") or []
        )
        provided_symbols = [
            str(item.get("symbol") or "").strip()
            for item in (contract.get("provided_interfaces") or [])
            if isinstance(item, dict)
            and bool(item.get("interface_required"))
            and str(item.get("symbol") or "").strip()
        ]
        contract["required_symbols"] = _dedupe_strings(
            list(contract.get("required_symbols") or []) + provided_symbols
        )
        contract["allowed_imports"] = _dedupe_strings(contract.get("allowed_imports") or [])
        contract["owned_action_refs"] = _dedupe_strings(contract.get("owned_action_refs") or [])

    result: List[FileContract] = []
    for original in contracts:
        updated = mutable_by_path[original.path]
        result.append(
            FileContract(
                path=updated["path"],
                kind=updated["kind"],
                responsibilities=list(updated["responsibilities"]),
                required_symbols=list(updated["required_symbols"]),
                allowed_imports=list(updated["allowed_imports"]),
                forbidden_imports=list(updated["forbidden_imports"]),
                endpoints=list(updated["endpoints"]),
                endpoint_contracts=list(updated["endpoint_contracts"]),
                dependencies=list(updated["dependencies"]),
                env=list(updated["env"]),
                persistence=dict(updated["persistence"]),
                test_strategy=dict(updated["test_strategy"]),
                source=dict(updated["source"]),
                notes=list(updated["notes"]),
                implementation_contracts=list(updated["implementation_contracts"]),
                must_implement=list(updated["must_implement"]),
                must_not=list(updated["must_not"]),
                implementation_plan=list(updated["implementation_plan"]),
                actions=list(updated["actions"]),
                errors=list(updated["errors"]),
                integration_refs=list(updated["integration_refs"]),
                external_dependencies=list(updated["external_dependencies"]),
                implementation_levels=list(updated["implementation_levels"]),
                configuration=list(updated["configuration"]),
                configuration_access=dict(updated["configuration_access"]),
                authentication_constraints=dict(updated["authentication_constraints"]),
                authentication_runtime_contract=dict(
                    updated["authentication_runtime_contract"]
                ),
                provided_interfaces=list(updated["provided_interfaces"]),
                required_internal_calls=list(updated["required_internal_calls"]),
                included_routers=list(updated["included_routers"]),
                routing_convention=dict(updated["routing_convention"]),
                owned_action_refs=list(updated["owned_action_refs"]),
                data_contracts=dict(updated["data_contracts"]),
                data_flows=list(updated.get("data_flows") or []),
            )
        )
    return result


def _symbol_for_action(action: Dict[str, Any]) -> str:
    action_id = action.get("id")
    return safe_python_identifier(
        action_id,
        fallback="execute_action",
    )


def _resolve_action_owner_path(
    *,
    action: Dict[str, Any],
    endpoint_file: str,
    integration_path_by_ref: Dict[str, str],
    repository_paths: List[str] | None = None,
) -> str:
    kind = str(action.get("kind") or "").strip()
    integration_ref = str(action.get("integration_ref") or "").strip()

    if kind in {"external_call", "notification"}:
        integration_path = integration_path_by_ref.get(integration_ref)
        if integration_path:
            return integration_path

    if kind == "persistence":
        repository_paths = repository_paths or []
        if repository_paths:
            return sorted(repository_paths)[0]

    return endpoint_file


def _parameters_for_action(
    *,
    action: Dict[str, Any],
    endpoint: Dict[str, Any],
) -> List[Dict[str, Any]]:
    request = endpoint.get("request") or {}
    request_type = str(request.get("type") or "").strip().lower()
    schema = request.get("schema") or request.get("schema_hint") or {}

    if request_type == "none" and not schema:
        return []

    result: List[Dict[str, Any]] = []

    if isinstance(schema, dict):
        for name, definition in schema.items():
            lowered = str(name or "").strip().lower()
            if any(
                token in lowered
                for token in ("config", "secret", "credential", "token", "password")
            ):
                continue
            identifier = safe_python_identifier(
                name,
                fallback="value",
            )
            result.append(
                {
                    "name": identifier,
                    "required": True,
                    "type": _schema_type_name(definition),
                }
            )

    if not result:
        result.append(
            {
                "name": "payload",
                "required": True,
                "type": "object",
            }
        )

    return result


def _returns_for_action(
    *,
    action: Dict[str, Any],
    endpoint: Dict[str, Any],
) -> Dict[str, Any]:
    explicit_output = (
        action.get("output_contract")
        or action.get("returns")
        or action.get("return_contract")
        or action.get("output_shape")
    )
    if isinstance(explicit_output, dict):
        return _normalize_shape_contract(explicit_output)

    data_contract_ref = _data_contract_ref_for_action(action)
    endpoint_data_contracts = _data_contracts_for_endpoint(endpoint)
    if data_contract_ref:
        referenced_contract = endpoint_data_contracts.get(data_contract_ref)
        if isinstance(referenced_contract, dict):
            return _normalize_shape_contract(referenced_contract)

    return {"kind": "unknown"}


def _return_hint_for_action(action: Dict[str, Any]) -> str:
    kind = str(action.get("kind") or "").strip()
    if kind in {"validation", "internal_processing", "transformation"}:
        return "dict"
    if kind in {"external_call", "notification", "persistence"}:
        return "dict"
    return "Any"


def _dedupe_interface_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[tuple[str, str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("symbol") or "").strip(),
            str(item.get("kind") or "").strip(),
            str(item.get("action_ref") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def _dedupe_internal_call_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[tuple[str, str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("module") or "").strip(),
            str(item.get("symbol") or "").strip(),
            str(item.get("action_ref") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def _endpoint_contract_from_endpoint(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    request = endpoint.get("request") or {}
    schema = request.get("schema") or request.get("schema_hint") or {}
    return {
        "method": str(endpoint.get("method") or "").strip().upper(),
        "path": str(endpoint.get("path") or "").strip(),
        "request_type": str(request.get("type") or "none").strip().lower(),
        "request_schema": dict(schema) if isinstance(schema, dict) else {},
        "request": dict(request) if isinstance(request, dict) else {},
        "response": dict(endpoint.get("response") or {})
        if isinstance(endpoint.get("response"), dict)
        else {},
        "errors": [
            dict(item)
            for item in (endpoint.get("errors") or [])
            if isinstance(item, dict)
        ],
        "integration_refs": [
            str(item).strip()
            for item in (endpoint.get("integration_refs") or [])
            if str(item).strip()
        ],
        "configuration_refs": _endpoint_configuration_refs(endpoint),
    }


def _build_configuration_access(
    *,
    path: str,
    kind: str,
    configuration: List[Dict[str, Any]],
) -> Dict[str, Any]:
    allowed_fields = [
        str(item.get("key") or "").strip()
        for item in configuration
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    ]
    if not allowed_fields:
        return {}
    return {
        "provider_module": "app.core.config",
        "provider_symbol": "get_settings",
        "allowed_fields": allowed_fields,
        "access_mode": "lazy" if kind in {"endpoint", "integration", "service", "repository"} else "direct",
        "consumer_path": path,
    }


def _build_authentication_constraints(
    authentication: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(authentication, dict) or not authentication:
        return {}
    return {
        "credential_source": str(authentication.get("credential_source") or "").strip()
        or "unknown",
        "allows_embedded_secret": bool(authentication.get("allows_embedded_secret")),
        "allows_static_credential_file": bool(
            authentication.get("allows_static_credential_file", True)
        ),
    }


def _build_authentication_runtime_contract(
    authentication: Dict[str, Any] | None,
) -> Dict[str, Any]:
    if not isinstance(authentication, dict):
        return {}

    credential_source = str(authentication.get("credential_source") or "").strip()
    allows_static_file = bool(
        authentication.get("allows_static_credential_file", True)
    )
    allows_embedded_secret = bool(authentication.get("allows_embedded_secret", True))

    if not credential_source:
        return {}

    return {
        "discovery": (
            "ambient" if credential_source == "runtime" else "contract_defined"
        ),
        "requires_configuration_field": (
            False if credential_source == "runtime" else None
        ),
        "requires_static_credential_file": allows_static_file,
        "requires_embedded_secret": allows_embedded_secret,
    }


def _endpoint_configuration_refs(endpoint: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    request = endpoint.get("request") or {}
    for key in ("configuration_refs", "config_refs"):
        for ref in request.get(key) or []:
            normalized = str(ref or "").strip()
            if normalized:
                refs.append(normalized)
    for key in ("configuration_refs", "config_refs"):
        for ref in endpoint.get(key) or []:
            normalized = str(ref or "").strip()
            if normalized:
                refs.append(normalized)
    return _dedupe_strings(refs)


def _schema_type_name(definition: Any) -> str:
    if isinstance(definition, dict):
        candidate = (
            definition.get("type")
            or definition.get("format")
            or definition.get("kind")
            or "object"
        )
        return str(candidate or "object").strip()
    if isinstance(definition, str):
        normalized = definition.strip()
        return normalized or "string"
    return "string"


def _data_contract_ref_for_action(action: Dict[str, Any]) -> str:
    explicit = str(action.get("data_contract_ref") or "").strip()
    if explicit:
        return explicit
    kind = str(action.get("kind") or "").strip()
    if kind in {"internal_processing", "transformation", "persistence", "external_call", "notification"}:
        action_id = str(action.get("id") or "").strip()
        if action_id:
            return f"{safe_python_identifier(action_id, fallback='data')}_payload"
    return ""


def _data_contracts_for_endpoint(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    request_contract = _endpoint_request_data_contract(endpoint)
    if request_contract:
        result["request_payload"] = request_contract

    for action in endpoint.get("actions") or []:
        if not isinstance(action, dict) or not bool(action.get("required", True)):
            continue
        ref = _data_contract_ref_for_action(action)
        explicit_contract = (
            action.get("input_contract")
            or action.get("output_contract")
            or action.get("data_contract")
        )
        if ref and isinstance(explicit_contract, dict):
            result[ref] = _normalize_shape_contract(explicit_contract)
    return result


def _endpoint_request_data_contract(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    request = endpoint.get("request") or {}
    schema = request.get("schema") or request.get("schema_hint") or {}
    if not isinstance(schema, dict) or not schema:
        return {}
    return {
        "kind": "object",
        "fields": {
            safe_python_identifier(name, fallback="value"): _schema_type_name(definition)
            for name, definition in schema.items()
        },
    }


def _normalize_shape_contract(contract: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(contract.get("kind") or contract.get("type") or "unknown").strip().lower() or "unknown"
    normalized: Dict[str, Any] = {"kind": kind}
    fields = contract.get("fields")
    if isinstance(fields, dict):
        normalized["fields"] = {
            str(name).strip(): str(field_type).strip().lower()
            for name, field_type in fields.items()
            if str(name).strip() and str(field_type).strip()
        }
    return normalized


def _validate_contracts_against_spec(
    *,
    spec_files: List[str],
    endpoints: List[Any],
    contracts: List[FileContract],
) -> None:
    c_paths = [c.path for c in contracts]
    if set(c_paths) != set(spec_files):
        missing = sorted(list(set(spec_files) - set(c_paths)))
        extra = sorted(list(set(c_paths) - set(spec_files)))
        raise ValueError(f"file contracts mismatch. missing={missing} extra={extra}")
    if len(c_paths) != len(set(c_paths)):
        raise ValueError("duplicate file contracts by path")

    ep_seen = 0
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        ep_file = str(ep.get("file") or "").replace("\\", "/")
        if not ep_file:
            continue
        if ep_file not in spec_files:
            raise ValueError(f"endpoint.file not present in spec.files: {ep_file}")

        owners = [
            c
            for c in contracts
            if c.kind == "endpoint" and c.path == ep_file and ep in c.endpoints
        ]
        if len(owners) != 1:
            raise ValueError(
                f"endpoint not owned by exactly 1 file contract: file={ep_file}"
            )
        ep_seen += 1
        fn = ep.get("func") or ep.get("function") or ep.get("handler")
        if isinstance(fn, str) and fn.strip():
            if fn.strip() not in owners[0].required_symbols:
                raise ValueError(
                    f"endpoint func not in required_symbols: {fn} file={ep_file}"
                )

    ep_expected = len(
        [ep for ep in endpoints if isinstance(ep, dict) and (ep.get("file") or "")]
    )
    if ep_seen != ep_expected:
        raise ValueError("orphan endpoints detected")


def _validate_implementation_assignment(
    *,
    endpoints: List[Any],
    implementation_contracts: List[Dict[str, Any]],
    file_contracts: List[FileContract],
) -> None:
    if not implementation_contracts:
        return

    endpoint_contracts = [
        contract for contract in file_contracts if contract.kind == "endpoint"
    ]
    endpoint_keys_by_file = {
        contract.path: {
            _endpoint_key(endpoint.get("method"), endpoint.get("path"))
            for endpoint in contract.endpoints
            if isinstance(endpoint, dict)
        }
        for contract in endpoint_contracts
    }

    valid_endpoint_keys = {
        _endpoint_key(endpoint.get("method"), endpoint.get("path"))
        for endpoint in endpoints
        if isinstance(endpoint, dict)
        and _endpoint_key(endpoint.get("method"), endpoint.get("path"))[0]
        and _endpoint_key(endpoint.get("method"), endpoint.get("path"))[1]
    }

    owners_by_contract_key: Dict[tuple[str, str, str], List[FileContract]] = {}

    for contract in implementation_contracts:
        if not isinstance(contract, dict):
            continue
        key = _endpoint_key(contract.get("method"), contract.get("path"))
        if not key[0] or not key[1]:
            continue
        if key not in valid_endpoint_keys:
            raise ValueError(
                f"implementation contract endpoint not present in spec.endpoints: method={key[0]} path={key[1]}"
            )
        owners = [
            file_contract
            for file_contract in endpoint_contracts
            if key in endpoint_keys_by_file.get(file_contract.path, set())
            and any(
                _endpoint_key(item.get("method"), item.get("path")) == key
                for item in file_contract.implementation_contracts
                if isinstance(item, dict)
            )
        ]
        if len(owners) != 1:
            raise ValueError(
                f"implementation contract not owned by exactly 1 endpoint file contract: method={key[0]} path={key[1]}"
            )

        owner = owners[0]
        contract_identity = (
            key[0],
            key[1],
            str(
                contract.get("capability")
                or contract.get("capability_id")
                or contract.get("operation")
                or json.dumps(contract, ensure_ascii=False, sort_keys=True)
            ),
        )
        owners_by_contract_key.setdefault(contract_identity, []).append(owner)

    for contract_identity, owners in owners_by_contract_key.items():
        if len(owners) != 1:
            raise ValueError(
                "implementation contract assigned to multiple file contracts: "
                f"method={contract_identity[0]} path={contract_identity[1]}"
            )
