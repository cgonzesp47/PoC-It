from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from poc_it.testing.rendering.contract_test_renderer import render_tests_from_test_plan
from poc_it.testing.rendering.tests_harness import render_conftest_py
from poc_it.testing.reporting.coverage_report import (
    build_initial_coverage_report,
    render_test_coverage_markdown,
    to_pretty_json,
)


@dataclass(frozen=True)
class SuiteRenderResult:
    patch: Dict[str, str]
    validation_report: Dict[str, Any]


def render_suite_from_plan(
    *,
    structure_with_plan: Dict[str, str],
    runtime_contracts: Optional[dict],
    runtime_facts: Optional[dict],
    plan: Any,
    project_name: str,
) -> SuiteRenderResult:
    """
    Adaptador temporal al renderer existente.

    La responsabilidad de rendering vive ya en `poc_it.testing.rendering`,
    aunque durante la migración la implementación siga reutilizando el renderer actual.
    """
    tests_patch = render_tests_from_test_plan(
        structure=structure_with_plan,
        runtime_contracts=runtime_contracts if isinstance(runtime_contracts, dict) else None,
        runtime_facts=runtime_facts if isinstance(runtime_facts, dict) else None,
    )

    if "tests/conftest.py" not in tests_patch and any(
        getattr(e, "level", None) in ("HERMETIC_ENDPOINT_CONTRACT", "SEMANTIC_STATEFUL")
        for e in getattr(plan, "endpoints", [])
    ):
        try:
            tests_patch["tests/conftest.py"] = render_conftest_py(runtime_contracts if isinstance(runtime_contracts, dict) else {})
        except Exception:
            pass

    validation_report = _build_test_validation_report(
        nombre_proyecto=project_name,
        plan=plan,
        runtime_contracts=runtime_contracts if isinstance(runtime_contracts, dict) else None,
        runtime_facts=runtime_facts if isinstance(runtime_facts, dict) else None,
        tests_patch=tests_patch,
    )
    coverage_report = build_initial_coverage_report(
        nombre_proyecto=project_name,
        plan=plan,
        runtime_contracts=runtime_contracts if isinstance(runtime_contracts, dict) else None,
        runtime_facts=runtime_facts if isinstance(runtime_facts, dict) else None,
        tests_patch=tests_patch,
    )
    tests_patch[".poc_it/test_coverage_report.json"] = to_pretty_json(coverage_report)
    tests_patch["TEST_COVERAGE.md"] = render_test_coverage_markdown(coverage_report)
    return SuiteRenderResult(patch=tests_patch, validation_report=validation_report)


def _build_test_validation_report(
    *,
    nombre_proyecto: str,
    plan: Any,
    runtime_contracts: Optional[dict],
    runtime_facts: Optional[dict],
    tests_patch: Dict[str, str],
) -> Dict[str, Any]:
    endpoints = []
    try:
        for e in getattr(plan, "endpoints", []) or []:
            endpoints.append(
                {
                    "path": getattr(e, "path", None),
                    "method": getattr(e, "method", None),
                    "level": getattr(e, "level", None),
                    "reason": getattr(e, "reason", None),
                    "expected_status": getattr(e, "expected_status", None),
                    "allowed_statuses": getattr(e, "allowed_statuses", None),
                    "required_response_keys": getattr(e, "required_response_keys", None),
                    "response_media_type": getattr(e, "response_media_type", None),
                    "hermetic": getattr(e, "hermetic", None),
                }
            )
    except Exception:
        endpoints = []

    allow = []
    try:
        allow = list((runtime_contracts or {}).get("allowed_dependency_overrides") or [])
    except Exception:
        allow = []

    has_db_override = any(str(x).strip().endswith(".get_db") for x in allow)
    has_settings_override = any(str(x).strip().endswith(".get_settings") for x in allow) or any(
        str(x).strip().endswith(".get_config") for x in allow
    )

    totals = {}
    try:
        totals = getattr(plan, "totals", {}) or {}
    except Exception:
        totals = {}

    file_sources = {
        path: "contract-first"
        for path in sorted(
            list((tests_patch or {}).keys())
            + [".poc_it/test_plan.json", ".poc_it/test_validation_report.json", ".poc_it/test_coverage_report.json", "TEST_COVERAGE.md"]
        )
    }

    return {
        "project": nombre_proyecto,
        "strategy": "contract-first",
        "test_plan": {
            "mode": getattr(plan, "mode", None),
            "totals": totals,
            "endpoints": endpoints,
        },
        "overrides": {
            "allowed_dependency_overrides": allow,
            "has_get_db_override": bool(has_db_override),
            "has_get_settings_override": bool(has_settings_override),
        },
        "runtime": {
            "available": isinstance(runtime_facts, dict),
            "keys": sorted((runtime_facts or {}).keys()) if isinstance(runtime_facts, dict) else [],
        },
        "rendered_tests": {
            "files": sorted(list((tests_patch or {}).keys()) + [".poc_it/test_coverage_report.json", "TEST_COVERAGE.md"]),
            "has_conftest": bool("tests/conftest.py" in (tests_patch or {})),
            "strategy_by_file": file_sources,
        },
    }
