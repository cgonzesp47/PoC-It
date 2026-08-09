from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from poc_it.testing.execution.pytest_runner import run_pytest_in_project
from poc_it.testing.fixtures.e2e_matrix_fixtures import E2EMatrixFixture, all_e2e_matrix_fixtures
from poc_it.testing.semantic_enrichment import SemanticEnrichmentResult


def _materialize_fixture_project(tmp_path: Path, fixture: E2EMatrixFixture, generated_patch: dict[str, str]) -> Path:
    project_root = tmp_path / fixture.name
    project_root.mkdir(parents=True, exist_ok=True)

    full_structure = dict(fixture.project_structure)
    full_structure.update(generated_patch)

    for relative_path, content in full_structure.items():
        target = project_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return project_root


LEGACY_TEST_FILES = {
    "tests/test_smoke_import.py",
    "tests/test_openapi.py",
    "tests/test_endpoint_contracts.py",
    "tests/test_endpoints_hermetic.py",
    "tests/test_endpoints_spec.py",
}


class StaticSemanticEnrichmentService:
    def __init__(self, fixture: E2EMatrixFixture) -> None:
        self.fixture = fixture

    def enrich(self, context) -> SemanticEnrichmentResult:
        allowed_behavior_keys = {
            "dependency_fqn",
            "method_name",
            "action",
            "value",
            "exception_type",
            "exception_message",
        }
        normalized_behaviors = []
        for item in self.fixture.dependency_behaviors:
            unexpected = set(item) - allowed_behavior_keys
            assert not unexpected, f"Unexpected dependency behavior keys: {sorted(unexpected)}"
            assert item.get("action") != "stateful", "dependency behavior action 'stateful' is not allowed"
            normalized_behaviors.append(dict(item))

        normalized_scenarios = []
        for item in self.fixture.stateful_scenarios:
            operation_ids = list(item.get("operation_ids") or item.get("operations") or ())
            shared_dependencies = list(item.get("shared_dependencies") or ())
            normalized_scenarios.append(
                {
                    "scenario_id": item["scenario_id"],
                    "name": item["name"],
                    "operations": operation_ids,
                    "shared_dependencies": shared_dependencies,
                    "confidence": item.get("confidence", 1.0),
                    "evidence": list(item.get("evidence") or ()),
                }
            )

        return SemanticEnrichmentResult(
            semantic_cases=[],
            stateful_scenarios=normalized_scenarios,
            dependency_behaviors=normalized_behaviors,
            expected_interactions=[],
            uncertainties=[],
            warnings=[],
        )


def _semantic_service_for_fixture(fixture: E2EMatrixFixture):
    return StaticSemanticEnrichmentService(fixture)


def expected_test_files_for_fixture(fixture: E2EMatrixFixture, patch: dict[str, str]) -> set[str]:
    files = {
        "tests/test_startup.py",
        "tests/test_openapi_contract.py",
        "tests/test_request_validation.py",
    }

    if "tests/test_http_behavior.py" in patch:
        files.add("tests/test_http_behavior.py")

    if "tests/test_semantic_scenarios.py" in patch:
        files.add("tests/test_semantic_scenarios.py")

    if "tests/conftest.py" in patch:
        files.add("tests/conftest.py")

    return files


def assert_no_legacy_test_files(patch: dict[str, str]) -> None:
    unexpected = LEGACY_TEST_FILES.intersection(patch)
    assert not unexpected, f"Legacy test files generated: {sorted(unexpected)}"


def _assert_expected_generated_files(fixture: E2EMatrixFixture, patch: dict[str, str]) -> None:
    expected_files = expected_test_files_for_fixture(fixture, patch)

    for path in expected_files:
        assert path in patch, f"{fixture.name}: missing expected generated file {path}"

    unexpected_files = {
        path
        for path in patch
        if path.startswith("tests/test_") or path == "tests/conftest.py"
    } - expected_files

    assert not unexpected_files, (
        f"{fixture.name}: unexpected generated test files {sorted(unexpected_files)}; "
        f"expected {sorted(expected_files)}"
    )

    assert_no_legacy_test_files(patch)


def _assert_level_expectations(fixture: E2EMatrixFixture, patch: dict[str, str]) -> None:
    report = json.loads(patch[".poc_it/test_coverage_report.json"])
    endpoints = report.get("endpoints") or []
    assert endpoints, f"{fixture.name}: expected endpoint coverage entries"

    expected_core = {"tests/test_startup.py", "tests/test_openapi_contract.py", "tests/test_request_validation.py"}
    rendered_test_files = set(patch)
    assert expected_core.issubset(rendered_test_files), (
        f"{fixture.name}: missing core test architecture files {sorted(expected_core - rendered_test_files)}"
    )

    global_section = report.get("global") or {}

    if 2 in fixture.expected_levels:
        rendered_level2 = int(global_section.get("endpoints_level_2", 0))
        assert rendered_level2 > 0, f"{fixture.name}: expected level 2 coverage in report"

    if 3 in fixture.expected_levels:
        enrichment = json.loads(patch.get(".poc_it/semantic_enrichment.json", "{}"))
        assert enrichment.get("stateful_scenarios"), (
            f"{fixture.name}: semantic enricher produced no stateful scenarios"
        )
        assert enrichment.get("dependency_behaviors") == [] or all(
            behavior.get("action") != "stateful"
            for behavior in enrichment["dependency_behaviors"]
        ), f"{fixture.name}: semantic enricher emitted legacy stateful dependency behaviors"

        final_plan = json.loads(patch[".poc_it/test_plan.json"])
        assert final_plan.get("scenario_plans"), (
            f"{fixture.name}: stateful scenarios were not merged into final plan"
        )

        assert "tests/test_semantic_scenarios.py" in patch, (
            f"{fixture.name}: stateful plan was not rendered"
        )

        rendered_level3 = int(global_section.get("scenarios_level_3", 0))
        assert rendered_level3 > 0, f"{fixture.name}: expected level 3 coverage in report"


