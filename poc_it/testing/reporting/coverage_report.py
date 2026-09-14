from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Optional


class CoverageStatus(str, Enum):
    PLANNED = "PLANNED"
    RENDERED = "RENDERED"
    COLLECTED = "COLLECTED"
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_SUPPORTED = "NOT_SUPPORTED"


def build_initial_coverage_report(
    *,
    nombre_proyecto: str,
    plan: Any,
    runtime_contracts: Optional[dict],
    runtime_facts: Optional[dict],
    tests_patch: Dict[str, str],
) -> Dict[str, Any]:
    endpoints = _extract_endpoints(plan)
    scenarios = _extract_scenarios(plan)
    scenario_index = _build_scenario_index(scenarios)
    interaction_index = _build_interaction_index(plan)
    runtime_integrations = _extract_runtime_integrations(runtime_facts)

    endpoint_entries = [
        _build_endpoint_coverage_entry(
            endpoint=endpoint,
            scenario_index=scenario_index,
            interaction_index=interaction_index,
            tests_patch=tests_patch,
        )
        for endpoint in endpoints
    ]

    unvalidated_integrations = sorted(
        integration for integration in runtime_integrations if not _integration_validated(integration, endpoint_entries)
    )

    return {
        "project": nombre_proyecto,
        "strategy": "contract-first",
        "application": {
            "compilation": {"status": CoverageStatus.RENDERED.value, "reason": "python source was rendered for pytest execution"},
            "importation": {"status": CoverageStatus.RENDERED.value, "reason": "startup/import tests were rendered"},
            "lifespan": {"status": CoverageStatus.RENDERED.value, "reason": "lifespan validation tests were rendered"},
            "openapi": {"status": CoverageStatus.RENDERED.value, "reason": "OpenAPI validation tests were rendered"},
        },
        "endpoints": endpoint_entries,
        "global": {
            "endpoints_detected": len(endpoint_entries),
            "endpoints_level_1": sum(1 for entry in endpoint_entries if (entry.get("levels") or {}).get("openapi") == CoverageStatus.RENDERED.value),
            "endpoints_level_2": sum(1 for entry in endpoint_entries if (entry.get("levels") or {}).get("hermetic") == CoverageStatus.RENDERED.value),
            "scenarios_level_3": sum(1 for entry in endpoint_entries if (entry.get("levels") or {}).get("stateful") == CoverageStatus.RENDERED.value),
            "integrations_not_validated": unvalidated_integrations,
        },
        "artifacts": {
            "rendered_test_files": sorted(list((tests_patch or {}).keys())),
            "has_harness": bool("tests/conftest.py" in (tests_patch or {})),
            "allowed_dependency_overrides": sorted(list((runtime_contracts or {}).get("allowed_dependency_overrides") or []))
            if isinstance(runtime_contracts, dict)
            else [],
            "report_generated_without_test_execution": True,
        },
    }


def update_coverage_report_after_pytest(
    *,
    report: Dict[str, Any],
    rendered_manifest: Optional[dict] = None,
    pytest_results: Optional[dict] = None,
) -> Dict[str, Any]:
    updated = json.loads(json.dumps(report or {}))
    rendered_manifest = rendered_manifest if isinstance(rendered_manifest, dict) else {}
    pytest_results = pytest_results if isinstance(pytest_results, dict) else {}

    outcomes_by_operation = rendered_manifest.get("outcomes_by_operation")
    if not isinstance(outcomes_by_operation, dict):
        outcomes_by_operation = pytest_results.get("outcomes_by_operation")
    if not isinstance(outcomes_by_operation, dict):
        outcomes_by_operation = {}

    failures_by_operation = rendered_manifest.get("failures_by_operation")
    if not isinstance(failures_by_operation, dict):
        failures_by_operation = pytest_results.get("failures_by_operation")
    if not isinstance(failures_by_operation, dict):
        failures_by_operation = {}

    for endpoint in updated.get("endpoints") or []:
        operation_id = str(endpoint.get("operation_id") or "").strip()
        levels = endpoint.get("levels") or {}
        outcome = outcomes_by_operation.get(operation_id, {}) if operation_id else {}
        failure_cases = failures_by_operation.get(operation_id, []) if operation_id else []
        if not isinstance(outcome, dict):
            outcome = {}
        if not isinstance(failure_cases, list):
            failure_cases = [failure_cases]

        for level_name, current_status in list(levels.items()):
            execution_outcome = str(outcome.get(level_name) or "").upper().strip()
            if execution_outcome == CoverageStatus.PASSED.value:
                levels[level_name] = CoverageStatus.PASSED.value
            elif execution_outcome == CoverageStatus.FAILED.value:
                levels[level_name] = CoverageStatus.FAILED.value
            elif current_status == CoverageStatus.RENDERED.value:
                levels[level_name] = CoverageStatus.COLLECTED.value

        if failure_cases:
            endpoint["failed_cases"] = [str(item) for item in failure_cases if str(item).strip()]

    updated["artifacts"] = dict(updated.get("artifacts") or {})
    updated["artifacts"]["report_generated_without_test_execution"] = False
    return updated


