from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


class OpenAPIInspectorError(ValueError):
    pass


@dataclass(frozen=True)
class SchemaRef:
    ref: str


@dataclass(frozen=True)
class SchemaNode:
    type: Optional[str] = None
    format: Optional[str] = None
    nullable: bool = False
    enum: List[Any] = field(default_factory=list)
    required: List[str] = field(default_factory=list)
    properties: Dict[str, "SchemaNode"] = field(default_factory=dict)
    items: Optional["SchemaNode"] = None
    all_of: List["SchemaNode"] = field(default_factory=list)
    one_of: List["SchemaNode"] = field(default_factory=list)
    any_of: List["SchemaNode"] = field(default_factory=list)
    ref: Optional[str] = None
    additional_properties: Optional["SchemaNode"] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParameterContract:
    name: str
    location: str
    required: bool
    schema: SchemaNode
    deprecated: bool = False
    description: Optional[str] = None


@dataclass(frozen=True)
class RequestBodyContract:
    required: bool
    content: Dict[str, SchemaNode] = field(default_factory=dict)


@dataclass(frozen=True)
class ResponseContract:
    status_code: str
    content: Dict[str, SchemaNode] = field(default_factory=dict)
    headers: Dict[str, SchemaNode] = field(default_factory=dict)
    description: Optional[str] = None


@dataclass(frozen=True)
class EndpointContract:
    operation_id: str
    method: str
    path: str
    path_params: List[ParameterContract] = field(default_factory=list)
    query_params: List[ParameterContract] = field(default_factory=list)
    headers: List[ParameterContract] = field(default_factory=list)
    cookies: List[ParameterContract] = field(default_factory=list)
    request_body: Optional[RequestBodyContract] = None
    content_types: List[str] = field(default_factory=list)
    response_schemas: Dict[str, ResponseContract] = field(default_factory=dict)
    status_codes: List[str] = field(default_factory=list)
    security_schemes: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    deprecated: bool = False
    component_refs: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class FastAPIInspectionReport:
    endpoints: List[EndpointContract] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def inspect_fastapi_openapi(
    *,
    openapi: Dict[str, Any],
    runtime_contracts: Optional[Dict[str, Any]] = None,
    max_ref_depth: int = 8,
) -> FastAPIInspectionReport:
    errors: List[str] = []
    if not isinstance(openapi, dict):
        raise OpenAPIInspectorError("OpenAPI document must be a dict")
    if not isinstance(openapi.get("paths"), dict):
        raise OpenAPIInspectorError("OpenAPI document must define a 'paths' object")

    endpoints: List[EndpointContract] = []
    runtime_by_key = _runtime_endpoints_by_key(runtime_contracts)

    for path, path_item in openapi.get("paths", {}).items():
        if not isinstance(path, str):
            errors.append(f"Invalid path entry: {path!r}")
            continue
        if not isinstance(path_item, dict):
            errors.append(f"Path item for {path} must be an object")
            continue

        shared_parameters = _resolve_parameter_list(
            path_item.get("parameters"),
            openapi=openapi,
            max_ref_depth=max_ref_depth,
            errors=errors,
            context=f"path:{path}:parameters",
        )

        for method, operation in path_item.items():
            if method == "parameters":
                continue
            if str(method).lower() not in _HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                errors.append(f"Operation {method.upper()} {path} must be an object")
                continue

            try:
                endpoints.append(
                    _build_endpoint_contract(
                        openapi=openapi,
                        path=path,
                        method=str(method).upper(),
                        operation=operation,
                        shared_parameters=shared_parameters,
                        runtime_endpoint=runtime_by_key.get((str(method).upper(), path)),
                        max_ref_depth=max_ref_depth,
                    )
                )
            except OpenAPIInspectorError as exc:
                errors.append(f"{method.upper()} {path}: {exc}")

    return FastAPIInspectionReport(endpoints=endpoints, errors=errors)


