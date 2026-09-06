from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Set

from poc_it.generador.prompts_file_contracts import build_prompt_file_contract_fix

from .debug import (
    dump_codegen_candidate_for_debug,
    dump_codegen_candidate_metadata,
    dump_codegen_contract_errors,
    dump_codegen_raw_for_debug,
)
from .models import CodegenIssue, GeneratedFile
from .parsing import parse_single_file_response
from .project_validation import validate_generated_project
from .semantic_validation import validate_generated_file_semantics
from .structural_validation import validate_generated_file_structure


class IssueRepairClass(str, Enum):
    CONTRACT_BLOCKER = "contract_blocker"
    IMPLEMENTATION_REPAIRABLE = "implementation_repairable"
    IMPLEMENTATION_NON_REPAIRABLE = "implementation_non_repairable"
    UNKNOWN = "unknown"


CONTRACT_STRUCTURE_BLOCKERS: Set[str] = {
    "FILE_INTERFACE_SYMBOL_COLLISION",
    "FILE_ACTION_OWNER_MISSING",
    "FILE_INTERNAL_CALL_TARGET_MISSING",
    "FILE_INTERNAL_CALL_SYMBOL_MISSING",
    "FILE_INTERNAL_CALL_ACTION_MISMATCH",
    "PROJECT_INTERNAL_SYMBOL_CONTRACT_MISSING",
    "PROJECT_REQUIRED_ACTION_OWNER_MISSING",
    "PROJECT_REQUIRED_ACTION_INTERFACE_MISSING",
    "PROJECT_INTERNAL_MODULE_MISSING",
    "PROJECT_INTEGRATION_MODULE_MISSING",
    "PROJECT_UNDECLARED_ROUTE",
}

ENDPOINT_REPAIRABLE_CODES: Set[str] = {
    "PROJECT_INTERNAL_SYMBOL_NOT_IMPORTED",
    "PROJECT_INTERNAL_SYMBOL_NOT_CALLED",
    "PROJECT_REQUIRED_ACTION_NOT_REACHABLE",
    "PROJECT_DECLARED_ERROR_UNHANDLED",
    "PROJECT_ROUTE_MISSING",
    "PROJECT_ROUTE_DUPLICATED_PREFIX",
    "CODE_INTERNAL_CALL_IMPORT_MISSING",
    "CODE_REQUIRED_INTERNAL_CALL_NOT_INVOKED",
    "CODE_FAKE_SUCCESS_RESPONSE",
    "CODE_DECLARED_ERROR_UNHANDLED",
    "CODE_ENDPOINT_ROUTE_MISSING",
    "CODE_ENDPOINT_ROUTE_MISMATCH",
    "ENDPOINT_REQUEST_TRANSPORT_MISMATCH",
    "ENDPOINT_RESPONSE_CONTRACT_MISMATCH",
    "CODE_ENDPOINT_LOGGING_REQUIRED",
    "FILE_CONTRACT_SYMBOL_MISSING",
    "FILE_CONTRACT_CALL_SIGNATURE_MISMATCH",
    "FILE_CONTRACT_DATA_FLOW_MISMATCH",
    "INTERNAL_IMPORT_SYMBOL_MISSING",
    "CONFIGURATION_FIELD_MISSING",
    "ROUTE_PATH_MISMATCH",
    "HTTP_REQUEST_CONTRACT_MISMATCH",
}

INTEGRATION_REPAIRABLE_CODES: Set[str] = {
    "PROJECT_INTERNAL_SYMBOL_CODE_MISSING",
    "PROJECT_REQUIRED_ACTION_IMPLEMENTATION_MISSING",
    "PROJECT_INTEGRATION_OPERATION_MISSING",
    "PROJECT_INTEGRATION_CONFIGURATION_UNUSED",
    "PROJECT_CONFIGURATION_CONSUMER_MISSING",
    "PROJECT_INTEGRATION_TECHNOLOGY_UNUSED",
    "CODE_REQUIRED_INTERFACE_MISSING",
    "CODE_REQUIRED_INTERFACE_PARAMETER_MISSING",
    "CODE_REQUIRED_ACTION_STUB",
    "CODE_REQUIRED_CONFIGURATION_UNUSED",
    "CODE_INTEGRATION_SDK_UNUSED",
    "CODE_EXTERNAL_OPERATION_MISSING",
    "CODE_EXTERNAL_OPERATION_CONSTANT_RETURN",
    "CONFIGURATION_FIELD_MISSING",
    "FILE_CONTRACT_DATA_FLOW_MISMATCH",
    "UNRESOLVED_PYTHON_SYMBOL",
}

