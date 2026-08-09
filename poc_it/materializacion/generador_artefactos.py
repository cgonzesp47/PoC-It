from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from poc_it.entrada.demo_progress import is_demo_mode
from poc_it.generador import spec_builder as _spec_builder
from poc_it.generador.file_contracts import (
    build_file_contracts_from_spec as _build_file_contracts_from_spec,
    file_contracts_to_dict as _file_contracts_to_dict,
)
from poc_it.generador.file_contracts_validation import (
    validate_file_contracts as _validate_file_contracts,
)
from poc_it.generador.file_planner import enrich_spec_files_for_implementation
from poc_it.generador.guardrails import (
    guardrails_por_spec,
    seleccionar_error_bloqueante,
)
from poc_it.generador.implementation_contracts import (
    build_implementation_contracts_from_spec,
)
from poc_it.generador.json_utils import extraer_json_tolerante
from poc_it.generador.prompts_guardrails import (
    build_repair_prompt_por_restriccion as _build_repair_prompt_por_restriccion,
)
from poc_it.generador.repair_loop import (
    aplicar_patch_en_memoria as _aplicar_patch_en_memoria,
    aplicar_repair_loop_imports,
)
from poc_it.generador.request_ir import build_request_ir_from_context
from poc_it.generador.restrictions import compilar_restricciones
from poc_it.generador.spec_traceability import (
    TraceabilityError as _TraceabilityError,
)
from poc_it.generador.spec_traceability import (
    validate_request_ir_to_spec_traceability as _validate_request_ir_to_spec_traceability,
)
from poc_it.generador.spec_validation import (
    SpecValidationError as _SpecValidationError,
)
from poc_it.generador.spec_validation import (
    completar_inits_en_files as _completar_inits_en_files,
)
from poc_it.generador.spec_validation import (
    persistir_spec_debug as _persistir_spec_debug,
)
from poc_it.generador.spec_validation import (
    repair_spec_deterministic as _repair_spec_deterministic,
)
from poc_it.generador.spec_validation import validate_spec as _validate_spec
from poc_it.generador.validators import (
    validar_imports_internos as _validar_imports_internos,
    validar_proyecto as _validar_proyecto,
)
from poc_it.infraestructura.llm_client import (
    chat_completion_json,
    solicitarJSONEstructurado,
)
from poc_it.materializacion.codegen.file_generator import FileContractGenerator
from poc_it.materializacion.codegen.models import CodegenIssue, GeneratedFile
from poc_it.materializacion.codegen.orchestrator import ContractFirstCodegen
from poc_it.materializacion.codegen.parsing import (
    parse_single_file_response as _parse_single_file_response_new,
)
from poc_it.materializacion.codegen.project_validation import (
    classify_generated_project as _classify_generated_project,
)
from poc_it.materializacion.codegen.project_validation import (
    validate_generated_project as _validate_generated_project,
)
from poc_it.materializacion.codegen.semantic_repair import (
    repair_generated_project as _repair_generated_project,
)
from poc_it.materializacion.codegen.structural_validation import (
    validate_generated_file_structure,
    validate_generated_files_against_file_contracts,
)


def _dump_debug_json(filename: str, payload: Any) -> None:
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _extract_single_generated_file(
    *,
    data: dict,
    expected_path: str,
    allow_empty_content: bool = False,
) -> Dict[str, str]:
    generated_file, issues = _parse_single_file_response_new(
        raw=json.dumps(data, ensure_ascii=False),
        expected_path=expected_path,
        allow_empty_content=allow_empty_content,
    )
    if generated_file is None:
        raise ValueError(
            ",".join(issue.code for issue in issues) or "single_file_extract_failed"
        )
    return {
        "path": generated_file.path,
        "content": generated_file.content,
    }


