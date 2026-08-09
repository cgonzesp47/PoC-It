from __future__ import annotations

from poc_it.orquestacion.override_repair_llm import suggest_override_repairs
from poc_it.orquestacion.stub_gen_llm import suggest_dependency_behaviors
from poc_it.orquestacion.tests_coverage_llm_repair import repair_tests_for_coverage
from poc_it.orquestacion.tests_sanitizer import sanitize_generated_tests


def test_sanitizer_is_now_identity_and_does_not_rewrite_files():
    files = {"tests/test_api.py": "def test_ok():\n    assert True\n"}
    result = sanitize_generated_tests(files)

    assert result == files
    assert result is not files


def test_coverage_repair_returns_structured_regeneration_signal():
    result = repair_tests_for_coverage(
        plan={"endpoints": []},
        coverage_report={"global": {"endpoints_detected": 1}},
        files={"TEST_COVERAGE.md": "# report"},
    )

    assert result["action"] == "regenerate_from_plan_if_needed"
    assert result["modified_files"] == []
    assert result["files"] == {"TEST_COVERAGE.md": "# report"}


def test_stub_generation_returns_structured_behaviors_not_python_code():
    result = suggest_dependency_behaviors(
        dependency_context={
            "dependencies": [
                {
                    "fqn": "app.dependencies.get_ai_client",
                    "observed_calls": [{"method_name": "summarize", "awaited": True}],
                }
            ]
        }
    )

    assert result == {
        "behaviors": [
            {
                "dependency": "app.dependencies.get_ai_client",
                "suggested_calls": [{"method_name": "summarize", "awaited": True}],
            }
        ]
    }


def test_override_repair_returns_suggestions_without_editing_conftest():
    result = suggest_override_repairs(
        plan={"endpoints": []},
        runtime_contracts={"allowed_dependency_overrides": ["app.dependencies.get_repo"]},
        failure_context={"missing_overrides": ["app.dependencies.get_ai_client", "app.dependencies.get_repo"]},
    )

    assert result["action"] == "UPDATE_DEPENDENCY_BINDINGS"
    assert result["edit_files_directly"] is False
    assert result["target"] == "runtime_contracts.allowed_dependency_overrides"
    assert result["suggested_overrides"] == ["app.dependencies.get_ai_client"]
