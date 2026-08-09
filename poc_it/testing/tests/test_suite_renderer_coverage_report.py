from __future__ import annotations

from types import SimpleNamespace

from poc_it.testing.rendering.suite_renderer import render_suite_from_plan
from poc_it.testing.reporting.coverage_report import (
    CoverageStatus,
    update_coverage_report_after_pytest,
)


def test_render_suite_from_plan_emits_level_coverage_artifacts():
    plan = SimpleNamespace(
        mode="CONTRACT_FIRST",
        totals={"endpoints": 2},
        endpoints=[
            SimpleNamespace(
                operation_id="createProduct",
                method="POST",
                path="/products",
                level="HERMETIC_ENDPOINT_CONTRACT",
                reason=None,
                allowed_statuses=[201, 422],
                required_response_keys=["id", "name"],
                hermetic=True,
            ),
            SimpleNamespace(
                operation_id="summarizeText",
                method="POST",
                path="/summarize",
                level="OPENAPI_ONLY",
                reason="semantic confidence insufficient",
                allowed_statuses=[],
                required_response_keys=["summary"],
                hermetic=False,
            ),
        ],
        endpoint_plans=[
            {
                "method": "POST",
                "path": "/products",
                "cases": [
                    {
                        "level": "HERMETIC_HTTP",
                        "assertions": [{"kind": "CALL_COUNT", "expected": 1}],
                    }
                ],
            }
        ],
        scenario_plans=[
            {
                "scenario_id": "product_lifecycle",
                "confidence": 0.9,
                "operations": [
                    {
                        "request": {
                            "method": "POST",
                            "path_template": "/products",
                        }
                    }
                ],
            },
            {
                "scenario_id": "weak_summary_flow",
                "confidence": 0.4,
                "reason": "semantic confidence insufficient",
                "operations": [
                    {
                        "request": {
                            "method": "POST",
                            "path_template": "/summarize",
                        }
                    }
                ],
            },
        ],
    )

    result = render_suite_from_plan(
        structure_with_plan={".poc_it/test_plan.json": "{}"},
        runtime_contracts={"allowed_dependency_overrides": ["app.dependencies.get_repo"]},
        runtime_facts={"external_integrations": ["google-drive", "openai"]},
        plan=plan,
        project_name="Pocket PoC",
    )

    patch = result.patch
    assert ".poc_it/test_coverage_report.json" in patch
    assert "TEST_COVERAGE.md" in patch

    coverage_json = patch[".poc_it/test_coverage_report.json"]
    coverage_md = patch["TEST_COVERAGE.md"]

    assert '"endpoints_detected": 2' in coverage_json
    assert f'"openapi": "{CoverageStatus.RENDERED.value}"' in coverage_json
    assert f'"hermetic": "{CoverageStatus.RENDERED.value}"' in coverage_json
    assert f'"stateful": "{CoverageStatus.RENDERED.value}"' in coverage_json
    assert f'"stateful": "{CoverageStatus.NOT_SUPPORTED.value}"' in coverage_json
    assert '"report_generated_without_test_execution": true' in coverage_json

    assert "### POST /products" in coverage_md
    assert "### POST /summarize" in coverage_md
    assert "~ Contrato OpenAPI" in coverage_md
    assert "~ Ejecución hermética" in coverage_md
    assert "~ Escenario stateful" in coverage_md
    assert "— Escenario stateful" in coverage_md
    assert "Este informe distingue cobertura planificada, renderizada y realmente validada tras pytest." in coverage_md


def test_render_suite_from_plan_distinguishes_not_applicable_from_not_supported():
    plan = SimpleNamespace(
        mode="CONTRACT_FIRST",
        totals={"endpoints": 1},
        endpoints=[
            SimpleNamespace(
                operation_id="healthCheck",
                method="GET",
                path="/health",
                level="HERMETIC_ENDPOINT_CONTRACT",
                reason=None,
                allowed_statuses=[],
                required_response_keys=[],
                hermetic=True,
            )
        ],
        endpoint_plans=[],
        scenario_plans=[],
    )

    result = render_suite_from_plan(
        structure_with_plan={".poc_it/test_plan.json": "{}"},
        runtime_contracts=None,
        runtime_facts=None,
        plan=plan,
        project_name="Pocket PoC",
    )

    coverage_json = result.patch[".poc_it/test_coverage_report.json"]
    coverage_md = result.patch["TEST_COVERAGE.md"]

    assert f'"startup": "{CoverageStatus.RENDERED.value}"' in coverage_json
    assert f'"stateful": "{CoverageStatus.NOT_APPLICABLE.value}"' in coverage_json
    assert "~ Startup" in coverage_md
    assert "— Escenario stateful" in coverage_md