def _parse_single_file_response(
    *,
    raw: str,
    expected_path: str,
    allow_empty_content: bool = False,
):
    generated_file, issues = _parse_single_file_response_new(
        raw=raw,
        expected_path=expected_path,
        allow_empty_content=allow_empty_content,
    )
    if generated_file is None:
        legacy_codes = {
            "CODEGEN_MULTIPLE_FILES_RETURNED": "multiple_files_returned",
            "CODEGEN_JSON_PARSE_FAILED": "json_parse_failed",
            "CODEGEN_SINGLE_FILE_EXTRACT_FAILED": "single_file_extract_failed",
            "CODEGEN_PATH_MISMATCH": "single_file_extract_failed",
            "CODEGEN_EMPTY_CONTENT": "empty_content_not_allowed",
        }
        return None, [legacy_codes.get(issue.code, issue.code) for issue in issues]
    return {
        "path": generated_file.path,
        "content": generated_file.content,
    }, []


def _format_structural_issues(issues: List[CodegenIssue]) -> List[str]:
    return [f"[{issue.code}] {issue.message}" for issue in issues]


def _serialize_codegen_issue(issue: CodegenIssue) -> dict:
    return issue.to_dict()


def _to_generated_files(files_generados: List[Dict[str, str]]) -> List[GeneratedFile]:
    return [
        GeneratedFile(
            path=str(item.get("path") or "").replace("\\", "/"),
            content=str(item.get("content") or ""),
        )
        for item in files_generados
        if isinstance(item, dict) and item.get("path")
    ]


def _is_materializable_status(status: str) -> bool:
    return status in {
        "valid_local",
        "valid_integration_skeleton",
    }


def finalize_codegen_classification(
    *,
    preliminary_result: Dict[str, Any],
    runtime_tests_passed: bool,
) -> Dict[str, Any]:
    spec = preliminary_result.get("spec")
    files = preliminary_result.get("files")
    validation_report = preliminary_result.get("validation_report")

    if not isinstance(spec, dict):
        return dict(preliminary_result)

    if not isinstance(files, list):
        return dict(preliminary_result)

    file_contracts = []
    if isinstance(validation_report, dict):
        file_contracts = validation_report.get("file_contracts") or []

    if not isinstance(file_contracts, list) or not file_contracts:
        return dict(preliminary_result)

    classification = _classify_generated_project(
        generated_files=_to_generated_files(files),
        file_contracts=file_contracts,
        spec=spec,
        static_validation_passed=True,
        runtime_tests_passed=runtime_tests_passed,
    )
    final_status = str(classification.get("status") or "invalid")

    result = dict(preliminary_result)
    result["codegen_status"] = final_status
    result["external_connectivity_verified"] = classification["external_connectivity_verified"]
    result["runtime_tests_passed"] = classification.get("runtime_tests_passed")
    result["materializable"] = _is_materializable_status(final_status)

    report = dict(validation_report) if isinstance(validation_report, dict) else {}
    report["codegen_status"] = final_status
    report["external_connectivity_verified"] = classification["external_connectivity_verified"]
    report["codegen_message"] = classification.get("message")
    report["runtime_tests_passed"] = classification.get("runtime_tests_passed")
    result["codegen_message"] = classification.get("message")
    result["validation_report"] = report
    return result


MAX_SEMANTIC_REPAIR_ROUNDS = 3