def render_test_coverage_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# TEST_COVERAGE",
        "",
        f"Proyecto: **{report.get('project', 'unknown')}**",
        "",
        "## Aplicación",
        "",
    ]

    application = report.get("application") or {}
    for key in ["compilation", "importation", "lifespan", "openapi"]:
        item = application.get(key) or {}
        lines.append(f"- {_status_icon(item.get('status'))} **{key}** — {item.get('reason', '')}")

    lines.extend(["", "## Endpoints", ""])
    for endpoint in report.get("endpoints") or []:
        levels = endpoint.get("levels") or {}
        lines.append(f"### {endpoint.get('method')} {endpoint.get('path')}")
        lines.append("")
        lines.append(f"- {_status_icon(levels.get('startup'))} Startup")
        lines.append(f"- {_status_icon(levels.get('openapi'))} Contrato OpenAPI")
        lines.append(f"- {_status_icon(levels.get('hermetic'))} Ejecución hermética")
        lines.append(f"- {_status_icon(levels.get('stateful'))} Escenario stateful")
        failed_cases = endpoint.get("failed_cases") or []
        if failed_cases:
            lines.append(f"- Casos fallidos: {', '.join(failed_cases)}")
        limitations = endpoint.get("limitations") or []
        if limitations:
            lines.append(f"- Limitaciones: {'; '.join(limitations)}")
        else:
            lines.append("- Limitaciones: ninguna reportada")
        lines.append("")

    global_section = report.get("global") or {}
    lines.extend(
        [
            "## Resumen global",
            "",
            f"- Endpoints detectados: **{global_section.get('endpoints_detected', 0)}**",
            f"- Endpoints nivel 1: **{global_section.get('endpoints_level_1', 0)}**",
            f"- Endpoints nivel 2: **{global_section.get('endpoints_level_2', 0)}**",
            f"- Escenarios nivel 3: **{global_section.get('scenarios_level_3', 0)}**",
        ]
    )

    integrations = global_section.get("integrations_not_validated") or []
    if integrations:
        lines.append(f"- Integraciones no validadas: **{', '.join(integrations)}**")
    else:
        lines.append("- Integraciones no validadas: ninguna")

    lines.extend(
        [
            "",
            "## Interpretación",
            "",
            "- ◌ planned: previsto en el plan pero aún no renderizado.",
            "- ~ rendered/collected: generado o recogido por pytest, pero no aprobado todavía.",
            "- ✓ passed: validación ejecutada con éxito.",
            "- ✗ failed: validación ejecutada con fallo asociado a casos concretos.",
            "- — not applicable / not supported: ese nivel no corresponde al endpoint o no puede validarse.",
            "",
            "Este informe distingue cobertura planificada, renderizada y realmente validada tras pytest.",
            "",
        ]
    )
    return "\n".join(lines)


