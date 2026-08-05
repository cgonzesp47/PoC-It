from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Compat label (legacy-ish). Kept to avoid breaking downstream consumers.
Capability = str
InteractionKind = str

_INTERACTION_KINDS: set[str] = {
    "file_io",
    "persistence",
    "external_integration",
    "messaging",
    "authentication",
    "validation",
    "data_processing",
    "internal_processing",
    "cache",
    "unknown",
}

_CAPABILITIES: set[str] = {
    "health_check",
    "create_resource",
    "list_resource",
    "list_resources",
    "get_resource",
    "update_resource",
    "delete_resource",
    "upload_file",
    "download_file",
    "process_file",
    "call_external_api",
    "receive_webhook",
    "publish_event",
    "consume_event",
    "cache_lookup",
    "cache_write",
    "authenticate_request",
    "transform_payload",
    "transform_data",
    "generate_report",
    "send_notification",
    "validate_input",
    "process_data",
    "unknown_operation",
}

_ACTION_PRIORITY: Dict[str, int] = {
    "external_call": 60,
    "notification": 60,
    "persistence": 50,
    "validation": 40,
    "transformation": 30,
    "internal_processing": 20,
    "other": 10,
}

_PERSISTENCE_ID_PREFIX_TO_OPERATION: Dict[str, str] = {
    "create": "create_resource",
    "list": "list_resources",
    "get": "get_resource",
    "read": "get_resource",
    "update": "update_resource",
    "patch": "update_resource",
    "delete": "delete_resource",
    "remove": "delete_resource",
}


@dataclass(frozen=True, slots=True)
class ImplementationContract:
    # identity / binding
    file: str
    method: str
    path: str
    func: str

    # agnostic declared capability
    capability_id: Optional[str]
    interaction_kind: InteractionKind
    operation: str
    goal: str
    confidence: str
    evidence: str

    # contracts
    input_contract: Dict[str, Any]
    output_contract: Dict[str, Any]
    state_contract: Dict[str, Any]
    dependency_contract: Dict[str, Any]

    # strategies
    runtime_strategy: Dict[str, Any]
    implementation_plan: List[str]
    response_strategy: str
    validation_strategy: str
    test_strategy: str

    # legacy compatibility
    capability: Capability

    # debug/meta
    source: Dict[str, Any]

    resource: Optional[str] = None
    must_implement: List[str] = field(default_factory=list)
    must_not: List[str] = field(default_factory=list)
    external_dependencies: List[Dict[str, Any]] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    integration_refs: List[str] = field(default_factory=list)
    implementation_levels: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        state_strategy = str(self.runtime_strategy.get("default_mode") or "safe_local")
        adapter_strategy = str(self.runtime_strategy.get("real_mode") or "none")
        must_implement = list(self.must_implement or _must_implement_for_capability(self.capability))
        must_not = list(
            self.must_not
            or [
                "Do not return a hard-coded success response that bypasses required actions",
                "Do not invent side effects that are not declared by the SPEC",
            ]
        )
        return {
            "file": self.file,
            "method": self.method,
            "path": self.path,
            "func": self.func,
            "capability_id": self.capability_id,
            "interaction_kind": self.interaction_kind,
            "operation": self.operation,
            "goal": self.goal,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "state_contract": self.state_contract,
            "dependency_contract": self.dependency_contract,
            "runtime_strategy": self.runtime_strategy,
            "implementation_plan": list(self.implementation_plan),
            "response_strategy": self.response_strategy,
            "validation_strategy": self.validation_strategy,
            "test_strategy": self.test_strategy,
            "state_strategy": state_strategy,
            "adapter_strategy": adapter_strategy,
            "capability": self.capability,
            "resource": self.resource,
            "external_dependencies": list(self.external_dependencies),
            "must_implement": must_implement,
            "must_not": must_not,
            "actions": list(self.actions),
            "errors": list(self.errors),
            "integration_refs": list(self.integration_refs),
            "implementation_levels": list(self.implementation_levels),
            "source": self.source,
        }


def _debug_dir() -> Path:
    return Path("output") / "_debug"


def _safe_write_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def _normalize_path(p: str) -> str:
    return str(p or "").replace("\\", "/").strip()


def _snake(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s or "").strip("_").lower()
    s = re.sub(r"_+", "_", s)
    return s or "handler"


def _extract_resource_from_path(path: str) -> Optional[str]:
    p = str(path or "").strip()
    if not p.startswith("/"):
        return None
    parts = [x for x in p.split("/") if x]
    technical = {"api", "internal", "public", "private", "admin"}
    version_pattern = re.compile(r"v(?:ersion)?\d+$", re.IGNORECASE)
    candidate: Optional[str] = None
    for seg in parts:
        s = seg.strip()
        if not s:
            continue
        if s.startswith("{") and s.endswith("}"):
            continue
        if s.lower() in technical:
            continue
        if version_pattern.fullmatch(s):
            continue
        candidate = s
        break
    return candidate