def _validate_generated_outputs(
    *,
    spec: dict,
    files_generados: List[Dict[str, str]],
    allowed_paths: Set[str],
    intentos: int,
    file_contracts: Optional[List[dict]] = None,
) -> tuple[bool, dict]:
    report = {
        "ast_ok": True,
        "file_contracts_ok": True,
        "imports_ok": True,
        "guardrails_ok": True,
        "errors": [],
        "warnings": [],
        "static_validation_passed": True,
        "codegen_status": "invalid",
        "external_connectivity_verified": False,
        "codegen_message": None,
        "file_contracts": file_contracts,
    }

    file_contracts = file_contracts or []

    if not _validar_proyecto(files_generados):
        report["ast_ok"] = False
        report["static_validation_passed"] = False
        report["errors"].append("AST_INVALID")
        return False, report

    structural_issues = validate_generated_files_against_file_contracts(
        files_generados=files_generados,
        file_contracts=file_contracts,
    )
    if structural_issues:
        report["file_contracts_ok"] = False
        report["static_validation_passed"] = False
        report["errors"].append("FILE_CONTRACTS_VIOLATIONS")
        report["errors"].extend(
            [
                f"[{issue.code}] {issue.path}: {issue.message}"
                for issue in structural_issues
            ]
        )
        return False, report

    project_issues = _validate_generated_project(
        generated_files=_to_generated_files(files_generados),
        file_contracts=file_contracts,
        spec=spec,
    )
    blocking_project_issues = [
        issue for issue in project_issues if issue.severity == "error"
    ]
    if blocking_project_issues:
        repaired_files, remaining_project_issues = _repair_generated_project(
            generated_files=_to_generated_files(files_generados),
            file_contracts=file_contracts,
            spec=spec,
            descripcion_global="",
            contexto_normalizado={},
            llm_call=solicitarJSONEstructurado,
            max_rounds=MAX_SEMANTIC_REPAIR_ROUNDS,
        )
        files_generados[:] = [
            {"path": file.path, "content": file.content}
            for file in repaired_files
        ]
        project_issues = _validate_generated_project(
            generated_files=_to_generated_files(files_generados),
            file_contracts=file_contracts,
            spec=spec,
        )
        blocking_project_issues = [
            issue for issue in project_issues if issue.severity == "error"
        ]
        if remaining_project_issues or blocking_project_issues:
            final_issues = remaining_project_issues or blocking_project_issues
            report["errors"].append("PROJECT_VALIDATION_FAILED")
            report["errors"].extend(
                [
                    f"[{issue.code}] {issue.path}: {issue.message}"
                    for issue in final_issues
                ]
            )
            _dump_debug_json(
                "codegen_project_issues.json",
                [
                    _serialize_codegen_issue(issue)
                    for issue in final_issues
                ],
            )
            report["static_validation_passed"] = False
            return False, report

    ok_imports, e_imports = _validar_imports_internos(files_generados, allowed_paths)
    if not ok_imports:
        report["imports_ok"] = False
        report["errors"].append("IMPORTS_INVALID")
        report["errors"].extend([str(item) for item in e_imports or []])

        patched_ok, _repair_paths = aplicar_repair_loop_imports(
            spec=spec,
            files_generados=files_generados,
            allowed_paths=allowed_paths,
            errores_imports=e_imports,
            chat_completion_json=chat_completion_json,
            intentos=intentos,
        )
        if not patched_ok:
            report["static_validation_passed"] = False
            report["errors"].append("IMPORTS_REPAIR_NOT_CONVERGED")
            return False, report

    guard = guardrails_por_spec(spec, files_generados)
    if guard.warnings:
        report["warnings"].extend(list(guard.warnings))
    if not guard.ok:
        report["guardrails_ok"] = False
        report["errors"].append("GUARDRAILS_FAILED")
        report["errors"].extend([str(item) for item in guard.errors or []])

        if guard.repair_paths:
            sel = seleccionar_error_bloqueante(guard.errors)
            if sel:
                target_path, target_msg = sel
                prompt_fix = _build_repair_prompt_por_restriccion(
                    spec=spec,
                    full_errors=guard.errors,
                    target_error_path=target_path,
                    target_error_msg=target_msg,
                    repair_paths=guard.repair_paths,
                    files_generados=files_generados,
                )
                raw = chat_completion_json(
                    prompt=prompt_fix,
                    system=None,
                    temperature=0.1,
                    max_tokens=1600,
                    fase="generacion_codigo",
                )
                data = extraer_json_tolerante(raw) or {}
                cand = data.get("files")
                if isinstance(cand, list) and cand:
                    files_before_repair = copy.deepcopy(files_generados)
                    candidate_files = copy.deepcopy(files_generados)
                    _aplicar_patch_en_memoria(candidate_files, cand)
                    candidate_ok, candidate_report = _validate_generated_outputs(
                        spec=spec,
                        files_generados=candidate_files,
                        allowed_paths=allowed_paths,
                        intentos=intentos,
                        file_contracts=file_contracts,
                    )
                    if candidate_ok:
                        files_generados[:] = candidate_files
                        guard = guardrails_por_spec(spec, files_generados)
                    else:
                        files_generados[:] = files_before_repair
                        report["warnings"].append(
                            "[GUARDRAIL-REPAIR] Repair rechazado; rollback."
                        )
                        report["warnings"].extend(
                            [
                                f"[GUARDRAIL-REPAIR] {item}"
                                for item in (candidate_report.get("errors") or [])
                            ]
                        )

        if not guard.ok:
            report["static_validation_passed"] = False
            report["errors"].append("GUARDRAILS_REPAIR_NOT_CONVERGED")
            return False, report

    classification = _classify_generated_project(
        generated_files=_to_generated_files(files_generados),
        file_contracts=file_contracts,
        spec=spec,
        static_validation_passed=bool(
            report["ast_ok"]
            and report["file_contracts_ok"]
            and report["imports_ok"]
            and report["guardrails_ok"]
            and not report["errors"]
        ),
        runtime_tests_passed=None,
    )
    report["codegen_status"] = classification["status"]
    report["external_connectivity_verified"] = classification["external_connectivity_verified"]
    report["codegen_message"] = classification.get("message")
    report["runtime_tests_passed"] = classification.get("runtime_tests_passed")

    return classification["status"] != "invalid", report