CONFIG_REPAIRABLE_CODES: Set[str] = {
    "CODE_CONFIG_KEY_MISSING",
    "CODE_CONFIG_GET_SETTINGS_MISSING",
    "PROJECT_CONFIGURATION_DECLARATION_MISSING",
    "PROJECT_CONFIGURATION_CONSUMER_MISSING",
    "CONFIGURATION_FIELD_MISSING",
}

REQUIREMENTS_REPAIRABLE_CODES: Set[str] = {
    "CODE_REQUIREMENT_MISSING",
    "PROJECT_DEPENDENCY_MISSING",
}

ALL_REPAIRABLE_CODES: Set[str] = (
    ENDPOINT_REPAIRABLE_CODES
    | INTEGRATION_REPAIRABLE_CODES
    | CONFIG_REPAIRABLE_CODES
    | REQUIREMENTS_REPAIRABLE_CODES
)

IMPLEMENTATION_REPAIRABLE_CODES: Set[str] = ALL_REPAIRABLE_CODES

REPAIRABLE_KINDS_BY_CODE: dict[str, frozenset[str]] = {
    "FILE_CONTRACT_CALL_SIGNATURE_MISMATCH": frozenset(
        {"endpoint", "service", "repository", "integration"}
    ),
    "CONFIGURATION_FIELD_MISSING": frozenset(
        {"endpoint", "integration", "service", "repository", "config"}
    ),
    "UNRESOLVED_PYTHON_SYMBOL": frozenset(
        {"endpoint", "integration", "service", "repository", "config", "router"}
    ),
    "FILE_CONTRACT_DATA_FLOW_MISMATCH": frozenset(
        {"endpoint", "integration", "service", "repository"}
    ),
    "PROJECT_ROUTE_DUPLICATED_PREFIX": frozenset({"endpoint", "router"}),
    "ROUTE_PATH_MISMATCH": frozenset({"endpoint", "router"}),
}

_PLACEHOLDER_PATTERNS = (
    "... existing code ...",
    "# existing code",
    "# todo: implement",
    "pass  # todo",
)


@dataclass(frozen=True)
class GeneratedInterfaceSummary:
    path: str
    module: str
    exported_symbols: List[str]
    provided_interfaces: List[Dict[str, Any]]


def summarize_generated_interfaces(
    *,
    generated_files: List[GeneratedFile],
    file_contracts: List[Dict[str, Any]],
) -> List[GeneratedInterfaceSummary]:
    files_by_path = {item.path.replace("\\", "/"): item for item in generated_files}
    summaries: List[GeneratedInterfaceSummary] = []
    from .orchestrator import sort_file_contracts_for_generation

    for contract in sort_file_contracts_for_generation(file_contracts):
        if not isinstance(contract, dict):
            continue
        path = str(contract.get("path") or "").replace("\\", "/")
        if path not in files_by_path:
            continue
        provided_interfaces = [
            item
            for item in (contract.get("provided_interfaces") or [])
            if isinstance(item, dict)
        ]
        exported_symbols = [
            str(item.get("symbol") or "").strip()
            for item in provided_interfaces
            if str(item.get("symbol") or "").strip()
        ]
        for symbol in contract.get("required_symbols") or []:
            normalized = str(symbol).strip()
            if normalized and normalized not in exported_symbols:
                exported_symbols.append(normalized)
        summaries.append(
            GeneratedInterfaceSummary(
                path=path,
                module=_module_from_path(path),
                exported_symbols=exported_symbols,
                provided_interfaces=provided_interfaces,
            )
        )
    return summaries