def _path_has_params(path: str) -> bool:
    return re.search(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", path or "") is not None


def _parse_endpoint_binding(s: str) -> Optional[Tuple[str, str]]:
    if not isinstance(s, str):
        return None
    m = re.fullmatch(r"\s*([A-Z]+)\s+(\S+)\s*", s)
    if not m:
        return None
    return (m.group(1).upper(), m.group(2))


def _dedupe_strs(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _dedupe_dicts(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _normalize_action_kind(value: Any) -> str:
    return str(value or "").strip().lower()


def _action_text(action: Dict[str, Any]) -> str:
    return " ".join(
        [
            str(action.get("id") or ""),
            str(action.get("kind") or ""),
            str(action.get("description") or ""),
        ]
    ).strip().lower()


def _is_collection_path(path: str) -> bool:
    return not _path_has_params(path)


def _capability_from_persistence_action(action: Dict[str, Any], endpoint: Dict[str, Any]) -> str:
    explicit = str(action.get("operation") or "").strip()
    if explicit:
        normalized = _capability_from_legacy_operation(explicit)
        if normalized in {
            "create_resource",
            "list_resources",
            "get_resource",
            "update_resource",
            "delete_resource",
        }:
            return normalized

    action_id = str(action.get("id") or "").strip().lower().replace("-", "_")
    for prefix, operation in _PERSISTENCE_ID_PREFIX_TO_OPERATION.items():
        if action_id.startswith(prefix + "_") or action_id == prefix:
            return operation

    method = str(endpoint.get("method") or "").upper().strip()
    path = str(endpoint.get("path") or "").strip()
    if method == "POST":
        return "create_resource"
    if method == "GET":
        return "get_resource" if _path_has_params(path) else "list_resources"
    if method in {"PUT", "PATCH"}:
        return "update_resource"
    if method == "DELETE":
        return "delete_resource"
    return "unknown_operation"


def _capability_from_external_action(action: Dict[str, Any]) -> str:
    text = _action_text(action)
    if any(token in text for token in ("upload", "upload_file", "upload_to")):
        return "upload_file"
    if any(token in text for token in ("download", "download_file")):
        return "download_file"
    if any(token in text for token in ("send_email", "email", "mail")):
        return "send_notification"
    if any(token in text for token in ("publish_event", "publish", "send_event")):
        return "publish_event"
    if any(token in text for token in ("consume_event", "read_event", "consume")):
        return "consume_event"
    return "call_external_api"


def _operation_from_capability(capability: str) -> str:
    return _semantics_from_capability(capability).get("operation", "handle_request")


def _resolve_from_actions(endpoint: Dict[str, Any], spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    del spec
    raw_actions = endpoint.get("actions") or []
    actions = [action for action in raw_actions if isinstance(action, dict)]
    if not actions:
        return None

    candidates: List[Tuple[int, str, str, str]] = []
    for action in actions:
        kind = _normalize_action_kind(action.get("kind"))
        if kind == "persistence":
            capability = _capability_from_persistence_action(action, endpoint)
            candidates.append((_ACTION_PRIORITY["persistence"], capability, "persistence", _operation_from_capability(capability)))
        elif kind == "external_call":
            capability = _capability_from_external_action(action)
            candidates.append((_ACTION_PRIORITY["external_call"], capability, "external_integration", _operation_from_capability(capability)))
        elif kind == "notification":
            text = _action_text(action)
            operation = "send_email" if "email" in text or "mail" in text else "notify"
            candidates.append((_ACTION_PRIORITY["notification"], "send_notification", "external_integration", operation))
        elif kind == "validation":
            text = _action_text(action)
            if any(token in text for token in ("token", "auth", "credentials", "authentication")):
                candidates.append((_ACTION_PRIORITY["validation"], "authenticate_request", "authentication", "verify_credentials"))
            else:
                candidates.append((_ACTION_PRIORITY["validation"], "validate_input", "validation", "validate"))
        elif kind == "transformation":
            candidates.append((_ACTION_PRIORITY["transformation"], "transform_data", "data_processing", "transform"))
        elif kind == "internal_processing":
            candidates.append((_ACTION_PRIORITY["internal_processing"], "process_data", "internal_processing", "process"))

    if not candidates:
        return None

    best = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
    return {
        "capability": best[1],
        "interaction_kind": best[2],
        "operation": best[3],
        "resource": _extract_resource_from_path(str(endpoint.get("path") or "")),
        "source_kind": "endpoint_actions",
        "declared_capability": None,
    }


def _integrations_by_id(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in spec.get("integrations") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip()
        if key and key not in out:
            out[key] = item
    return out


def _configuration_by_key(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in spec.get("configuration") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key and key not in out:
            out[key] = item
    return out


def _collect_integration_refs(endpoint: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    for ref in endpoint.get("integration_refs") or []:
        if isinstance(ref, str) and ref.strip():
            refs.append(ref.strip())
    for action in endpoint.get("actions") or []:
        if not isinstance(action, dict):
            continue
        ref = action.get("integration_ref")
        if isinstance(ref, str) and ref.strip():
            refs.append(ref.strip())
    return _dedupe_strs(refs)


def _resolve_endpoint_integrations(endpoint: Dict[str, Any], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    refs = _collect_integration_refs(endpoint)
    by_id = _integrations_by_id(spec)
    resolved: List[Dict[str, Any]] = []
    for ref in refs:
        integration = by_id.get(ref)
        if not isinstance(integration, dict):
            continue
        resolved.append(
            {
                "id": integration.get("id"),
                "name": integration.get("name"),
                "kind": integration.get("kind", "other"),
                "implementation_level": integration.get("implementation_level", "integration_skeleton"),
                "authentication": integration.get("authentication", {}) or {},
                "configuration_refs": list(integration.get("configuration_refs") or []),
                "technology_refs": list(integration.get("technology_refs") or []),
            }
        )

    for item in endpoint.get("source", {}).get("external_dependencies") or []:
        if isinstance(item, dict):
            resolved.append(dict(item))

    return _dedupe_dicts(resolved)


def _resolve_configuration_for_integrations(
    integrations: List[Dict[str, Any]],
    configuration_by_key: Dict[str, Dict[str, Any]],
    endpoint: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    refs: List[str] = []
    for integration in integrations:
        for ref in integration.get("configuration_refs") or []:
            if isinstance(ref, str) and ref.strip():
                refs.append(ref.strip())

    for action in (endpoint or {}).get("actions") or []:
        if not isinstance(action, dict):
            continue
        for ref in action.get("configuration_refs") or []:
            if isinstance(ref, str) and ref.strip():
                refs.append(ref.strip())

    resolved: List[Dict[str, Any]] = []
    for ref in _dedupe_strs(refs):
        conf = configuration_by_key.get(ref)
        if isinstance(conf, dict):
            resolved.append(conf)
    return resolved


def find_declared_capability_for_endpoint(endpoint: Dict[str, Any], spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    caps = spec.get("product_capabilities")
    if not isinstance(caps, list) or not caps:
        return None

    method = str(endpoint.get("method") or "").upper().strip()
    path = str(endpoint.get("path") or "").strip()
    func = str(endpoint.get("func") or "").strip()
    operation_id = str(endpoint.get("operation_id") or "").strip()

    for c in caps:
        if not isinstance(c, dict):
            continue

        eps = c.get("endpoints")
        if isinstance(eps, list):
            for ep in eps:
                if isinstance(ep, str):
                    binding = _parse_endpoint_binding(ep)
                    if binding and binding[0] == method and binding[1] == path:
                        return c
                elif isinstance(ep, dict):
                    ep_method = str(ep.get("method") or "").upper().strip()
                    ep_path = str(ep.get("path") or "").strip()
                    if ep_method == method and ep_path == path:
                        return c

        funcs = c.get("endpoint_funcs")
        if func and isinstance(funcs, list) and func in [str(x) for x in funcs]:
            return c

        cid = str(c.get("id") or "").strip()
        if operation_id and (operation_id == cid or operation_id == str(c.get("operation_id") or "").strip()):
            return c

    return None


def _interaction_kind_from_declared(cap: Dict[str, Any]) -> InteractionKind:
    kind = str(cap.get("kind") or "").strip()
    return kind if kind in _INTERACTION_KINDS else "unknown"


def _legacy_capability_label_from_declared(kind: InteractionKind, operation: str) -> Capability:
    op = (operation or "").strip().lower()
    if kind == "persistence":
        if op in ("create", "add", "register"):
            return "create_resource"
        if op in ("list", "search", "query"):
            return "list_resources"
        if op in ("read", "get", "detail"):
            return "get_resource"
        if op in ("update", "replace", "patch"):
            return "update_resource"
        if op in ("delete", "remove"):
            return "delete_resource"
        return "unknown_operation"
    if kind == "file_io":
        if op in ("ingest", "upload", "import"):
            return "upload_file"
        if op in ("export", "download"):
            return "download_file"
        return "process_file"
    if kind == "external_integration":
        return "call_external_api"
    if kind == "messaging":
        if op in ("publish", "publish_event"):
            return "publish_event"
        if op in ("consume", "consume_event"):
            return "consume_event"
        return "unknown_operation"
    if kind == "authentication":
        return "authenticate_request"
    if kind == "validation":
        return "validate_input"
    if kind == "data_processing":
        return "transform_data"
    if kind == "internal_processing":
        return "process_data"
    return "unknown_operation"


def _detect_declared_external_dependencies(cap: Dict[str, Any], endpoint: Dict[str, Any], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for src in (cap.get("external_dependencies"), endpoint.get("source", {}).get("external_dependencies")):
        if not isinstance(src, list):
            continue
        for it in src:
            if isinstance(it, dict):
                kind = str(it.get("kind") or it.get("type") or "").strip()
                provider = str(it.get("provider") or it.get("name") or it.get("service") or "").strip()
                d = {**it}
                if kind:
                    d["kind"] = kind
                if provider and "provider" not in d:
                    d["provider"] = provider
                out.append(d)

    for integration in _resolve_endpoint_integrations(endpoint, spec):
        out.append(integration)

    return _dedupe_dicts(out)


def _build_runtime_strategy(
    *,
    interaction_kind: InteractionKind,
    input_contract: Dict[str, Any],
    output_contract: Dict[str, Any],
    state_contract: Dict[str, Any],
    external_dependencies: List[Dict[str, Any]],
) -> Dict[str, Any]:
    import_time_side_effects_allowed = False
    external_blocking = False

    state_required = bool(state_contract.get("required") is True)
    has_external = bool(external_dependencies)

    input_kinds = set()
    if isinstance(input_contract.get("inputs"), list):
        for i in input_contract["inputs"]:
            if isinstance(i, dict) and i.get("kind"):
                input_kinds.add(str(i["kind"]))

    output_kinds = set()
    if isinstance(output_contract.get("outputs"), list):
        for o in output_contract["outputs"]:
            if isinstance(o, dict) and o.get("kind"):
                output_kinds.add(str(o["kind"]))

    uses_file = ("file" in input_kinds) or ("multipart" in input_kinds) or ("file" in output_kinds) or ("report" in output_kinds)

    if has_external:
        default_mode = "dry_run"
        real_mode = "lazy_adapter"
    elif state_required:
        default_mode = "in_memory"
        real_mode = "lazy_adapter"
    elif uses_file:
        default_mode = "local_filesystem"
        real_mode = "lazy_adapter"
    else:
        default_mode = "safe_local"
        real_mode = "none"

    return {
        "default_mode": default_mode,
        "real_mode": real_mode,
        "external_blocking": external_blocking,
        "import_time_side_effects_allowed": import_time_side_effects_allowed,
        "manual_steps_required": [],
    }


def _semantics_from_capability(capability: str) -> Dict[str, str]:
    mapping: Dict[str, Dict[str, str]] = {
        "create_resource": {"interaction_kind": "persistence", "operation": "create"},
        "list_resources": {"interaction_kind": "persistence", "operation": "list"},
        "list_resource": {"interaction_kind": "persistence", "operation": "list"},
        "get_resource": {"interaction_kind": "persistence", "operation": "get"},
        "update_resource": {"interaction_kind": "persistence", "operation": "update"},
        "delete_resource": {"interaction_kind": "persistence", "operation": "delete"},
        "upload_file": {"interaction_kind": "file_io", "operation": "upload"},
        "download_file": {"interaction_kind": "file_io", "operation": "download"},
        "receive_webhook": {"interaction_kind": "external_integration", "operation": "receive_webhook"},
        "publish_event": {"interaction_kind": "messaging", "operation": "publish"},
        "consume_event": {"interaction_kind": "messaging", "operation": "consume"},
        "cache_lookup": {"interaction_kind": "cache", "operation": "lookup"},
        "cache_write": {"interaction_kind": "cache", "operation": "write"},
        "authenticate_request": {"interaction_kind": "authentication", "operation": "verify_credentials"},
        "call_external_api": {"interaction_kind": "external_integration", "operation": "call"},
        "send_notification": {"interaction_kind": "external_integration", "operation": "notify"},
        "transform_data": {"interaction_kind": "data_processing", "operation": "transform"},
        "validate_input": {"interaction_kind": "validation", "operation": "validate"},
        "process_data": {"interaction_kind": "internal_processing", "operation": "process"},
    }
    return mapping.get(capability, {"interaction_kind": "unknown", "operation": "handle_request"})


def _capability_from_legacy_operation(operation: Any) -> Optional[str]:
    normalized = str(operation or "").strip().lower().replace("_", "-")
    if not normalized:
        return None
    mapping = {
        "upload": "upload_file",
        "upload-file": "upload_file",
        "download": "download_file",
        "webhook": "receive_webhook",
        "receive-webhook": "receive_webhook",
        "publish": "publish_event",
        "publish-event": "publish_event",
        "consume": "consume_event",
        "consume-event": "consume_event",
        "cache-lookup": "cache_lookup",
        "cache-read": "cache_lookup",
        "cache-write": "cache_write",
        "verify-token": "authenticate_request",
        "authenticate": "authenticate_request",
        "auth": "authenticate_request",
        "external-call": "call_external_api",
    }
    return mapping.get(normalized)


def _resolve_from_integrations(endpoint: Dict[str, Any], spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    integrations = _resolve_endpoint_integrations(endpoint, spec)
    if not integrations:
        return None

    actions = [a for a in endpoint.get("actions") or [] if isinstance(a, dict)]
    for action in actions:
        kind = _normalize_action_kind(action.get("kind"))
        if kind in {"external_call", "notification"}:
            capability = "send_notification" if kind == "notification" else _capability_from_external_action(action)
            semantics = _semantics_from_capability(capability)
            operation = "send_email" if capability == "send_notification" and "email" in _action_text(action) else semantics["operation"]
            return {
                "capability": capability,
                "interaction_kind": semantics["interaction_kind"],
                "operation": operation,
                "resource": _extract_resource_from_path(str(endpoint.get("path") or "")),
                "source_kind": "endpoint_integrations",
                "declared_capability": None,
            }

    return {
        "capability": "call_external_api",
        "interaction_kind": "external_integration",
        "operation": "call",
        "resource": _extract_resource_from_path(str(endpoint.get("path") or "")),
        "source_kind": "endpoint_integrations",
        "declared_capability": None,
    }


def _resolve_from_declared_capability(endpoint: Dict[str, Any], spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    declared = find_declared_capability_for_endpoint(endpoint, spec)
    if not declared:
        return None

    interaction_kind = _interaction_kind_from_declared(declared)
    operation = str(declared.get("operation") or "handle_request").strip() or "handle_request"
    capability = str(declared.get("capability") or "").strip() or _legacy_capability_label_from_declared(interaction_kind, operation)
    return {
        "capability": capability,
        "interaction_kind": interaction_kind,
        "operation": operation,
        "resource": _extract_resource_from_path(str(endpoint.get("path") or "")),
        "source_kind": "declared_product_capability",
        "declared_capability": declared,
    }


def _resolve_endpoint_semantics(endpoint: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_from_actions(endpoint, spec)
    if resolved:
        return resolved

    resolved = _resolve_from_integrations(endpoint, spec)
    if resolved:
        return resolved

    resolved = _resolve_from_declared_capability(endpoint, spec)
    if resolved:
        return resolved

    explicit_capability = str(endpoint.get("capability") or "").strip()
    if explicit_capability:
        semantics = _semantics_from_capability(explicit_capability)
        return {
            "capability": explicit_capability,
            "interaction_kind": semantics["interaction_kind"],
            "operation": semantics["operation"],
            "resource": _extract_resource_from_path(str(endpoint.get("path") or "")),
            "source_kind": "legacy_endpoint_capability",
            "declared_capability": None,
        }

    # Legacy compatibility: endpoint.operation is not the primary source of truth.
    legacy_operation_capability = _capability_from_legacy_operation(endpoint.get("operation"))
    if legacy_operation_capability:
        semantics = _semantics_from_capability(legacy_operation_capability)
        return {
            "capability": legacy_operation_capability,
            "interaction_kind": semantics["interaction_kind"],
            "operation": semantics["operation"],
            "resource": _extract_resource_from_path(str(endpoint.get("path") or "")),
            "source_kind": "legacy_endpoint_operation",
            "declared_capability": None,
        }

    fallback = fallback_infer_capability_from_endpoint_shape(endpoint, spec)
    return {
        "capability": fallback["capability"],
        "interaction_kind": fallback["interaction_kind"],
        "operation": fallback["operation"],
        "resource": _extract_resource_from_path(str(endpoint.get("path") or "")),
        "source_kind": fallback["source_kind"],
        "declared_capability": None,
    }


def fallback_infer_capability_from_endpoint_shape(endpoint: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    method = str(endpoint.get("method") or "").upper().strip() or "GET"
    path = str(endpoint.get("path") or "").strip() or "/"
    req = endpoint.get("request") if isinstance(endpoint.get("request"), dict) else {}
    resp = endpoint.get("response") if isinstance(endpoint.get("response"), dict) else {}

    request_type = str(req.get("type") or "").strip().lower()
    response_type = str(resp.get("type") or "").strip().lower()
    has_file_field = any(
        isinstance(field, dict) and str(field.get("format") or "").strip().lower() == "binary"
        for field in (req.get("fields") or [])
    )

    if request_type == "multipart" or has_file_field:
        return {
            "capability": "upload_file",
            "interaction_kind": "file_io",
            "operation": "upload",
            "source_kind": "fallback_endpoint_shape",
        }

    if response_type == "file" and request_type != "multipart":
        return {
            "capability": "download_file",
            "interaction_kind": "file_io",
            "operation": "download",
            "source_kind": "fallback_endpoint_shape",
        }

    external_deps = endpoint.get("source", {}).get("external_dependencies") or []
    if external_deps:
        return {
            "capability": "call_external_api",
            "interaction_kind": "external_integration",
            "operation": "call",
            "source_kind": "fallback_endpoint_shape",
        }

    persistence = spec.get("persistence") if isinstance(spec.get("persistence"), dict) else {}
    if persistence.get("required") is True:
        if method == "POST" and _is_collection_path(path):
            return {
                "capability": "create_resource",
                "interaction_kind": "persistence",
                "operation": "create",
                "source_kind": "fallback_endpoint_shape",
            }
        if method == "GET" and _is_collection_path(path):
            return {
                "capability": "list_resources",
                "interaction_kind": "persistence",
                "operation": "list",
                "source_kind": "fallback_endpoint_shape",
            }
        if method == "GET" and _path_has_params(path):
            return {
                "capability": "get_resource",
                "interaction_kind": "persistence",
                "operation": "get",
                "source_kind": "fallback_endpoint_shape",
            }
        if method in {"PUT", "PATCH"} and _path_has_params(path):
            return {
                "capability": "update_resource",
                "interaction_kind": "persistence",
                "operation": "update",
                "source_kind": "fallback_endpoint_shape",
            }
        if method == "DELETE" and _path_has_params(path):
            return {
                "capability": "delete_resource",
                "interaction_kind": "persistence",
                "operation": "delete",
                "source_kind": "fallback_endpoint_shape",
            }

    return {
        "capability": "unknown_operation",
        "interaction_kind": "unknown",
        "operation": "handle_request",
        "source_kind": "fallback_endpoint_shape",
    }


def _inputs_from_endpoint(endpoint: Dict[str, Any]) -> List[Dict[str, Any]]:
    req = endpoint.get("request") if isinstance(endpoint.get("request"), dict) else {}
    inputs: List[Dict[str, Any]] = []
    if req:
        rtype = str(req.get("type") or "json").strip().lower()
        inputs.append({"kind": rtype or "json", "name": "body"})
    if _path_has_params(str(endpoint.get("path") or "")):
        inputs.append({"kind": "path", "name": "path_params"})
    return inputs


def _outputs_from_endpoint(endpoint: Dict[str, Any]) -> List[Dict[str, Any]]:
    resp = endpoint.get("response") if isinstance(endpoint.get("response"), dict) else {}
    if resp:
        return [{"kind": str(resp.get("type") or "json").strip().lower() or "json", "name": "response"}]
    return [{"kind": "json", "name": "response"}]


def _must_implement_for_capability(capability: Capability) -> List[str]:
    mapping: Dict[str, List[str]] = {
        "create_resource": [
            "Validate input before creating the resource",
            "Execute the declared persistence create operation",
            "Build the response from the created resource state",
        ],
        "list_resources": [
            "Validate query inputs before listing resources",
            "Execute the declared persistence list operation",
            "Build the response collection from the persistence result",
        ],
        "list_resource": [
            "Validate query inputs before listing resources",
            "Execute the declared persistence list operation",
            "Build the response collection from the persistence result",
        ],
        "get_resource": [
            "Validate path inputs before loading the resource",
            "Execute the declared persistence read operation",
            "Build the response from the loaded resource state",
        ],
        "update_resource": [
            "Validate input before updating the resource",
            "Execute the declared persistence update operation",
            "Build the response from the updated resource state",
        ],
        "delete_resource": [
            "Validate path inputs before deleting the resource",
            "Execute the declared persistence delete operation",
            "Build the response from the deletion result",
        ],
        "upload_file": [
            "Validate uploaded file metadata and content when declared",
            "Persist or transfer the uploaded file through the declared operation",
            "Build the response with file metadata or resulting status",
        ],
        "download_file": [
            "Validate file retrieval inputs before downloading",
            "Read the declared file resource from the configured source",
            "Build the response with file content or metadata",
        ],
        "receive_webhook": [
            "Validate the incoming webhook payload and signature when declared",
            "Execute the declared webhook handling flow",
            "Build the acknowledgement response",
        ],
        "publish_event": [
            "Validate the event payload before publishing",
            "Publish the declared event through the required adapter",
            "Build the response with event publication status",
        ],
        "consume_event": [
            "Consume message from the declared source",
            "Acknowledge or record the consumed message outcome",
        ],
        "cache_lookup": [
            "Read from the configured cache using the declared lookup key",
            "Return a cache hit or miss result according to the contract",
        ],
        "cache_write": [
            "Write the declared value to the configured cache",
            "Return the cache write status according to the contract",
        ],
        "authenticate_request": [
            "Extract credentials or token from the declared request inputs",
            "Execute the declared verification logic before accepting authentication",
        ],
        "call_external_api": [
            "Resolve and invoke the declared external integration",
            "Build the response from the external operation result",
        ],
        "send_notification": [
            "Build the notification payload from declared inputs",
            "Send the notification through the declared provider integration",
        ],
        "transform_data": [
            "Validate input before applying the declared transformation",
            "Transform the data deterministically according to the contract",
        ],
        "validate_input": [
            "Validate the request according to the declared schema when available",
        ],
        "process_data": [
            "Execute the declared internal processing actions",
            "Build the response from the processing result",
        ],
        "unknown_operation": [
            "Validate the request according to the declared schema when available",
            "Build the response from the declared response contract",
        ],
    }
    return mapping.get(capability, mapping["unknown_operation"])


def _build_must_implement(
    *,
    capability: str,
    endpoint: Dict[str, Any],
    integrations: List[Dict[str, Any]],
    configuration_by_key: Dict[str, Dict[str, Any]],
) -> List[str]:
    obligations: List[str] = []
    obligations.extend(_must_implement_for_capability(capability))

    for action in endpoint.get("actions") or []:
        if not isinstance(action, dict):
            continue
        if action.get("required", True) is False:
            continue
        action_id = str(action.get("id") or "unnamed_action").strip()
        description = str(action.get("description") or "").strip()
        if description:
            obligations.append(f"Implement required action '{action_id}': {description}")
        else:
            obligations.append(f"Implement required action '{action_id}'")

        kind = _normalize_action_kind(action.get("kind"))
        if kind == "external_call":
            ref = str(action.get("integration_ref") or "").strip()
            if ref:
                obligations.append(f"Resolve and invoke integration '{ref}'")

    resolved_configuration = _resolve_configuration_for_integrations(integrations, configuration_by_key, endpoint)

    for integration in integrations:
        integration_id = str(integration.get("id") or integration.get("name") or "external_integration").strip()
        if integration_id:
            obligations.append(f"Resolve and invoke integration '{integration_id}'")
        if str(integration.get("implementation_level") or "") == "integration_skeleton":
            obligations.append(
                "Implement a real integration skeleton with client construction, configuration and callable external operation"
            )
        authentication = integration.get("authentication")
        if isinstance(authentication, dict):
            mechanism = str(authentication.get("mechanism") or "").strip()
            if mechanism:
                obligations.append(f"Configure authentication mechanism '{mechanism}' without embedded credentials")

        for ref in integration.get("configuration_refs") or []:
            if isinstance(ref, str) and ref.strip():
                obligations.append(f"Read required configuration '{ref.strip()}' from its declared delivery mechanism")

    for conf in resolved_configuration:
        key = str(conf.get("key") or "").strip()
        if key:
            obligations.append(f"Read required configuration '{key}' from its declared delivery mechanism")

    for error in endpoint.get("errors") or []:
        if not isinstance(error, dict):
            continue
        if error.get("required", True) is False:
            continue
        code = str(error.get("code") or "unknown_error").strip()
        status_code = error.get("status_code")
        if status_code is None:
            obligations.append(f"Handle declared error '{code}'")
        else:
            obligations.append(f"Handle declared error '{code}' with status {status_code}")

    return _dedupe_strs(obligations)


def _build_must_not(
    *,
    capability: str,
    endpoint: Dict[str, Any],
    integrations: List[Dict[str, Any]],
) -> List[str]:
    rules: List[str] = [
        "Do not return a hard-coded success response that bypasses required actions",
    ]

    if integrations:
        rules.extend(
            [
                "Do not embed credentials, access tokens or secret values in source code",
                "Do not replace a required external integration with a fake success response",
                "Do not execute real external calls in hermetic tests",
            ]
        )

    if capability in {"create_resource", "list_resources", "list_resource", "get_resource", "update_resource", "delete_resource"}:
        rules.append("Do not replace required persistence with an in-memory placeholder unless the SPEC explicitly allows it")

    if capability == "authenticate_request":
        rules.append("Do not accept authentication as valid without executing the declared verification logic")

    if capability == "upload_file":
        rules.append("Do not claim that a file was uploaded unless the upload operation was invoked")

    path = str(endpoint.get("path") or "").strip()
    actions = [a for a in endpoint.get("actions") or [] if isinstance(a, dict)]
    if path == "/health" and not actions and not integrations:
        rules.append("Do not make the health endpoint depend on external integrations")

    if capability == "unknown_operation" or not rules:
        rules.append("Do not invent side effects that are not declared by the SPEC")

    return _dedupe_strs(rules)


def _build_implementation_plan(
    capability: str,
    endpoint: Dict[str, Any],
    integrations: List[Dict[str, Any]],
) -> List[str]:
    if capability == "unknown_operation":
        return [
            "validate_input_if_schema",
            "build_response_from_example_or_status",
        ]

    plan: List[str] = ["validate_request_schema"]

    actions = [a for a in endpoint.get("actions") or [] if isinstance(a, dict)]
    if any(_normalize_action_kind(action.get("kind")) == "internal_processing" for action in actions):
        plan.append("execute_internal_processing_actions")

    if capability in {"create_resource", "list_resources", "list_resource", "get_resource", "update_resource", "delete_resource"}:
        plan.append("execute_persistence_operation")
        plan.append("map_not_found_or_validation_errors")
    elif integrations:
        if _resolve_configuration_for_integrations(integrations, {}, endpoint) or any(
            integration.get("configuration_refs") for integration in integrations
        ):
            plan.append("load_declared_configuration")
        plan.append("build_external_integration_client")
        plan.append("invoke_external_operation")
        plan.append("map_declared_errors")
    elif capability == "upload_file":
        plan.append("execute_file_upload_or_transfer")
        plan.append("map_declared_errors")
    elif capability == "authenticate_request":
        plan.append("execute_declared_authentication_logic")
    elif capability == "send_notification":
        plan.append("build_external_integration_client")
        plan.append("invoke_external_operation")
    elif capability == "publish_event":
        plan.append("build_external_integration_client")
        plan.append("invoke_external_operation")
    elif capability == "consume_event":
        plan.append("consume_declared_event")
    elif capability == "transform_data":
        plan.append("transform_declared_payload")
    elif capability == "validate_input":
        plan.append("apply_declared_validation")
    elif capability == "process_data":
        plan.append("execute_internal_processing_actions")

    plan.append("build_declared_response")
    return _dedupe_strs(plan)


def build_implementation_contracts_from_spec(spec: dict) -> List[Dict[str, Any]]:
    if not isinstance(spec, dict):
        raise TypeError("spec must be a dict")

    endpoints = spec.get("endpoints") or []
    if not isinstance(endpoints, list):
        endpoints = []

    contracts: List[ImplementationContract] = []
    sources_debug: List[Dict[str, Any]] = []

    configuration_by_key = _configuration_by_key(spec)

    for ep in endpoints:
        if not isinstance(ep, dict):
            continue

        method = str(ep.get("method") or "").upper().strip() or "GET"
        path = str(ep.get("path") or "").strip() or "/"
        file_path = _normalize_path(ep.get("file") or "")
        func = str(ep.get("func") or "").strip()
        resource = _extract_resource_from_path(path)
        if not func:
            func = _snake(f"{method.lower()}_{resource or path}")

        semantics = _resolve_endpoint_semantics(ep, spec)
        declared = semantics.get("declared_capability")
        source_kind = str(semantics.get("source_kind") or "fallback_endpoint_shape")
        capability = str(semantics.get("capability") or "unknown_operation")
        interaction_kind = str(semantics.get("interaction_kind") or "unknown")
        operation = str(semantics.get("operation") or "handle_request")

        integrations = _resolve_endpoint_integrations(ep, spec)
        configuration = _resolve_configuration_for_integrations(integrations, configuration_by_key, ep)
        implementation_levels = _dedupe_strs(
            [str(integration.get("implementation_level") or "").strip() for integration in integrations if integration.get("implementation_level")]
        )

        input_contract = {"inputs": _inputs_from_endpoint(ep)}
        output_contract = {"outputs": _outputs_from_endpoint(ep)}
        state_contract = {
            "required": capability in {"create_resource", "list_resources", "list_resource", "get_resource", "update_resource", "delete_resource"},
            "kind": "resource_state" if capability in {"create_resource", "list_resources", "list_resource", "get_resource", "update_resource", "delete_resource"} else None,
        }
        dependency_contract = {"external_dependencies": integrations}
        runtime_strategy = _build_runtime_strategy(
            interaction_kind=interaction_kind,
            input_contract=input_contract,
            output_contract=output_contract,
            state_contract=state_contract,
            external_dependencies=integrations,
        )

        implementation_plan = _build_implementation_plan(capability, ep, integrations)
        must_implement = _build_must_implement(
            capability=capability,
            endpoint=ep,
            integrations=integrations,
            configuration_by_key=configuration_by_key,
        )
        must_not = _build_must_not(
            capability=capability,
            endpoint=ep,
            integrations=integrations,
        )

        response_strategy = "from_declared_output_contract" if declared else "response_example_or_status_ok"
        validation_strategy = "schema_if_present_else_basic"
        test_strategy = "contract_based_smoke_tests" if declared or integrations or ep.get("actions") else "safe_smoke_tests"

        contract = ImplementationContract(
            file=file_path,
            method=method,
            path=path,
            func=func,
            capability_id=str(declared.get("id") or "").strip() or None if isinstance(declared, dict) else None,
            interaction_kind=interaction_kind if interaction_kind in _INTERACTION_KINDS else "unknown",
            operation=operation,
            goal=str(declared.get("goal") or "").strip() if isinstance(declared, dict) else "",
            confidence="high" if source_kind != "fallback_endpoint_shape" else "low",
            evidence=source_kind,
            input_contract=input_contract,
            output_contract=output_contract,
            state_contract=state_contract,
            dependency_contract=dependency_contract,
            runtime_strategy=runtime_strategy,
            implementation_plan=implementation_plan,
            response_strategy=response_strategy,
            validation_strategy=validation_strategy,
            test_strategy=test_strategy,
            capability=capability if capability in _CAPABILITIES else "unknown_operation",
            source={
                "source_kind": source_kind,
                "endpoint": ep,
                "declared_product_capability": declared,
                "technology_signals": list(spec.get("technology_signals") or []),
                "integrations": integrations,
                "configuration": configuration,
            },
            resource=resource,
            must_implement=must_implement,
            must_not=must_not,
            external_dependencies=integrations,
            actions=[action for action in ep.get("actions") or [] if isinstance(action, dict)],
            errors=[error for error in ep.get("errors") or [] if isinstance(error, dict)],
            integration_refs=_collect_integration_refs(ep),
            implementation_levels=implementation_levels,
        )
        contracts.append(contract)
        sources_debug.append(
            {
                "file": file_path,
                "method": method,
                "path": path,
                "func": func,
                "source_kind": source_kind,
                "confidence": contract.confidence,
                "capability_id": contract.capability_id,
                "capability": contract.capability,
            }
        )

    contracts_dict = [c.to_dict() for c in contracts]

    _safe_write_json(_debug_dir() / "implementation_contracts.json", contracts_dict)

    summary: Dict[str, int] = {}
    for c in contracts:
        summary[c.interaction_kind] = summary.get(c.interaction_kind, 0) + 1
    _safe_write_json(_debug_dir() / "implementation_capabilities_summary.json", summary)

    _safe_write_json(_debug_dir() / "implementation_contract_sources.json", sources_debug)

    return contracts_dict


def implementation_contracts_for_file(path: str, contracts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    p = _normalize_path(path)
    if not p:
        return []
    out: List[Dict[str, Any]] = []
    for c in contracts or []:
        if not isinstance(c, dict):
            continue
        if _normalize_path(c.get("file") or "") == p:
            out.append(c)
    return out