def _generate_files_from_contracts(
    *,
    spec: dict,
    file_contracts: List[dict],
    descripcion_global: str,
    contexto_normalizado: dict | None,
    intentos: int,
) -> List[Dict[str, str]]:
    generator = FileContractGenerator(
        llm_call=solicitarJSONEstructurado,
        max_attempts=max(2, int(intentos)),
    )
    orchestrator = ContractFirstCodegen(file_generator=generator)
    project_result = orchestrator.generate_project(
        spec=spec,
        file_contracts=file_contracts,
        descripcion_global=descripcion_global,
        contexto_normalizado=contexto_normalizado or {},
    )
    if project_result.status != "valid":
        raise RuntimeError(
            ",".join(issue.code for issue in project_result.issues)
            or "file_contract_generation_failed"
        )
    return [
        {"path": file.path, "content": file.content}
        for file in project_result.files
    ]


def _generar_desde_spec_validado(
    *,
    spec: dict,
    contexto_normalizado: dict | None,
    intentos: int,
    files_iniciales: Optional[List[Dict[str, str]]] = None,
    descripcion_global: str = "",
) -> Dict[str, Any]:
    implementation_contracts = build_implementation_contracts_from_spec(spec)
    plan_result = enrich_spec_files_for_implementation(
        spec,
        implementation_contracts=implementation_contracts,
    )
    spec = plan_result.spec
    files_plan = _completar_inits_en_files(spec.get("files", []))
    spec["files"] = files_plan
    allowed_paths = set(files_plan)

    file_contracts = _build_file_contracts_from_spec(
        spec,
        implementation_contracts=implementation_contracts,
    )
    file_contracts_dict = _file_contracts_to_dict(file_contracts)
    file_contract_errors = _validate_file_contracts(
        spec=spec,
        implementation_contracts=implementation_contracts,
        file_contracts=file_contracts_dict,
    )

    _dump_debug_json("implementation_contracts.json", implementation_contracts)
    _dump_debug_json(
        "file_plan.json",
        {
            "generated_paths": plan_result.generated_paths,
            "implementation_files": spec.get("implementation_files", []),
        },
    )
    _dump_debug_json("file_contracts.json", file_contracts_dict)
    _dump_debug_json(
        "file_contract_validation.json",
        [error.__dict__ for error in file_contract_errors],
    )

    if file_contract_errors:
        return {
            "files": [],
            "spec": spec,
            "codegen_status": "file_contract_validation_failed",
            "materializable": False,
            "codegen_errors": [
                f"{error.code}:{error.path}" if error.path else error.code
                for error in file_contract_errors
            ],
            "validation_report": {
                "stage": "file_contract_gate",
                "ok": False,
                "errors": [error.__dict__ for error in file_contract_errors],
            },
        }

    spec["restrictions"] = compilar_restricciones(
        spec,
        contexto_normalizado,
        intentos=intentos,
    )

    if isinstance(files_iniciales, list) and files_iniciales:
        files_generados = [
            {
                "path": (f.get("path") or "").replace("\\", "/"),
                "content": (f.get("content") or ""),
            }
            for f in files_iniciales
            if isinstance(f, dict) and f.get("path")
        ]
        ok_final, report = _validate_generated_outputs(
            spec=spec,
            files_generados=files_generados,
            allowed_paths=allowed_paths,
            intentos=intentos,
            file_contracts=file_contracts_dict,
        )
        if not ok_final:
            return {"files": [], "spec": spec, "validation_report": report}
        return {"files": files_generados, "spec": spec, "validation_report": report}

    try:
        files_generados = _generate_files_from_contracts(
            spec=spec,
            file_contracts=file_contracts_dict,
            descripcion_global=descripcion_global,
            contexto_normalizado=contexto_normalizado,
            intentos=intentos,
        )
    except Exception as exc:
        return {
            "files": [],
            "spec": spec,
            "codegen_status": "failed",
            "materializable": False,
            "codegen_errors": [str(exc) or "file_contract_generation_failed"],
            "validation_report": {
                "stage": "contract_first_codegen",
                "ok": False,
                "errors": [str(exc) or "file_contract_generation_failed"],
            },
        }

    ok_final, report = _validate_generated_outputs(
        spec=spec,
        files_generados=files_generados,
        allowed_paths=allowed_paths,
        intentos=intentos,
        file_contracts=file_contracts_dict,
    )
    _dump_debug_json("final_validation_report.json", report)

    if not ok_final:
        final_status = str(report.get("codegen_status") or "invalid")
        return {
            "files": files_generados,
            "spec": spec,
            "codegen_status": final_status,
            "external_connectivity_verified": report.get("external_connectivity_verified", False),
            "codegen_message": report.get("codegen_message"),
            "runtime_tests_passed": report.get("runtime_tests_passed"),
            "materializable": _is_materializable_status(final_status),
            "draft_files_available": bool(files_generados),
            "codegen_errors": ["final_validation_failed"],
            "validation_report": report,
        }

    final_status = str(report.get("codegen_status") or "invalid")
    return {
        "files": files_generados,
        "spec": spec,
        "codegen_status": final_status,
        "external_connectivity_verified": report.get("external_connectivity_verified", False),
        "codegen_message": report.get("codegen_message"),
        "runtime_tests_passed": report.get("runtime_tests_passed"),
        "materializable": _is_materializable_status(final_status),
        "codegen_errors": [],
        "validation_report": report,
    }