def repair_paths_for_issues(
    issues: List[CodegenIssue],
    *,
    file_contracts: List[Dict[str, Any]],
) -> List[str]:
    contracts_by_path = {
        str(contract.get("path") or "").replace("\\", "/").strip(): contract
        for contract in file_contracts
        if isinstance(contract, dict) and str(contract.get("path") or "").strip()
    }
    issues_by_path: dict[str, list[CodegenIssue]] = {}

    for issue in issues:
        for target_path in _repair_targets_for_issue(
            issue,
            file_contracts=file_contracts,
        ):
            issues_by_path.setdefault(target_path, []).append(issue)

    repair_paths: List[str] = []
    for path, path_issues in issues_by_path.items():
        contract = contracts_by_path.get(path)
        if not isinstance(contract, dict):
            continue

        classes = [
            classify_issue_for_repair(issue, contract=contract) for issue in path_issues
        ]
        if IssueRepairClass.CONTRACT_BLOCKER in classes:
            continue

        reparable = [
            issue
            for issue, issue_class in zip(path_issues, classes)
            if issue_class == IssueRepairClass.IMPLEMENTATION_REPAIRABLE
        ]
        if reparable:
            repair_paths.append(path)

    return repair_paths


def _repair_targets_for_issue(
    issue: CodegenIssue,
    *,
    file_contracts: List[Dict[str, Any]],
) -> List[str]:
    del file_contracts
    path = str(issue.path or "").replace("\\", "/").strip()
    if not path:
        return []

    code = str(issue.code or "").strip()
    if code in {
        "CONFIGURATION_FIELD_MISSING",
        "FILE_CONTRACT_CALL_SIGNATURE_MISMATCH",
        "UNRESOLVED_PYTHON_SYMBOL",
    }:
        return [path]

    return [path]


def classify_issue_for_repair(
    issue: CodegenIssue,
    *,
    contract: dict[str, object] | None,
) -> IssueRepairClass:
    code = str(issue.code or "").strip()

    if code in CONTRACT_STRUCTURE_BLOCKERS:
        return IssueRepairClass.CONTRACT_BLOCKER

    if code in IMPLEMENTATION_REPAIRABLE_CODES:
        if _issue_is_repairable_for_contract_kind(issue, contract=contract):
            return IssueRepairClass.IMPLEMENTATION_REPAIRABLE
        return IssueRepairClass.IMPLEMENTATION_NON_REPAIRABLE

    return IssueRepairClass.UNKNOWN


def _issue_is_repairable_for_contract_kind(
    issue: CodegenIssue,
    *,
    contract: dict[str, object] | None,
) -> bool:
    code = str(issue.code or "").strip()
    allowed_kinds = REPAIRABLE_KINDS_BY_CODE.get(code)
    if allowed_kinds is None:
        kind = str((contract or {}).get("kind") or "").strip()
        if code in ENDPOINT_REPAIRABLE_CODES:
            return kind == "endpoint"
        if code in INTEGRATION_REPAIRABLE_CODES:
            return kind == "integration"
        if code in CONFIG_REPAIRABLE_CODES:
            return kind == "config"
        if code in REQUIREMENTS_REPAIRABLE_CODES:
            return kind == "requirements"
        return False

    kind = str((contract or {}).get("kind") or "").strip()
    return kind in allowed_kinds


def _blocking_issues(
    issues: List[CodegenIssue],
) -> List[CodegenIssue]:
    return [issue for issue in issues if issue.severity == "error"]


def _contains_codegen_placeholder(content: str) -> bool:
    lowered = str(content or "").lower()
    return any(pattern.lower() in lowered for pattern in _PLACEHOLDER_PATTERNS)


def _invalid_configuration_fields(issues: List[CodegenIssue], *, path: str) -> List[str]:
    normalized_path = str(path or "").replace("\\", "/").strip()
    fields = {
        str(issue.details.get("field") or "").strip()
        for issue in issues
        if str(issue.path or "").replace("\\", "/").strip() == normalized_path
        and str(issue.code or "").strip() == "CONFIGURATION_FIELD_MISSING"
        and isinstance(issue.details, dict)
        and str(issue.details.get("field") or "").strip()
    }
    return sorted(fields)