def to_pretty_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _extract_endpoints(plan: Any) -> List[Dict[str, Any]]:
    extracted: List[Dict[str, Any]] = []
    try:
        for endpoint in getattr(plan, "endpoints", []) or []:
            method = getattr(endpoint, "method", None)
            path = getattr(endpoint, "path", None)
            if not method or not path:
                continue
            extracted.append(
                {
                    "operation_id": str(getattr(endpoint, "operation_id", None) or f"{str(method).lower()}_{str(path).strip('/').replace('/', '_') or 'root'}"),
                    "method": str(method).upper(),
                    "path": str(path),
                    "level": getattr(endpoint, "level", None),
                    "reason": getattr(endpoint, "reason", None),
                    "hermetic": getattr(endpoint, "hermetic", None),
                    "required_response_keys": list(getattr(endpoint, "required_response_keys", None) or []),
                    "allowed_statuses": list(getattr(endpoint, "allowed_statuses", None) or []),
                }
            )
    except Exception:
        extracted = []
    return extracted


def _extract_scenarios(plan: Any) -> List[Dict[str, Any]]:
    scenarios: List[Dict[str, Any]] = []
    raw = getattr(plan, "scenario_plans", None)
    if isinstance(raw, list):
        for scenario in raw:
            if isinstance(scenario, dict):
                scenarios.append(scenario)
                continue
            try:
                operations = []
                for operation in getattr(scenario, "operations", []) or []:
                    operations.append(
                        {
                            "operation_id": getattr(operation, "operation_id", None),
                            "request": {
                                "method": getattr(getattr(operation, "request", None), "method", None),
                                "path_template": getattr(getattr(operation, "request", None), "path_template", None),
                            },
                        }
                    )
                scenarios.append(
                    {
                        "scenario_id": getattr(scenario, "scenario_id", None),
                        "name": getattr(scenario, "name", None),
                        "level": getattr(scenario, "level", None),
                        "operations": operations,
                        "confidence": getattr(scenario, "confidence", 0.0),
                    }
                )
            except Exception:
                continue
    return scenarios