def generar_proyecto_desde_spec(
    *,
    spec: dict,
    descripcion_global: str,
    contexto_normalizado: dict | None = None,
    intentos: int = 2,
    files_iniciales: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    if not isinstance(spec, dict) or not spec:
        return generar_proyecto_completo(
            descripcion_global=descripcion_global,
            contexto_normalizado=contexto_normalizado,
            intentos=intentos,
        )

    return _generar_desde_spec_validado(
        spec=spec,
        contexto_normalizado=contexto_normalizado,
        intentos=intentos,
        files_iniciales=files_iniciales,
        descripcion_global=descripcion_global,
    )


def generar_proyecto_completo(
    descripcion_global: str,
    contexto_normalizado: dict | None = None,
    intentos: int = 2,
) -> Dict[str, Any]:
    def _serialize_validation_errors(errors: List[_SpecValidationError]) -> List[dict]:
        return [e.__dict__ for e in errors]

    def _serialize_traceability_errors(errors: List[_TraceabilityError]) -> List[dict]:
        return [e.__dict__ for e in errors]

    try:
        _dump_debug_json("request_ir_input_context.json", contexto_normalizado)
        req_ir = build_request_ir_from_context(
            contexto_normalizado,
            descripcion_global=descripcion_global,
        )
        try:
            from poc_it.generador.request_ir import request_ir_to_dict

            _dump_debug_json("request_ir.json", request_ir_to_dict(req_ir))
        except Exception:
            pass

        spec = _spec_builder.build_spec_from_request_ir(req_ir)
        traceability_errors = _validate_request_ir_to_spec_traceability(req_ir, spec)
        if traceability_errors:
            _dump_debug_json(
                "spec_traceability_errors.json",
                _serialize_traceability_errors(traceability_errors),
            )
            raise ValueError(
                "Traceability validation failed: "
                + "; ".join(error.code for error in traceability_errors)
            )

        _dump_debug_json("spec_base.json", spec)
    except Exception as exc:
        raise RuntimeError(f"No se pudo construir SPEC determinista: {exc}") from exc

    if isinstance(spec, dict):
        _persistir_spec_debug(
            nombre_archivo=f"spec_base_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            spec=spec,
            descripcion_global=descripcion_global,
            contexto_normalizado=contexto_normalizado,
        )
        errors0 = _validate_spec(spec)
        fatals0 = [e for e in errors0 if e.severity == "fatal"]
        warns0 = [e for e in errors0 if e.severity == "warning"]

        if fatals0:
            spec_repaired, repair_changes = _repair_spec_deterministic(spec)
            _persistir_spec_debug(
                nombre_archivo=f"spec_repaired_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                spec=spec_repaired,
                descripcion_global=descripcion_global,
                contexto_normalizado=contexto_normalizado,
            )
            _dump_debug_json(
                "spec_repair_changes.json",
                _serialize_validation_errors(repair_changes),
            )
            errors1 = _validate_spec(spec_repaired)
            fatals1 = [e for e in errors1 if e.severity == "fatal"]
            warns1 = [e for e in errors1 if e.severity == "warning"]
            if fatals1:
                _dump_debug_json(
                    "spec_validation_errors.json",
                    _serialize_validation_errors(errors1),
                )
                spec_repaired["status"] = "degraded"
                return {
                    "files": [],
                    "spec": spec_repaired,
                    "spec_errors": _serialize_validation_errors(errors1),
                }
            spec = spec_repaired
            if warns1:
                _dump_debug_json(
                    "spec_validation_warnings.json",
                    _serialize_validation_errors(warns1),
                )
            spec["status"] = "valid"
        else:
            if warns0:
                _dump_debug_json(
                    "spec_validation_warnings.json",
                    _serialize_validation_errors(warns0),
                )
            spec["status"] = "valid"

    return _generar_desde_spec_validado(
        spec=spec,
        contexto_normalizado=contexto_normalizado,
        intentos=intentos,
        descripcion_global=descripcion_global,
    )


__all__ = [
    "_extract_single_generated_file",
    "_format_structural_issues",
    "_generate_files_from_contracts",
    "_parse_single_file_response",
    "generar_proyecto_completo",
    "generar_proyecto_desde_spec",
    "is_demo_mode",
    "validate_generated_file_structure",
    "validate_generated_files_against_file_contracts",
]