def _persist_rejected_candidate(
    *,
    path: str,
    attempt: int,
    candidate_content: str,
    before_issues: List[CodegenIssue],
    after_issues: List[CodegenIssue],
    rejected_attempts_by_path: Dict[str, List[Dict[str, Any]]] | None = None,
) -> None:
    if rejected_attempts_by_path is not None:
        rejected_attempts_by_path.setdefault(path, []).append(
            {
                "content": candidate_content,
                "issues": [str(issue.message or "").strip() for issue in after_issues if issue.message],
            }
        )
    candidate_filename = dump_codegen_candidate_for_debug(
        path=path,
        attempt=attempt,
        content=candidate_content,
    )
    before_fields = _invalid_configuration_fields(before_issues, path=path)
    after_fields = _invalid_configuration_fields(after_issues, path=path)
    dump_codegen_candidate_metadata(
        path=path,
        attempt=attempt,
        payload={
            "path": path,
            "round": attempt,
            "attempt": attempt,
            "target_issues": [
                {
                    "code": str(issue.code or "").strip(),
                    "symbol": (
                        issue.details.get("symbol")
                        if isinstance(issue.details, dict)
                        else None
                    ),
                    "field": (
                        issue.details.get("field")
                        if isinstance(issue.details, dict)
                        else None
                    ),
                }
                for issue in before_issues
            ],
            "issue_codes_before": sorted({str(issue.code or "").strip() for issue in before_issues}),
            "issue_codes_after": sorted({str(issue.code or "").strip() for issue in after_issues}),
            "issues_before": [issue.to_dict() for issue in before_issues],
            "issues_after": [issue.to_dict() for issue in after_issues],
            "accepted": False,
            "candidate_persisted": bool(candidate_filename),
            "candidate_filename": candidate_filename,
            "field_before": before_fields[0] if len(before_fields) == 1 else None,
            "allowed_fields": sorted(
                {
                    str(field).strip()
                    for issue in before_issues
                    if str(issue.path or "").replace("\\", "/").strip() == str(path or "").replace("\\", "/").strip()
                    and isinstance(issue.details, dict)
                    for field in (issue.details.get("allowed_fields") or [])
                    if str(field).strip()
                }
            ),
            "same_issue_persisted": bool(before_fields and after_fields and set(before_fields) == set(after_fields)),
        },
    )


