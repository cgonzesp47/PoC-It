from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Compat label (legacy-ish). Kept to avoid breaking downstream consumers.
Capability = str


# New agnostic interaction kinds
InteractionKind = str

_INTERACTION_KINDS: set[str] = {
    "file_io",
    "resource_state",
    "external_call",
    "event_ingress",
    "event_egress",
    "cache_state",
    "auth_gate",
    "payload_transform",
    "report_generation",
    "notification",
    "search_query",
    "job",
    "unknown",
}

# Backward-compat capability taxonomy (label only)
_CAPABILITIES: set[str] = {
    "health_check",
    "create_resource",
    "list_resource",
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
    "generate_report",
    "unknown_operation",
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

    def to_dict(self) -> Dict[str, Any]:
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
            "implementation_plan": self.implementation_plan,
            "response_strategy": self.response_strategy,
            "validation_strategy": self.validation_strategy,
            "test_strategy": self.test_strategy,
            "capability": self.capability,
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
    """
    Resource helper ONLY for downstream hints and for shape fallback.
    Not used to infer semantic capabilities beyond low-confidence heuristics.
    """
    p = str(path or "").strip()
    if not p.startswith("/"):
        return None
    parts = [x for x in p.split("/") if x]
    technical = {"api", "internal", "public", "private", "admin"}
    for seg in parts:
        s = seg.strip()
        if not s:
            continue
        if s.startswith("{") and s.endswith("}"):
            continue
        if s.lower() in technical:
            continue
        if re.fullmatch(r"v\d+", s.lower()):
            continue
        return s
    return None


def _path_has_params(path: str) -> bool:
    return re.search(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", path or "") is not None


def _parse_endpoint_binding(s: str) -> Optional[Tuple[str, str]]:
    # "POST /uploads"
    if not isinstance(s, str):
        return None
    m = re.fullmatch(r"\s*([A-Z]+)\s+(\S+)\s*", s)
    if not m:
        return None
    return (m.group(1).upper(), m.group(2))


def find_declared_capability_for_endpoint(endpoint: Dict[str, Any], spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Deterministic matching endpoint -> product_capability.

    Matching order:
    - endpoint.method + endpoint.path against capability.endpoints entries like "POST /x"
    - endpoint.func if capability declares endpoint_funcs (optional extension)
    - endpoint.operation_id if capability declares id or operation_id (optional)

    No keyword matching.
    """
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

        # endpoints binding
        eps = c.get("endpoints")
        if isinstance(eps, list):
            for ep in eps:
                binding = _parse_endpoint_binding(ep) if isinstance(ep, str) else None
                if binding and binding[0] == method and binding[1] == path:
                    return c

        # optional func binding (extension)
        funcs = c.get("endpoint_funcs")
        if func and isinstance(funcs, list) and func in [str(x) for x in funcs]:
            return c

        # optional operation_id binding (extension)
        cid = str(c.get("id") or "").strip()
        if operation_id and (operation_id == cid or operation_id == str(c.get("operation_id") or "").strip()):
            return c

    return None


def _interaction_kind_from_declared(cap: Dict[str, Any]) -> InteractionKind:
    kind = str(cap.get("kind") or "").strip()
    return kind if kind in _INTERACTION_KINDS else "unknown"


def _legacy_capability_label_from_declared(kind: InteractionKind, operation: str) -> Capability:
    """
    Only a compatibility label. Never used as the source-of-truth path.
    Keep minimal mapping; unknown by default.
    """
    op = (operation or "").strip().lower()
    if kind == "resource_state":
        if op in ("create", "add", "register"):
            return "create_resource"
        if op in ("list", "search", "query"):
            return "list_resource"
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
    if kind == "external_call":
        return "call_external_api"
    if kind == "event_ingress":
        return "receive_webhook"
    if kind == "event_egress":
        return "publish_event"
    if kind == "cache_state":
        if op in ("read", "get", "lookup"):
            return "cache_lookup"
        if op in ("write", "set"):
            return "cache_write"
        return "cache_state"
    if kind == "auth_gate":
        return "authenticate_request"
    if kind == "payload_transform":
        return "transform_payload"
    if kind == "report_generation":
        return "generate_report"
    return "unknown_operation"


def _detect_declared_external_dependencies(cap: Dict[str, Any], endpoint: Dict[str, Any], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Declared external dependencies only. Not technology hints.
    """
    out: List[Dict[str, Any]] = []
    for src in (cap.get("external_dependencies"), endpoint.get("source", {}).get("external_dependencies"), spec.get("integrations")):
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
    # dedup
    seen: set[str] = set()
    dedup: List[Dict[str, Any]] = []
    for d in out:
        key = json.dumps({"kind": d.get("kind"), "provider": d.get("provider"), "name": d.get("name")}, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(d)
    return dedup


def _build_runtime_strategy(
    *,
    interaction_kind: InteractionKind,
    input_contract: Dict[str, Any],
    output_contract: Dict[str, Any],
    state_contract: Dict[str, Any],
    external_dependencies: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Agnostic runtime strategy.

    default_mode:
      - local_fake | in_memory | local_filesystem | dry_run | none | safe_local
    real_mode:
      - lazy_adapter | not_generated | none
    """
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


def _implementation_plan_for_interaction_kind(kind: InteractionKind) -> List[str]:
    plans: Dict[str, List[str]] = {
        "resource_state": ["parse_request", "apply_state_operation_locally", "build_resource_response"],
        "file_io": ["parse_file_or_file_reference", "process_or_store_locally", "build_metadata_or_file_response"],
        "external_call": ["parse_request", "call_fake_client_by_default", "build_deterministic_response"],
        "event_ingress": ["parse_event", "record_event_locally", "return_ack"],
        "event_egress": ["build_event", "publish_to_fake_publisher", "return_event_status"],
        "cache_state": ["parse_key_value", "use_in_memory_cache", "return_hit_or_status"],
        "auth_gate": ["extract_credentials", "validate_with_local_policy", "return_auth_result_or_error"],
        "payload_transform": ["parse_payload", "transform_deterministically", "build_response"],
        "report_generation": ["parse_query", "build_deterministic_report", "return_report"],
        "unknown": ["parse_request_if_possible", "build_response_from_example_or_status", "avoid_external_side_effects"],
    }
    return plans.get(kind, plans["unknown"])


def fallback_infer_capability_from_endpoint_shape(endpoint: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    FALLBACK ONLY.
    Must not infer business intent from text.
    Uses only endpoint technical shape and declared dependencies.
    """
    method = str(endpoint.get("method") or "").upper().strip() or "GET"
    path = str(endpoint.get("path") or "").strip() or "/"
    req = endpoint.get("request") if isinstance(endpoint.get("request"), dict) else {}
    resp = endpoint.get("response") if isinstance(endpoint.get("response"), dict) else {}

    # inputs/outputs
    inputs: List[Dict[str, Any]] = []
    if isinstance(req, dict):
        rtype = str(req.get("type") or "json").strip().lower()
        if rtype:
            inputs.append({"kind": rtype, "name": "body"})
    if _path_has_params(path):
        inputs.append({"kind": "path", "name": "path_params"})

    outputs: List[Dict[str, Any]] = []
    if isinstance(resp, dict):
        otype = str(resp.get("type") or "json").strip().lower()
        outputs.append({"kind": otype or "json", "name": "response"})
    else:
        outputs.append({"kind": "json", "name": "response"})

    persistence = spec.get("persistence") if isinstance(spec.get("persistence"), dict) else {}
    state_required = bool(persistence.get("required") is True)

    external_deps = _detect_declared_external_dependencies({}, endpoint, spec)
    kind: InteractionKind = "unknown"
    if external_deps:
        kind = "external_call"
    elif state_required and method in {"POST", "PUT", "PATCH", "DELETE"}:
        kind = "resource_state"
    elif any(i.get("kind") in ("multipart", "file") for i in inputs) or any(o.get("kind") == "file" for o in outputs):
        kind = "file_io"

    operation = "handle_request"
    confidence = "low"
    evidence = "fallback_endpoint_shape"

    return {
        "interaction_kind": kind,
        "operation": operation,
        "confidence": confidence,
        "evidence": evidence,
        "inputs": inputs,
        "outputs": outputs,
        "state_required": state_required,
        "external_dependencies": external_deps,
    }


def build_implementation_contracts_from_spec(spec: dict) -> List[Dict[str, Any]]:
    if not isinstance(spec, dict):
        raise TypeError("spec must be a dict")

    endpoints = spec.get("endpoints") or []
    if not isinstance(endpoints, list):
        endpoints = []

    contracts: List[ImplementationContract] = []
    sources_debug: List[Dict[str, Any]] = []

    for ep in endpoints:
        if not isinstance(ep, dict):
            continue

        method = str(ep.get("method") or "").upper().strip() or "GET"
        path = str(ep.get("path") or "").strip() or "/"
        file_path = _normalize_path(ep.get("file") or "")
        func = str(ep.get("func") or "").strip()
        if not func:
            func = _snake(f"{method.lower()}_{_extract_resource_from_path(path) or path}")

        declared = find_declared_capability_for_endpoint(ep, spec)
        if declared:
            source_kind = "declared_product_capability"
            interaction_kind = _interaction_kind_from_declared(declared)
            operation = str(declared.get("operation") or "handle_request").strip() or "handle_request"
            goal = str(declared.get("goal") or "").strip()
            confidence = str(declared.get("confidence") or "high").strip()
            evidence = str(declared.get("evidence") or "").strip()

            inputs = declared.get("inputs") if isinstance(declared.get("inputs"), list) else []
            outputs = declared.get("outputs") if isinstance(declared.get("outputs"), list) else []
            state = declared.get("state") if isinstance(declared.get("state"), dict) else {}
            fallback = declared.get("fallback") if isinstance(declared.get("fallback"), dict) else {}
            external_deps = _detect_declared_external_dependencies(declared, ep, spec)

            input_contract = {"inputs": inputs}
            output_contract = {"outputs": outputs}
            state_contract = {
                "required": bool(state.get("required") is True),
                "kind": state.get("kind") or ("resource_state" if interaction_kind == "resource_state" else None),
            }
            dependency_contract = {"external_dependencies": external_deps}
            runtime_strategy = _build_runtime_strategy(
                interaction_kind=interaction_kind,
                input_contract=input_contract,
                output_contract=output_contract,
                state_contract=state_contract,
                external_dependencies=external_deps,
            )
            # override by declared fallback if provided
            if fallback.get("mode"):
                runtime_strategy = {**runtime_strategy, "default_mode": str(fallback.get("mode"))}

            implementation_plan = _implementation_plan_for_interaction_kind(interaction_kind)
            response_strategy = "from_declared_output_contract"
            validation_strategy = "schema_if_present_else_basic"
            test_strategy = "contract_based_smoke_tests"

            capability = _legacy_capability_label_from_declared(interaction_kind, operation)
            cap_id = str(declared.get("id") or "").strip() or None
        else:
            source_kind = "fallback_endpoint_shape"
            fb = fallback_infer_capability_from_endpoint_shape(ep, spec)

            interaction_kind = str(fb["interaction_kind"])
            operation = str(fb["operation"])
            confidence = str(fb["confidence"])
            evidence = str(fb["evidence"])
            goal = ""
            cap_id = None

            input_contract = {"inputs": fb["inputs"]}
            output_contract = {"outputs": fb["outputs"]}
            state_contract = {"required": bool(fb["state_required"]), "kind": None}
            dependency_contract = {"external_dependencies": fb["external_dependencies"]}

            runtime_strategy = _build_runtime_strategy(
                interaction_kind=interaction_kind,
                input_contract=input_contract,
                output_contract=output_contract,
                state_contract=state_contract,
                external_dependencies=fb["external_dependencies"],
            )
            implementation_plan = _implementation_plan_for_interaction_kind(interaction_kind)
            response_strategy = "response_example_or_status_ok"
            validation_strategy = "basic_validation_only"
            test_strategy = "safe_smoke_tests"

            # legacy label: conservative
            capability = "unknown_operation"
            if interaction_kind == "external_call":
                capability = "call_external_api"
            elif interaction_kind == "resource_state":
                # purely technical: treat as unknown_operation to avoid CRUD semantics
                capability = "unknown_operation"
            elif interaction_kind == "file_io":
                capability = "process_file"

        contract = ImplementationContract(
            file=file_path,
            method=method,
            path=path,
            func=func,
            capability_id=cap_id,
            interaction_kind=interaction_kind,
            operation=operation,
            goal=goal,
            confidence=confidence,
            evidence=evidence,
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
            },
        )
        contracts.append(contract)
        sources_debug.append(
            {
                "file": file_path,
                "method": method,
                "path": path,
                "func": func,
                "source_kind": source_kind,
                "confidence": confidence,
                "capability_id": cap_id,
            }
        )

    contracts_dict = [c.to_dict() for c in contracts]

    # debug persistence
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