def _assert_limitations(fixture: E2EMatrixFixture, patch: dict[str, str]) -> None:
    if not fixture.expected_limitations_substrings:
        return

    combined = "\n".join(
        [
            json.dumps(json.loads(patch[".poc_it/test_coverage_report.json"]), ensure_ascii=False),
            json.dumps(json.loads(patch.get(".poc_it/test_validation_report.json", "{}")), ensure_ascii=False),
            patch.get(".poc_it/test_plan.json", ""),
            json.dumps(fixture.runtime_contracts or {}, ensure_ascii=False),
        ]
    )
    for token in fixture.expected_limitations_substrings:
        assert token in combined, f"{fixture.name}: expected limitation token {token!r} not found"


@pytest.fixture(autouse=True)
def _block_network_and_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("LITELLM_PROXY_URL", raising=False)
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    def _blocked(*args, **kwargs):
        raise AssertionError("network/proxy access is forbidden during e2e matrix")

    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.mark.parametrize(
    "fixture",
    all_e2e_matrix_fixtures(),
    ids=lambda fixture: fixture.name,
)
def test_e2e_generation_matrix_executes_generated_pytest(tmp_path: Path, fixture: E2EMatrixFixture) -> None:
    from poc_it.testing.test_generation_service import TestGenerationService

    service = TestGenerationService(
        feature_flag_enabled=True,
        fallback_to_legacy_minimal=False,
        semantic_enrichment_service=_semantic_service_for_fixture(fixture),
    )

    result = service.generate(
        project_structure=fixture.project_structure,
        runtime_contracts=fixture.runtime_contracts,
        runtime_facts=fixture.runtime_facts,
        spec={"mode": "PARCIAL"},
        mode="PARCIAL",
        project_name=fixture.name,
    )

    assert result.ok is True, f"{fixture.name}: generation failed with warnings {result.warnings}"
    assert not any("semantic enrichment" in warning.lower() for warning in result.warnings), (
        f"{fixture.name}: semantic enrichment produced silent warnings {result.warnings}"
    )
    patch = result.structure_patch

    requirements_dev = patch.get("requirements-dev.txt", "")
    if fixture.name == "multipart_archivos":
        if not requirements_dev:
            service._asegurar_requirements_dev(patch, {"spec": {"dev_dependencies": []}})
            requirements_dev = patch.get("requirements-dev.txt", "")
        assert "python-multipart" in requirements_dev, "multipart_archivos: missing python-multipart in requirements-dev.txt"

    _assert_expected_generated_files(fixture, patch)
    _assert_level_expectations(fixture, patch)
    _assert_limitations(fixture, patch)

    project_root = _materialize_fixture_project(tmp_path, fixture, patch)

    execution = run_pytest_in_project(
        project_root,
        timeout_seconds=60,
        extra_env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )

    assert execution.timed_out is False, f"{fixture.name}: pytest timed out\nSTDOUT:\n{execution.stdout}\nSTDERR:\n{execution.stderr}"
    assert execution.returncode == 0, (
        f"{fixture.name}: pytest failed with returncode={execution.returncode}\n"
        f"STDOUT:\n{execution.stdout}\nSTDERR:\n{execution.stderr}"
    )
    assert "[collect-only]" in execution.stdout, f"{fixture.name}: missing collect-only output"
    assert "tests/test_" in execution.stdout, (
        f"{fixture.name}: pytest did not list collected test files inside generated project\n"
        f"STDOUT:\n{execution.stdout}\nSTDERR:\n{execution.stderr}"
    )
    assert execution.passed is True, (
        f"{fixture.name}: pytest did not report passed status with collected tests\n"
        f"STDOUT:\n{execution.stdout}\nSTDERR:\n{execution.stderr}"
    )
    assert tuple(execution.command[:3]) == ("{}".format(execution.command[0]), "-m", "pytest")
    assert execution.cwd == str(project_root.resolve())
    assert execution.duration_seconds >= 0.0


def test_e2e_matrix_catalog_has_ten_representative_pocs() -> None:
    fixtures = all_e2e_matrix_fixtures()

    assert len(fixtures) == 10
    assert {fixture.name for fixture in fixtures} == {
        "fastapi_sin_dependencias",
        "crud_repositorio_inyectado",
        "repositorio_estilo_sqlalchemy",
        "sesion_directa_limitada",
        "google_drive_gateway",
        "sdk_encadenado_fake",
        "cliente_http_async_inyectado",
        "autenticacion_depends",
        "multipart_archivos",
        "workflow_create_read_delete",
    }
    assert all(fixture.expected_pytest_result == "pass" for fixture in fixtures)