def test_coverage_marks_startup_rendered_when_file_exists():
    plan = SimpleNamespace(
        mode="CONTRACT_FIRST",
        totals={"endpoints": 1},
        endpoints=[
            SimpleNamespace(
                operation_id="healthCheck",
                method="GET",
                path="/health",
                level="OPENAPI_ONLY",
                reason=None,
                allowed_statuses=[],
                required_response_keys=[],
                hermetic=False,
            )
        ],
        endpoint_plans=[],
        scenario_plans=[],
    )

    result = render_suite_from_plan(
        structure_with_plan={".poc_it/test_plan.json": "{}"},
        runtime_contracts=None,
        runtime_facts=None,
        plan=plan,
        project_name="Pocket PoC",
    )

    coverage_json = result.patch[".poc_it/test_coverage_report.json"]
    assert f'"startup": "{CoverageStatus.RENDERED.value}"' in coverage_json


def test_coverage_marks_stateful_not_applicable_without_scenarios():
    plan = SimpleNamespace(
        mode="CONTRACT_FIRST",
        totals={"endpoints": 1},
        endpoints=[
            SimpleNamespace(
                operation_id="listProducts",
                method="GET",
                path="/products",
                level="HERMETIC_ENDPOINT_CONTRACT",
                reason=None,
                allowed_statuses=[200],
                required_response_keys=["items"],
                hermetic=True,
            )
        ],
        endpoint_plans=[],
        scenario_plans=[],
    )

    result = render_suite_from_plan(
        structure_with_plan={".poc_it/test_plan.json": "{}"},
        runtime_contracts=None,
        runtime_facts=None,
        plan=plan,
        project_name="Pocket PoC",
    )

    coverage_json = result.patch[".poc_it/test_coverage_report.json"]
    assert f'"stateful": "{CoverageStatus.NOT_APPLICABLE.value}"' in coverage_json


def test_coverage_marks_http_not_supported_when_endpoint_cannot_be_executed():
    plan = SimpleNamespace(
        mode="CONTRACT_FIRST",
        totals={"endpoints": 1},
        endpoints=[
            SimpleNamespace(
                operation_id="summarizeText",
                method="POST",
                path="/summarize",
                level="OPENAPI_ONLY",
                reason="client not representable",
                allowed_statuses=[],
                required_response_keys=["summary"],
                hermetic=False,
            )
        ],
        endpoint_plans=[],
        scenario_plans=[],
    )

    result = render_suite_from_plan(
        structure_with_plan={".poc_it/test_plan.json": "{}"},
        runtime_contracts=None,
        runtime_facts=None,
        plan=plan,
        project_name="Pocket PoC",
    )

    coverage_json = result.patch[".poc_it/test_coverage_report.json"]
    assert f'"hermetic": "{CoverageStatus.NOT_SUPPORTED.value}"' in coverage_json


def test_update_coverage_report_after_pytest_promotes_levels_and_attaches_failed_cases():
    report = {
        "project": "Pocket PoC",
        "endpoints": [
            {
                "operation_id": "uploadFile",
                "method": "POST",
                "path": "/upload",
                "levels": {
                    "startup": CoverageStatus.RENDERED.value,
                    "openapi": CoverageStatus.RENDERED.value,
                    "hermetic": CoverageStatus.RENDERED.value,
                    "stateful": CoverageStatus.NOT_APPLICABLE.value,
                },
                "limitations": [],
            }
        ],
        "artifacts": {"report_generated_without_test_execution": True},
    }

    updated = update_coverage_report_after_pytest(
        report=report,
        rendered_manifest={
            "outcomes_by_operation": {
                "uploadFile": {
                    "startup": CoverageStatus.PASSED.value,
                    "openapi": CoverageStatus.PASSED.value,
                    "hermetic": CoverageStatus.FAILED.value,
                }
            },
            "failures_by_operation": {"uploadFile": ["test_upload_rejects_invalid_token"]},
        },
    )

    endpoint = updated["endpoints"][0]
    assert endpoint["levels"] == {
        "startup": CoverageStatus.PASSED.value,
        "openapi": CoverageStatus.PASSED.value,
        "hermetic": CoverageStatus.FAILED.value,
        "stateful": CoverageStatus.NOT_APPLICABLE.value,
    }
    assert endpoint["failed_cases"] == ["test_upload_rejects_invalid_token"]
    assert updated["artifacts"]["report_generated_without_test_execution"] is False


def test_update_coverage_report_after_pytest_marks_unexecuted_rendered_levels_as_collected():
    report = {
        "project": "Pocket PoC",
        "endpoints": [
            {
                "operation_id": "listProducts",
                "method": "GET",
                "path": "/products",
                "levels": {
                    "startup": CoverageStatus.RENDERED.value,
                    "openapi": CoverageStatus.RENDERED.value,
                    "hermetic": CoverageStatus.NOT_SUPPORTED.value,
                    "stateful": CoverageStatus.RENDERED.value,
                },
                "limitations": [],
            }
        ],
        "artifacts": {"report_generated_without_test_execution": True},
    }

    updated = update_coverage_report_after_pytest(report=report, rendered_manifest={})

    endpoint = updated["endpoints"][0]
    assert endpoint["levels"] == {
        "startup": CoverageStatus.COLLECTED.value,
        "openapi": CoverageStatus.COLLECTED.value,
        "hermetic": CoverageStatus.NOT_SUPPORTED.value,
        "stateful": CoverageStatus.COLLECTED.value,
    }
    assert updated["artifacts"]["report_generated_without_test_execution"] is False