def _build_scenario_index(scenarios: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    index: Dict[str, List[Dict[str, Any]]] = {}
    for scenario in scenarios:
        for operation in scenario.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            request = operation.get("request") if isinstance(operation.get("request"), dict) else {}
            method = str(request.get("method") or "").upper().strip()
            path = str(request.get("path_template") or request.get("path") or "").strip()
            if not method or not path:
                continue
            index.setdefault(_endpoint_key(method, path), []).append(scenario)
    return index


def _build_interaction_index(plan: Any) -> Dict[str, bool]:
    index: Dict[str, bool] = {}
    endpoint_plans = getattr(plan, "endpoint_plans", None)
    if not isinstance(endpoint_plans, list):
        return index

    for endpoint_plan in endpoint_plans:
        if isinstance(endpoint_plan, dict):
            method = str(endpoint_plan.get("method") or "").upper().strip()
            path = str(endpoint_plan.get("path") or "").strip()
            cases = endpoint_plan.get("cases") or []
        else:
            method = str(getattr(endpoint_plan, "method", "") or "").upper().strip()
            path = str(getattr(endpoint_plan, "path", "") or "").strip()
            cases = getattr(endpoint_plan, "cases", []) or []

        if not method or not path:
            continue

        has_interaction_assertion = False
        for case in cases:
            assertions = case.get("assertions") if isinstance(case, dict) else getattr(case, "assertions", [])
            for assertion in assertions or []:
                kind = str(assertion.get("kind") if isinstance(assertion, dict) else getattr(assertion, "kind", "") or "").strip()
                if kind in {"CALL_COUNT", "CALL_ARGS_PARTIAL", "STATE_CONTAINS", "DEPENDENCY_CALLED"}:
                    has_interaction_assertion = True
                    break
            if has_interaction_assertion:
                break
        index[_endpoint_key(method, path)] = has_interaction_assertion
    return index


def _extract_runtime_integrations(runtime_facts: Optional[dict]) -> List[str]:
    if not isinstance(runtime_facts, dict):
        return []
    integrations = runtime_facts.get("external_integrations") or runtime_facts.get("integrations") or []
    return sorted(str(item) for item in integrations if str(item).strip())


def _build_endpoint_coverage_entry(
    *,
    endpoint: Dict[str, Any],
    scenario_index: Dict[str, List[Dict[str, Any]]],
    interaction_index: Dict[str, bool],
    tests_patch: Dict[str, str],
) -> Dict[str, Any]:
    method = endpoint["method"]
    path = endpoint["path"]
    key = _endpoint_key(method, path)
    level = str(endpoint.get("level") or "")
    reason = str(endpoint.get("reason") or "").strip()
    scenarios = scenario_index.get(key, [])

    limitations = [reason] if reason else []
    if level and level not in {"HERMETIC_ENDPOINT_CONTRACT", "SEMANTIC_STATEFUL"}:
        limitations.append(f"coverage degraded to {level}")

    levels = {
        "startup": _status_for_rendered_file(
            file_path="tests/test_startup.py",
            patch=tests_patch or {},
            applicable=True,
            supported=True,
        ),
        "openapi": CoverageStatus.RENDERED.value,
        "hermetic": (
            CoverageStatus.RENDERED.value
            if endpoint.get("hermetic") or level == "HERMETIC_ENDPOINT_CONTRACT"
            else CoverageStatus.NOT_SUPPORTED.value
        ),
        "stateful": _stateful_level_status(scenarios),
    }

    return {
        "operation_id": endpoint["operation_id"],
        "method": method,
        "path": path,
        "levels": levels,
        "interaction_assertions": CoverageStatus.RENDERED.value if interaction_index.get(key, False) else CoverageStatus.NOT_APPLICABLE.value,
        "limitations": limitations,
    }


def _stateful_level_status(scenarios: List[Dict[str, Any]]) -> str:
    if not scenarios:
        return CoverageStatus.NOT_APPLICABLE.value
    if any(_scenario_status(scenario) == CoverageStatus.RENDERED.value for scenario in scenarios):
        return CoverageStatus.RENDERED.value
    if any(_scenario_status(scenario) == CoverageStatus.NOT_SUPPORTED.value for scenario in scenarios):
        return CoverageStatus.NOT_SUPPORTED.value
    return CoverageStatus.NOT_APPLICABLE.value


def _scenario_status(scenario: Dict[str, Any]) -> str:
    operations = scenario.get("operations")
    if not operations:
        return CoverageStatus.NOT_APPLICABLE.value
    try:
        confidence = float(scenario.get("confidence", 0.0))
    except Exception:
        return CoverageStatus.NOT_SUPPORTED.value
    if confidence >= 0.6:
        return CoverageStatus.RENDERED.value
    reason = str(scenario.get("reason") or "").strip()
    if reason:
        return CoverageStatus.NOT_SUPPORTED.value
    return CoverageStatus.NOT_APPLICABLE.value


def _status_for_rendered_file(
    *,
    file_path: str,
    patch: Dict[str, str],
    applicable: bool,
    supported: bool,
) -> str:
    content = patch.get(file_path)
    if isinstance(content, str) and _contains_real_tests(content):
        return CoverageStatus.RENDERED.value
    if not applicable:
        return CoverageStatus.NOT_APPLICABLE.value
    if not supported:
        return CoverageStatus.NOT_SUPPORTED.value
    return CoverageStatus.NOT_SUPPORTED.value


def _contains_real_tests(content: str) -> bool:
    stripped = str(content or "")
    if "assert True" in stripped and "def test_case_placeholder" in stripped:
        return False
    return any(
        line.lstrip().startswith("def test_") and "placeholder" not in line.lower()
        for line in stripped.splitlines()
    )


def _integration_validated(integration: str, endpoint_entries: List[Dict[str, Any]]) -> bool:
    token = integration.lower()
    for entry in endpoint_entries:
        reason_text = " ".join(entry.get("limitations") or []).lower()
        if token in reason_text:
            return True
        if any(str(status).lower() in {"rendered", "collected", "passed", "failed"} for status in (entry.get("levels") or {}).values()):
            return True
    return False


def _endpoint_key(method: str, path: str) -> str:
    return f"{str(method).upper().strip()} {str(path).strip()}"


def _status_icon(status: Any) -> str:
    value = str(status or "").strip().upper()
    if value == CoverageStatus.PLANNED.value:
        return "◌"
    if value in {CoverageStatus.RENDERED.value, CoverageStatus.COLLECTED.value}:
        return "~"
    if value == CoverageStatus.PASSED.value:
        return "✓"
    if value == CoverageStatus.FAILED.value:
        return "✗"
    return "—"
