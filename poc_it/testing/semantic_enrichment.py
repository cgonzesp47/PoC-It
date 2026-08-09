from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class SemanticEnrichmentContext:
    scope_id: str
    normalized_openapi: Dict[str, Any]
    handlers: Dict[str, str]
    dependencies: Dict[str, List[str]]
    observed_methods: Dict[str, List[str]]
    related_models: Dict[str, List[str]]
    deterministic_cases: Dict[str, List[Dict[str, Any]]]


@dataclass(frozen=True)
class SemanticEnrichmentResult:
    semantic_cases: List[Dict[str, Any]] = field(default_factory=list)
    stateful_scenarios: List[Dict[str, Any]] = field(default_factory=list)
    dependency_behaviors: List[Dict[str, Any]] = field(default_factory=list)
    expected_interactions: List[Dict[str, Any]] = field(default_factory=list)
    uncertainties: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    raw_payload: Optional[Dict[str, Any]] = None


class SemanticEnrichmentValidationError(ValueError):
    pass


class SemanticEnrichmentService:
    _TOP_LEVEL_KEYS = {
        "semantic_cases",
        "stateful_scenarios",
        "dependency_behaviors",
        "expected_interactions",
        "uncertainties",
    }

    def __init__(self, *, provider: Optional[Callable[[SemanticEnrichmentContext], Any]] = None) -> None:
        self.provider = provider

    def enrich(self, context: SemanticEnrichmentContext) -> SemanticEnrichmentResult:
        if self.provider is None:
            return SemanticEnrichmentResult(warnings=["semantic enrichment provider not configured"])

        raw_response = self.provider(context)
        payload = self._coerce_payload(raw_response)
        validated = self._validate_payload(payload, context)
        return SemanticEnrichmentResult(
            semantic_cases=validated["semantic_cases"],
            stateful_scenarios=validated["stateful_scenarios"],
            dependency_behaviors=validated["dependency_behaviors"],
            expected_interactions=validated["expected_interactions"],
            uncertainties=validated["uncertainties"],
            warnings=validated.get("warnings", []),
            raw_payload=payload,
        )

    def _coerce_payload(self, raw_response: Any) -> Dict[str, Any]:
        if isinstance(raw_response, str):
            try:
                raw_response = json.loads(raw_response)
            except Exception as exc:
                raise SemanticEnrichmentValidationError(f"LLM output is not valid JSON: {exc}") from exc
        if not isinstance(raw_response, dict):
            raise SemanticEnrichmentValidationError("LLM output must be a JSON object")
        unknown = sorted(set(raw_response.keys()) - self._TOP_LEVEL_KEYS)
        if unknown:
            raise SemanticEnrichmentValidationError(f"LLM output contains unsupported top-level keys: {unknown}")
        normalized: Dict[str, Any] = {}
        for key in self._TOP_LEVEL_KEYS:
            value = raw_response.get(key, [])
            if not isinstance(value, list):
                raise SemanticEnrichmentValidationError(f"LLM output field '{key}' must be a list")
            normalized[key] = value
        return normalized

    def _validate_payload(self, payload: Dict[str, Any], context: SemanticEnrichmentContext) -> Dict[str, List[Dict[str, Any]]]:
        available_operations = set(context.handlers.keys())
        available_dependencies = {dep for deps in context.dependencies.values() for dep in deps}
        available_methods = {method for methods in context.observed_methods.values() for method in methods}
        available_paths = set((context.normalized_openapi.get("paths") or {}).keys())
        valid_statuses = self._build_valid_statuses(context.normalized_openapi)

        validated: Dict[str, List[Dict[str, Any]]] = {key: [] for key in self._TOP_LEVEL_KEYS}
        validated["warnings"] = []

        for item in payload["semantic_cases"]:
            entry = self._require_dict(item, "semantic_cases")
            operation_id = self._require_known_operation(entry, available_operations, "semantic_cases")
            status_code = entry.get("expected_status")
            if status_code is not None and (not isinstance(status_code, int) or status_code not in valid_statuses.get(operation_id, set())):
                raise SemanticEnrichmentValidationError(
                    f"semantic_cases for '{operation_id}' declares incompatible status {status_code}"
                )
            confidence = self._require_confidence(entry, "semantic_cases")
            evidence = self._require_string_list(entry.get("evidence", []), "semantic_cases.evidence")
            if confidence < 0.5 and not evidence:
                raise SemanticEnrichmentValidationError(
                    f"semantic_cases for '{operation_id}' has low confidence without evidence"
                )
            self._assert_no_unknown_response_fields(entry, context.normalized_openapi, operation_id)
            validated["semantic_cases"].append(entry)

        for item in payload["stateful_scenarios"]:
            entry = self._require_dict(item, "stateful_scenarios")
            confidence = self._require_confidence(entry, "stateful_scenarios")
            evidence = self._require_string_list(entry.get("evidence", []), "stateful_scenarios.evidence")
            if confidence < 0.5 and not evidence:
                raise SemanticEnrichmentValidationError("stateful_scenarios with low confidence require evidence")
            operations = self._require_string_list(entry.get("operations", []), "stateful_scenarios.operations")
            if not operations:
                raise SemanticEnrichmentValidationError("stateful_scenarios require at least one operation")
            unknown_operations = [operation_id for operation_id in operations if operation_id not in available_operations]
            if unknown_operations:
                validated["warnings"].append(
                    "semantic enrichment ignored: "
                    f"scenario {str(entry.get('scenario_id') or entry.get('name') or 'unknown')!r} "
                    f"references unknown operations {unknown_operations}"
                )
            validated["stateful_scenarios"].append(entry)

        for item in payload["dependency_behaviors"]:
            entry = self._require_dict(item, "dependency_behaviors")
            dependency_name = str(entry.get("dependency") or "").strip()
            method_name = str(entry.get("method") or "").strip()
            if dependency_name not in available_dependencies:
                raise SemanticEnrichmentValidationError(
                    f"dependency_behaviors references non-isolable dependency '{dependency_name}'"
                )
            if method_name and method_name not in available_methods:
                raise SemanticEnrichmentValidationError(
                    f"dependency_behaviors references unknown method '{method_name}'"
                )
            validated["dependency_behaviors"].append(entry)

        for item in payload["expected_interactions"]:
            entry = self._require_dict(item, "expected_interactions")
            operation_id = self._require_known_operation(entry, available_operations, "expected_interactions")
            dependency_name = str(entry.get("dependency") or "").strip()
            method_name = str(entry.get("method") or "").strip()
            if dependency_name and dependency_name not in available_dependencies:
                raise SemanticEnrichmentValidationError(
                    f"expected_interactions for '{operation_id}' references non-isolable dependency '{dependency_name}'"
                )
            if method_name and method_name not in available_methods:
                raise SemanticEnrichmentValidationError(
                    f"expected_interactions for '{operation_id}' references unknown method '{method_name}'"
                )
            validated["expected_interactions"].append(entry)

        for item in payload["uncertainties"]:
            entry = self._require_dict(item, "uncertainties")
            maybe_operation = str(entry.get("operation_id") or "").strip()
            maybe_path = str(entry.get("path") or "").strip()
            if maybe_operation and maybe_operation not in available_operations:
                raise SemanticEnrichmentValidationError(
                    f"uncertainties references unknown operation '{maybe_operation}'"
                )
            if maybe_path and maybe_path not in available_paths:
                raise SemanticEnrichmentValidationError(
                    f"uncertainties references unknown path '{maybe_path}'"
                )
            validated["uncertainties"].append(entry)

        return validated

    def _build_valid_statuses(self, openapi: Dict[str, Any]) -> Dict[str, set[int]]:
        statuses_by_operation: Dict[str, set[int]] = {}
        paths = openapi.get("paths") or {}
        if not isinstance(paths, dict):
            return statuses_by_operation
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, definition in methods.items():
                if not isinstance(definition, dict):
                    continue
                operation_id = str(definition.get("operationId") or f"{str(method).lower()}:{path}")
                responses = definition.get("responses") or {}
                valid_codes = {
                    int(code)
                    for code in responses.keys()
                    if isinstance(code, str) and code.isdigit()
                }
                statuses_by_operation[operation_id] = valid_codes
        return statuses_by_operation

    def _assert_no_unknown_response_fields(self, entry: Dict[str, Any], openapi: Dict[str, Any], operation_id: str) -> None:
        referenced_fields = entry.get("response_fields")
        if not isinstance(referenced_fields, list) or not referenced_fields:
            return
        allowed_fields = self._response_fields_for_operation(openapi, operation_id)
        for field_name in referenced_fields:
            if str(field_name) not in allowed_fields:
                raise SemanticEnrichmentValidationError(
                    f"semantic_cases for '{operation_id}' invents response field '{field_name}'"
                )

    def _response_fields_for_operation(self, openapi: Dict[str, Any], operation_id: str) -> set[str]:
        paths = openapi.get("paths") or {}
        if not isinstance(paths, dict):
            return set()
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, definition in methods.items():
                if not isinstance(definition, dict):
                    continue
                current_operation_id = str(definition.get("operationId") or f"{str(method).lower()}:{path}")
                if current_operation_id != operation_id:
                    continue
                responses = definition.get("responses") or {}
                for response in responses.values():
                    if not isinstance(response, dict):
                        continue
                    content = response.get("content") or {}
                    if not isinstance(content, dict):
                        continue
                    for media in content.values():
                        if not isinstance(media, dict):
                            continue
                        schema = media.get("schema") or {}
                        if not isinstance(schema, dict):
                            continue
                        props = schema.get("properties") or {}
                        if isinstance(props, dict):
                            return {str(key) for key in props.keys()}
        return set()

    def _require_dict(self, value: Any, section: str) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise SemanticEnrichmentValidationError(f"{section} entries must be objects")
        return value

    def _require_known_operation(self, entry: Dict[str, Any], available_operations: set[str], section: str) -> str:
        operation_id = str(entry.get("operation_id") or "").strip()
        if operation_id not in available_operations:
            raise SemanticEnrichmentValidationError(f"{section} references unknown operation '{operation_id}'")
        return operation_id

    def _require_confidence(self, entry: Dict[str, Any], section: str) -> float:
        confidence = entry.get("confidence", 1.0)
        try:
            confidence = float(confidence)
        except Exception as exc:
            raise SemanticEnrichmentValidationError(f"{section} confidence must be numeric") from exc
        if confidence < 0.0 or confidence > 1.0:
            raise SemanticEnrichmentValidationError(f"{section} confidence must be between 0 and 1")
        return confidence

    def _require_string_list(self, value: Any, section: str) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise SemanticEnrichmentValidationError(f"{section} must be a list")
        out = [str(item).strip() for item in value if str(item).strip()]
        return out