def _runtime_endpoints_by_key(runtime_contracts: Optional[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if not isinstance(runtime_contracts, dict):
        return out
    endpoints = runtime_contracts.get("endpoints")
    if not isinstance(endpoints, list):
        return out
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        method = str(endpoint.get("method") or "").upper().strip()
        path = str(endpoint.get("path") or "").strip()
        if method and path:
            out[(method, path)] = endpoint
    return out


def _build_endpoint_contract(
    *,
    openapi: Dict[str, Any],
    path: str,
    method: str,
    operation: Dict[str, Any],
    shared_parameters: List[ParameterContract],
    runtime_endpoint: Optional[Dict[str, Any]],
    max_ref_depth: int,
) -> EndpointContract:
    parameters = list(shared_parameters) + _resolve_parameter_list(
        operation.get("parameters"),
        openapi=openapi,
        max_ref_depth=max_ref_depth,
        errors=[],
        context=f"operation:{method}:{path}:parameters",
    )

    deduped_parameters: List[ParameterContract] = []
    seen_keys: Set[Tuple[str, str]] = set()
    for parameter in parameters:
        pkey = (parameter.location, parameter.name)
        if pkey not in seen_keys:
            seen_keys.add(pkey)
            deduped_parameters.append(parameter)

    path_params = [p for p in deduped_parameters if p.location == "path"]
    query_params = [p for p in deduped_parameters if p.location == "query"]
    headers = [p for p in deduped_parameters if p.location == "header"]
    cookies = [p for p in deduped_parameters if p.location == "cookie"]

    request_body = _resolve_request_body(
        operation.get("requestBody"),
        openapi=openapi,
        max_ref_depth=max_ref_depth,
    )

    responses = _resolve_responses(
        operation.get("responses"),
        openapi=openapi,
        max_ref_depth=max_ref_depth,
    )

    operation_id = str(operation.get("operationId") or "").strip() or f"{method.lower()}:{path}"
    tags = [str(x) for x in (operation.get("tags") or []) if str(x).strip()]
    deprecated = bool(operation.get("deprecated", False))
    security = operation.get("security") or []
    security_schemes = _extract_security_scheme_names(security)
    if isinstance(runtime_endpoint, dict):
        for scheme in runtime_endpoint.get("security_schemes") or []:
            s = str(scheme).strip()
            if s and s not in security_schemes:
                security_schemes.append(s)

    content_types = sorted(list(request_body.content.keys())) if request_body else []
    component_refs = sorted(
        _collect_component_refs_from_operation(
            operation=operation,
            request_body=request_body,
            responses=responses,
            parameters=deduped_parameters,
        )
    )

    return EndpointContract(
        operation_id=operation_id,
        method=method,
        path=path,
        path_params=path_params,
        query_params=query_params,
        headers=headers,
        cookies=cookies,
        request_body=request_body,
        content_types=content_types,
        response_schemas=responses,
        status_codes=list(responses.keys()),
        security_schemes=security_schemes,
        tags=tags,
        deprecated=deprecated,
        component_refs=component_refs,
    )


def _resolve_parameter_list(
    value: Any,
    *,
    openapi: Dict[str, Any],
    max_ref_depth: int,
    errors: List[str],
    context: str,
) -> List[ParameterContract]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{context} must be a list")
        return []

    out: List[ParameterContract] = []
    for idx, item in enumerate(value):
        try:
            param = _resolve_parameter(
                item,
                openapi=openapi,
                max_ref_depth=max_ref_depth,
                context=f"{context}[{idx}]",
            )
            out.append(param)
        except OpenAPIInspectorError as exc:
            errors.append(str(exc))
    return out


def _resolve_parameter(
    value: Any,
    *,
    openapi: Dict[str, Any],
    max_ref_depth: int,
    context: str,
) -> ParameterContract:
    resolved = _resolve_refs(value, openapi=openapi, max_ref_depth=max_ref_depth, seen_refs=())
    if not isinstance(resolved, dict):
        raise OpenAPIInspectorError(f"{context} parameter must resolve to an object")
    name = str(resolved.get("name") or "").strip()
    location = str(resolved.get("in") or "").strip()
    if not name or location not in {"path", "query", "header", "cookie"}:
        raise OpenAPIInspectorError(f"{context} parameter must define valid name and location")

    schema_source = resolved.get("schema")
    if not isinstance(schema_source, dict):
        raise OpenAPIInspectorError(f"{context} parameter must define schema")
    schema = _resolve_schema_node(
        schema_source,
        openapi=openapi,
        max_ref_depth=max_ref_depth,
        seen_refs=(),
    )

    return ParameterContract(
        name=name,
        location=location,
        required=bool(resolved.get("required", location == "path")),
        schema=schema,
        deprecated=bool(resolved.get("deprecated", False)),
        description=str(resolved.get("description")) if resolved.get("description") is not None else None,
    )


def _resolve_request_body(
    value: Any,
    *,
    openapi: Dict[str, Any],
    max_ref_depth: int,
) -> Optional[RequestBodyContract]:
    if value is None:
        return None
    resolved = _resolve_refs(value, openapi=openapi, max_ref_depth=max_ref_depth, seen_refs=())
    if not isinstance(resolved, dict):
        raise OpenAPIInspectorError("requestBody must resolve to an object")
    content = resolved.get("content")
    if not isinstance(content, dict):
        return RequestBodyContract(required=bool(resolved.get("required", False)), content={})

    resolved_content: Dict[str, SchemaNode] = {}
    for media_type, media_object in content.items():
        if not isinstance(media_type, str):
            continue
        if not isinstance(media_object, dict):
            raise OpenAPIInspectorError(f"requestBody content[{media_type}] must be an object")
        schema_source = media_object.get("schema") or {}
        if not isinstance(schema_source, dict):
            raise OpenAPIInspectorError(f"requestBody content[{media_type}] must define schema")
        resolved_content[media_type] = _resolve_schema_node(
            schema_source,
            openapi=openapi,
            max_ref_depth=max_ref_depth,
            seen_refs=(),
        )
    return RequestBodyContract(required=bool(resolved.get("required", False)), content=resolved_content)


def _resolve_responses(
    value: Any,
    *,
    openapi: Dict[str, Any],
    max_ref_depth: int,
) -> Dict[str, ResponseContract]:
    if not isinstance(value, dict):
        raise OpenAPIInspectorError("responses must be an object")

    out: Dict[str, ResponseContract] = {}
    for status_code, response in value.items():
        code = str(status_code).strip()
        resolved = _resolve_refs(response, openapi=openapi, max_ref_depth=max_ref_depth, seen_refs=())
        if not isinstance(resolved, dict):
            raise OpenAPIInspectorError(f"response {code} must resolve to an object")

        content: Dict[str, SchemaNode] = {}
        for media_type, media_object in (resolved.get("content") or {}).items():
            if not isinstance(media_type, str):
                continue
            if not isinstance(media_object, dict):
                raise OpenAPIInspectorError(f"response {code} content[{media_type}] must be an object")
            schema_source = media_object.get("schema") or {}
            if not isinstance(schema_source, dict):
                raise OpenAPIInspectorError(f"response {code} content[{media_type}] must define schema")
            content[media_type] = _resolve_schema_node(
                schema_source,
                openapi=openapi,
                max_ref_depth=max_ref_depth,
                seen_refs=(),
            )

        headers: Dict[str, SchemaNode] = {}
        for header_name, header_object in (resolved.get("headers") or {}).items():
            if not isinstance(header_name, str):
                continue
            header_resolved = _resolve_refs(header_object, openapi=openapi, max_ref_depth=max_ref_depth, seen_refs=())
            if not isinstance(header_resolved, dict):
                raise OpenAPIInspectorError(f"response {code} header {header_name} must resolve to object")
            schema_source = header_resolved.get("schema") or {}
            if not isinstance(schema_source, dict):
                raise OpenAPIInspectorError(f"response {code} header {header_name} must define schema")
            headers[header_name] = _resolve_schema_node(
                schema_source,
                openapi=openapi,
                max_ref_depth=max_ref_depth,
                seen_refs=(),
            )

        out[code] = ResponseContract(
            status_code=code,
            content=content,
            headers=headers,
            description=str(resolved.get("description")) if resolved.get("description") is not None else None,
        )
    return out


def _resolve_schema_node(
    value: Any,
    *,
    openapi: Dict[str, Any],
    max_ref_depth: int,
    seen_refs: Tuple[str, ...],
) -> SchemaNode:
    resolved = _resolve_refs(value, openapi=openapi, max_ref_depth=max_ref_depth, seen_refs=seen_refs)
    if not isinstance(resolved, dict):
        raise OpenAPIInspectorError("schema must resolve to an object")

    properties: Dict[str, SchemaNode] = {}
    for prop_name, prop_schema in (resolved.get("properties") or {}).items():
        if not isinstance(prop_name, str):
            continue
        properties[prop_name] = _resolve_schema_node(
            prop_schema,
            openapi=openapi,
            max_ref_depth=max_ref_depth,
            seen_refs=seen_refs,
        )

    items = None
    if isinstance(resolved.get("items"), dict):
        items = _resolve_schema_node(
            resolved.get("items"),
            openapi=openapi,
            max_ref_depth=max_ref_depth,
            seen_refs=seen_refs,
        )

    additional_properties = None
    if isinstance(resolved.get("additionalProperties"), dict):
        additional_properties = _resolve_schema_node(
            resolved.get("additionalProperties"),
            openapi=openapi,
            max_ref_depth=max_ref_depth,
            seen_refs=seen_refs,
        )

    all_of = [
        _resolve_schema_node(item, openapi=openapi, max_ref_depth=max_ref_depth, seen_refs=seen_refs)
        for item in (resolved.get("allOf") or [])
        if isinstance(item, dict)
    ]
    one_of = [
        _resolve_schema_node(item, openapi=openapi, max_ref_depth=max_ref_depth, seen_refs=seen_refs)
        for item in (resolved.get("oneOf") or [])
        if isinstance(item, dict)
    ]
    any_of = [
        _resolve_schema_node(item, openapi=openapi, max_ref_depth=max_ref_depth, seen_refs=seen_refs)
        for item in (resolved.get("anyOf") or [])
        if isinstance(item, dict)
    ]

    return SchemaNode(
        type=str(resolved.get("type")) if resolved.get("type") is not None else None,
        format=str(resolved.get("format")) if resolved.get("format") is not None else None,
        nullable=bool(resolved.get("nullable", False)),
        enum=list(resolved.get("enum") or []),
        required=[str(x) for x in (resolved.get("required") or []) if str(x).strip()],
        properties=properties,
        items=items,
        all_of=all_of,
        one_of=one_of,
        any_of=any_of,
        ref=str(resolved.get("x-origin-ref")) if resolved.get("x-origin-ref") is not None else None,
        additional_properties=additional_properties,
        raw=resolved,
    )


def _resolve_refs(
    value: Any,
    *,
    openapi: Dict[str, Any],
    max_ref_depth: int,
    seen_refs: Tuple[str, ...],
) -> Any:
    if not isinstance(value, dict):
        return value
    ref = value.get("$ref")
    if not isinstance(ref, str):
        return value
    if len(seen_refs) >= max_ref_depth:
        raise OpenAPIInspectorError(f"Max $ref depth exceeded while resolving {ref}")
    if ref in seen_refs:
        raise OpenAPIInspectorError(f"Cyclic $ref detected while resolving {ref}")

    target = _resolve_pointer(openapi, ref)
    if not isinstance(target, dict):
        raise OpenAPIInspectorError(f"$ref target is not an object: {ref}")

    merged = dict(target)
    for key, val in value.items():
        if key != "$ref":
            merged[key] = val
    merged["x-origin-ref"] = ref
    return _resolve_refs(merged, openapi=openapi, max_ref_depth=max_ref_depth, seen_refs=seen_refs + (ref,))


def _resolve_pointer(document: Dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise OpenAPIInspectorError(f"Only local refs are supported: {ref}")
    current: Any = document
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise OpenAPIInspectorError(f"Unresolvable $ref: {ref}")
        current = current[part]
    return current


def _extract_security_scheme_names(security: Any) -> List[str]:
    if not isinstance(security, list):
        return []
    out: List[str] = []
    for item in security:
        if not isinstance(item, dict):
            continue
        for key in item.keys():
            name = str(key).strip()
            if name and name not in out:
                out.append(name)
    return out


def _collect_component_refs_from_operation(
    *,
    operation: Dict[str, Any],
    request_body: Optional[RequestBodyContract],
    responses: Dict[str, ResponseContract],
    parameters: List[ParameterContract],
) -> Set[str]:
    refs: Set[str] = set()

    def visit_schema(schema: SchemaNode) -> None:
        if schema.ref:
            refs.add(schema.ref)
        for child in schema.properties.values():
            visit_schema(child)
        if schema.items:
            visit_schema(schema.items)
        if schema.additional_properties:
            visit_schema(schema.additional_properties)
        for child in schema.all_of:
            visit_schema(child)
        for child in schema.one_of:
            visit_schema(child)
        for child in schema.any_of:
            visit_schema(child)

    for parameter in parameters:
        visit_schema(parameter.schema)
    if request_body:
        for schema in request_body.content.values():
            visit_schema(schema)
    for response in responses.values():
        for schema in response.content.values():
            visit_schema(schema)
        for schema in response.headers.values():
            visit_schema(schema)

    for section_name in ("requestBody", "responses", "parameters"):
        section = operation.get(section_name)
        if isinstance(section, dict):
            _collect_raw_refs(section, refs)
        elif isinstance(section, list):
            for item in section:
                _collect_raw_refs(item, refs)
    return refs


def _collect_raw_refs(value: Any, refs: Set[str]) -> None:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            refs.add(ref)
        for child in value.values():
            _collect_raw_refs(child, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_raw_refs(item, refs)