def _dump_repair_not_converged(
    *,
    path: str,
    attempts: int,
    original_issues: List[CodegenIssue],
    final_issues: List[CodegenIssue],
) -> None:
    original_fields = _invalid_configuration_fields(original_issues, path=path)
    final_fields = _invalid_configuration_fields(final_issues, path=path)
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "path": path,
            "attempts": attempts,
            "issues_before": [issue.to_dict() for issue in original_issues],
            "issues_after": [issue.to_dict() for issue in final_issues],
            "issue_identities_before": [_issue_identity(issue) for issue in original_issues],
            "issue_identities_after": [_issue_identity(issue) for issue in final_issues],
            "same_issue_persisted": (
                {_issue_identity(issue) for issue in original_issues}
                == {_issue_identity(issue) for issue in final_issues}
            ),
        }
        if original_fields:
            payload.update(
                {
                    "original_issue": "CONFIGURATION_FIELD_MISSING",
                    "field": original_fields[0] if len(original_fields) == 1 else None,
                    "allowed_fields": sorted(
                        {
                            str(field).strip()
                            for issue in original_issues
                            if str(issue.path or "").replace("\\", "/").strip() == str(path or "").replace("\\", "/").strip()
                            and isinstance(issue.details, dict)
                            for field in (issue.details.get("allowed_fields") or [])
                            if str(field).strip()
                        }
                    ),
                    "configuration_field_persisted": bool(final_fields and set(final_fields) == set(original_fields)),
                }
            )
        normalized_path = (
            str(path or "").replace("/", "_").replace("\\", "_").replace(".", "_")
        )
        (debug_dir / f"codegen_repair_not_converged_{normalized_path}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _validate_python_content(path: str, content: str) -> list[str]:
    if not str(path or "").endswith(".py"):
        return []

    try:
        ast.parse(content, filename=path)
    except SyntaxError as exc:
        return [f"{path}: SyntaxError line={exc.lineno} msg={exc.msg}"]

    return []


def repair_generated_project(
    *,
    generated_files: List[GeneratedFile],
    file_contracts: List[Dict[str, Any]],
    spec: Dict[str, Any],
    descripcion_global: str,
    contexto_normalizado: Dict[str, Any],
    llm_call: Callable[..., str],
    max_rounds: int = 3,
) -> tuple[List[GeneratedFile], List[CodegenIssue]]:
    del descripcion_global, contexto_normalizado
    files_by_path = {file.path.replace("\\", "/"): file for file in generated_files}
    blocking_before = _blocking_issues(
        validate_generated_project(
            generated_files=list(files_by_path.values()),
            file_contracts=file_contracts,
            spec=spec,
        )
    )
    previous_blocking_count = len(blocking_before)
    rejected_attempts_by_path: Dict[str, List[Dict[str, Any]]] = {}

    for round_index in range(1, max(1, int(max_rounds)) + 1):
        if not blocking_before:
            break

        repair_paths = repair_paths_for_issues(
            blocking_before,
            file_contracts=file_contracts,
        )
        if not repair_paths:
            break

        applied_paths: List[str] = []
        for path in repair_paths:
            contract = next(
                (
                    item
                    for item in file_contracts
                    if isinstance(item, dict)
                    and str(item.get("path") or "").replace("\\", "/") == path
                ),
                None,
            )
            if contract is None:
                continue

            current_file = files_by_path.get(path)
            if current_file is None:
                continue

            path_issues = []
            for issue in blocking_before:
                if path in _repair_targets_for_issue(issue, file_contracts=file_contracts):
                    path_issues.append(issue)
            if not path_issues:
                continue

            from .orchestrator import related_contracts_for

            related_contracts = related_contracts_for(
                contract=contract,
                file_contracts=file_contracts,
                spec=spec,
            )
            summaries = summarize_generated_interfaces(
                generated_files=list(files_by_path.values()),
                file_contracts=file_contracts,
            )
            related_paths = {
                str(item.get("path") or "").replace("\\", "/")
                for item in related_contracts
                if isinstance(item, dict)
            }
            prompt = build_prompt_file_contract_fix(
                spec=spec,
                file_contract=contract,
                previous_content=current_file.content,
                errors=[issue.message for issue in path_issues],
                related_contracts=related_contracts,
                structured_issues=_build_structured_issues(
                    path_issues=path_issues,
                    target_contract=contract,
                    related_contracts=related_contracts,
                ),
                generated_interface_summaries=[
                    summary.__dict__
                    for summary in summaries
                    if summary.path != path
                    and (
                        summary.path in related_paths
                        or any(
                            provided.get("symbol") == issue.symbol
                            for issue in path_issues
                            for provided in summary.provided_interfaces
                        )
                    )
                ],
                previous_rejected_attempts=rejected_attempts_by_path.get(path),
            )
            attempt_number = round_index
            raw = llm_call(
                prompt=prompt,
                system=None,
                temperature=0.1,
                max_tokens=2600,
                fase="generacion_codigo_project_repair",
                provider_hint="code-gen",
            )
            repaired_file, parse_issues = parse_single_file_response(
                raw=raw,
                expected_path=path,
                allow_empty_content=False,
            )
            if parse_issues:
                dump_codegen_raw_for_debug(path=path, intento=round_index, raw=raw)
                dump_codegen_contract_errors(path=path, issues=parse_issues)
                continue

            assert repaired_file is not None
            repaired_file = _preserve_existing_action_lines(
                current_file=current_file,
                repaired_file=repaired_file,
                contract=contract,
            )
            candidate_file = repaired_file
            candidate_ast_issues = _validate_python_content(
                candidate_file.path,
                candidate_file.content,
            )
            if _contains_codegen_placeholder(candidate_file.content):
                blocking_local_issues = [
                    CodegenIssue(
                        code="CODEGEN_PLACEHOLDER_CONTENT",
                        path=candidate_file.path,
                        message="El repair contiene placeholders incompletos y se rechaza.",
                        severity="error",
                    )
                ]
                dump_codegen_contract_errors(path=path, issues=blocking_local_issues)
                _persist_rejected_candidate(
                    path=path,
                    attempt=attempt_number,
                    candidate_content=candidate_file.content,
                    before_issues=path_issues,
                    after_issues=blocking_local_issues,
                    rejected_attempts_by_path=rejected_attempts_by_path,
                )
                continue
            if candidate_ast_issues:
                blocking_local_issues = [
                    CodegenIssue(
                        code="CODEGEN_AST_INVALID",
                        path=candidate_file.path,
                        message=message,
                        severity="error",
                    )
                    for message in candidate_ast_issues
                ]
                dump_codegen_contract_errors(path=path, issues=blocking_local_issues)
                _persist_rejected_candidate(
                    path=path,
                    attempt=attempt_number,
                    candidate_content=candidate_file.content,
                    before_issues=path_issues,
                    after_issues=blocking_local_issues,
                    rejected_attempts_by_path=rejected_attempts_by_path,
                )
                continue

            structural_issues = validate_generated_file_structure(
                generated_file=candidate_file,
                file_contract=contract,
            )
            semantic_issues = validate_generated_file_semantics(
                generated_file=candidate_file,
                file_contract=contract,
            )
            local_issues = [*structural_issues, *semantic_issues]
            blocking_local_issues = _blocking_issues(local_issues)
            if blocking_local_issues:
                dump_codegen_contract_errors(path=path, issues=blocking_local_issues)
                _persist_rejected_candidate(
                    path=path,
                    attempt=attempt_number,
                    candidate_content=candidate_file.content,
                    before_issues=path_issues,
                    after_issues=blocking_local_issues,
                    rejected_attempts_by_path=rejected_attempts_by_path,
                )
                continue

            candidate_files_by_path = dict(files_by_path)
            candidate_files_by_path[path] = candidate_file
            candidate_project_issues = _blocking_issues(
                validate_generated_project(
                    generated_files=list(candidate_files_by_path.values()),
                    file_contracts=file_contracts,
                    spec=spec,
                )
            )
            if not _candidate_repair_is_acceptable(
                before_issues=blocking_before,
                after_issues=candidate_project_issues,
                target_path=path,
                target_issues=path_issues,
            ):
                dump_codegen_contract_errors(path=path, issues=candidate_project_issues)
                _persist_rejected_candidate(
                    path=path,
                    attempt=attempt_number,
                    candidate_content=candidate_file.content,
                    before_issues=path_issues,
                    after_issues=candidate_project_issues,
                    rejected_attempts_by_path=rejected_attempts_by_path,
                )
                continue

            files_by_path[path] = candidate_file
            applied_paths.append(path)

        blocking_after = _blocking_issues(
            validate_generated_project(
                generated_files=list(files_by_path.values()),
                file_contracts=file_contracts,
                spec=spec,
            )
        )
        _dump_repair_round(
            round_index=round_index,
            before_issues=blocking_before,
            after_issues=blocking_after,
            repair_paths=repair_paths,
            applied_paths=applied_paths,
            file_contracts=file_contracts,
        )
        if len(blocking_after) > previous_blocking_count:
            break
        blocking_before = blocking_after
        previous_blocking_count = len(blocking_after)

    for path in repair_paths_for_issues(blocking_before, file_contracts=file_contracts):
        _dump_repair_not_converged(
            path=path,
            attempts=max(1, int(max_rounds)),
            original_issues=blocking_before,
            final_issues=blocking_before,
        )

    return list(files_by_path.values()), blocking_before


def _module_from_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    if not normalized.endswith(".py"):
        return ""
    module = normalized[:-3].replace("/", ".")
    if module.endswith(".__init__"):
        module = module[: -len(".__init__")]
    return module


def _is_repairable_issue(
    issue: CodegenIssue,
    contract: Dict[str, Any],
) -> bool:
    return (
        classify_issue_for_repair(
            issue,
            contract=contract,
        )
        == IssueRepairClass.IMPLEMENTATION_REPAIRABLE
    )


def _build_structured_issues(
    *,
    path_issues: List[CodegenIssue],
    target_contract: Dict[str, Any],
    related_contracts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    related_by_path = {
        str(item.get("path") or "").replace("\\", "/"): item
        for item in related_contracts
        if isinstance(item, dict)
    }
    structured: List[Dict[str, Any]] = []

    for issue in path_issues:
        entry = issue.to_dict()
        code = str(issue.code or "").strip()

        if code == "CONFIGURATION_FIELD_MISSING":
            entry.update(
                {
                    "target_file": str(target_contract.get("path") or ""),
                    "invalid_configuration_field": (
                        issue.details.get("field")
                        if isinstance(issue.details, dict)
                        else None
                    ),
                    "configuration_contract": dict(
                        target_contract.get("configuration_access") or {}
                    ),
                    "authentication_constraints": dict(
                        target_contract.get("authentication_constraints") or {}
                    ),
                    "authentication_runtime_contract": dict(
                        target_contract.get("authentication_runtime_contract") or {}
                    ),
                    "strict_instruction": (
                        "Rewrite the target file so that the invalid configuration "
                        "field is completely removed, no replacement configuration "
                        "field is invented, only allowed configuration fields are "
                        "referenced, authentication remains compatible with the "
                        "runtime authentication contract, config.py is not modified, "
                        "SPEC and FileContracts are not modified, and unrelated valid "
                        "behavior is preserved."
                    ),
                }
            )

        if code in {
            "FILE_CONTRACT_DATA_FLOW_MISMATCH",
            "FILE_CONTRACT_CALL_SIGNATURE_MISMATCH",
        }:
            entry.update(
                {
                    "target_file_contract": dict(target_contract),
                    "related_file_contracts": [
                        dict(contract)
                        for contract in related_contracts
                        if _contract_is_related_to_issue(issue, contract)
                    ],
                    "interface_contract": _interface_contract_for_issue(
                        issue=issue,
                        target_contract=target_contract,
                        related_by_path=related_by_path,
                    ),
                    "strict_instruction": (
                        "Do not modify the contract. Make the caller conform to the "
                        "declared callee interface. Do not add new configuration fields. "
                        "Do not change authentication constraints. Preserve all unrelated behavior."
                    ),
                }
            )

        if code == "UNRESOLVED_PYTHON_SYMBOL":
            symbol = (
                issue.details.get("symbol")
                if isinstance(issue.details, dict)
                else None
            )
            entry.update(
                {
                    "target_file": str(target_contract.get("path") or ""),
                    "unresolved_symbol": symbol,
                    "target_file_contract": dict(target_contract),
                    "target_source": None,
                    "declared_dependencies": [],
                    "related_interfaces": [
                        dict(contract)
                        for contract in related_contracts
                        if _contract_is_related_to_issue(issue, contract)
                    ],
                    "strict_instruction": (
                        "The generated Python file references a symbol that is not available in its scope.\n\n"
                        f"Missing symbol:\n{symbol}\n\n"
                        "Rewrite ONLY the target file so the symbol is correctly resolved.\n\n"
                        "You may:\n"
                        "- add a valid import supported by the declared dependencies;\n"
                        "- change the implementation to use an already imported valid symbol;\n"
                        "- introduce a local helper if it is compatible with the FileContract.\n\n"
                        "You MUST NOT:\n"
                        "- invent new dependencies;\n"
                        "- modify requirements;\n"
                        "- modify SPEC;\n"
                        "- modify FileContracts;\n"
                        "- add configuration fields;\n"
                        "- remove required behavior;\n"
                        "- silence or bypass the validator.\n\n"
                        "Preserve all unrelated valid behavior."
                    ),
                }
            )

        structured.append(entry)

    return structured


def _contract_is_related_to_issue(
    issue: CodegenIssue,
    contract: Dict[str, Any],
) -> bool:
    symbol = str(issue.symbol or "").strip()
    if not symbol:
        return False
    for provided in contract.get("provided_interfaces") or []:
        if not isinstance(provided, dict):
            continue
        if str(provided.get("symbol") or "").strip() == symbol:
            return True
    return False


def _interface_contract_for_issue(
    *,
    issue: CodegenIssue,
    target_contract: Dict[str, Any],
    related_by_path: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    symbol = str(issue.symbol or "").strip()
    for call in target_contract.get("required_internal_calls") or []:
        if not isinstance(call, dict):
            continue
        if str(call.get("symbol") or "").strip() != symbol:
            continue
        module = str(call.get("module") or "").strip()
        related_path = _module_to_path(module)
        callee_contract = related_by_path.get(related_path, {})
        provided = next(
            (
                item
                for item in (callee_contract.get("provided_interfaces") or [])
                if isinstance(item, dict)
                and str(item.get("symbol") or "").strip() == symbol
            ),
            {},
        )
        return {
            "required_internal_call": dict(call),
            "provided_interface": dict(provided) if isinstance(provided, dict) else {},
            "data_contracts": {
                **dict(target_contract.get("data_contracts") or {}),
                **dict(callee_contract.get("data_contracts") or {}),
            },
        }
    return {}


def _candidate_repair_is_acceptable(
    *,
    before_issues: List[CodegenIssue],
    after_issues: List[CodegenIssue],
    target_path: str,
    target_issues: List[CodegenIssue],
) -> bool:
    target_identities = {_issue_identity(issue) for issue in target_issues}
    after_identities = {_issue_identity(issue) for issue in after_issues}
    if any(identity in after_identities for identity in target_identities):
        return False

    before_identities = {_issue_identity(issue) for issue in before_issues}
    if after_identities == before_identities:
        return False

    before_for_path = {
        _issue_identity(issue)
        for issue in before_issues
        if str(issue.path or "").replace("\\", "/").strip() == target_path
    }
    for issue in after_issues:
        identity = _issue_identity(issue)
        if identity in before_for_path:
            continue
        if str(issue.path or "").replace("\\", "/").strip() == target_path:
            return False

    before_invalid_fields = {
        str(issue.details.get("field") or "").strip()
        for issue in target_issues
        if str(issue.code or "").strip() == "CONFIGURATION_FIELD_MISSING"
        and isinstance(issue.details, dict)
        and str(issue.details.get("field") or "").strip()
    }
    after_invalid_fields = {
        str(issue.details.get("field") or "").strip()
        for issue in after_issues
        if str(issue.path or "").replace("\\", "/").strip() == target_path
        and str(issue.code or "").strip() == "CONFIGURATION_FIELD_MISSING"
        and isinstance(issue.details, dict)
        and str(issue.details.get("field") or "").strip()
    }
    if before_invalid_fields and after_invalid_fields:
        return False

    return True


def _issue_identity(
    issue: CodegenIssue,
) -> tuple[str, str, str]:
    details = issue.details if isinstance(issue.details, dict) else {}
    return (
        str(issue.code or "").strip(),
        str(issue.path or "").replace("\\", "/").strip(),
        str(details.get("symbol") or details.get("field") or issue.symbol or "").strip(),
    )


def _module_to_path(module: str) -> str:
    normalized = str(module or "").strip()
    if not normalized:
        return ""
    return normalized.replace(".", "/") + ".py"


def _preserve_existing_action_lines(
    *,
    current_file: GeneratedFile,
    repaired_file: GeneratedFile,
    contract: Dict[str, Any],
) -> GeneratedFile:
    missing_lines: List[str] = []
    current_lines = current_file.content.splitlines()
    repaired_content = repaired_file.content
    for action in contract.get("actions") or []:
        if not isinstance(action, dict):
            continue
        action_id = str(action.get("id") or "").strip()
        action_kind = str(action.get("kind") or "").strip()
        if not action_id or action_kind != "internal_processing":
            continue
        if action_id not in current_file.content or action_id in repaired_content:
            continue
        for line in current_lines:
            if action_id in line and line not in missing_lines:
                missing_lines.append(line)
    if not missing_lines:
        return repaired_file
    suffix = "\n".join(missing_lines).rstrip()
    content = repaired_content.rstrip() + "\n\n" + suffix + "\n"
    return GeneratedFile(path=repaired_file.path, content=content)


def _dump_repair_round(
    *,
    round_index: int,
    before_issues: List[CodegenIssue],
    after_issues: List[CodegenIssue],
    repair_paths: List[str],
    applied_paths: List[str],
    file_contracts: List[Dict[str, Any]],
) -> None:
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        contracts_by_path = {
            str(contract.get("path") or "").replace("\\", "/").strip(): contract
            for contract in file_contracts
            if isinstance(contract, dict) and str(contract.get("path") or "").strip()
        }
        payload = {
            "round": round_index,
            "repair_paths": repair_paths,
            "applied_paths": applied_paths,
            "before_issues": [
                {
                    **issue.to_dict(),
                    "repair_class": classify_issue_for_repair(
                        issue,
                        contract=contracts_by_path.get(
                            str(issue.path or "").replace("\\", "/").strip()
                        ),
                    ).value,
                }
                for issue in before_issues
            ],
            "after_issues": [
                {
                    **issue.to_dict(),
                    "repair_class": classify_issue_for_repair(
                        issue,
                        contract=contracts_by_path.get(
                            str(issue.path or "").replace("\\", "/").strip()
                        ),
                    ).value,
                }
                for issue in after_issues
            ],
            "before_issue_identities": [_issue_identity(issue) for issue in before_issues],
            "after_issue_identities": [_issue_identity(issue) for issue in after_issues],
        }
        (debug_dir / f"codegen_repair_round_{round_index}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
