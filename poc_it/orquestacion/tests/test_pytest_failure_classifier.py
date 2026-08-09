from __future__ import annotations

from poc_it.orquestacion.pytest_failure_classifier import classify_pytest_failure


def test_classifies_startup_import_failure_with_structured_metadata():
    failure = classify_pytest_failure(
        """
        __________________ tests/test_startup.py __________________
        E   ModuleNotFoundError: No module named 'app.main'
        FAILED tests/test_startup.py::test_application_can_be_imported
        """
    )

    assert failure.category == "STARTUP_IMPORT_FAILURE"
    assert failure.archivo == "tests/test_startup.py"
    assert failure.test == "test_application_can_be_imported"
    assert failure.nivel == "LEVEL_0_STARTUP"
    assert failure.accion_permitida == "repair_generated_import_or_module_reference"
    assert failure.reparable_automaticamente is True
    assert failure.missing_module == "app.main"


def test_classifies_request_data_failure_as_repairable_data_problem():
    failure = classify_pytest_failure(
        """
        __________________ tests/test_http_behavior.py __________________
        FAILED tests/test_http_behavior.py::test_contract_post_products - assert 422 == 201
        E   422 Unprocessable Entity
        """
    )

    assert failure.category == "REQUEST_DATA_FAILURE"
    assert failure.nivel == "LEVEL_2_HTTP"
    assert failure.accion_permitida == "repair_request_data_only"
    assert failure.reparable_automaticamente is True


def test_classifies_dependency_double_failure_without_blaming_application():
    failure = classify_pytest_failure(
        """
        __________________ tests/test_semantic_scenarios.py __________________
        FAILED tests/test_semantic_scenarios.py::test_scenario_create_product
        E   StrictDoubleError: Unexpected method 'delete' for dependency app.dependencies.repo
        """
    )

    assert failure.category == "DEPENDENCY_DOUBLE_FAILURE"
    assert failure.test == "test_scenario_create_product"
    assert failure.nivel == "LEVEL_3_STATEFUL"
    assert failure.accion_permitida == "repair_dependency_double_or_harness_cleanup"
    assert failure.reparable_automaticamente is True


def test_classifies_application_behavior_failures_as_non_relaxable_code_repairs():
    failure = classify_pytest_failure(
        """
        __________________ tests/test_http_behavior.py __________________
        FAILED tests/test_http_behavior.py::test_contract_get_products
        E   AssertionError: assert response.status_code == 201
        E   assert 500 == 201
        """
    )

    assert failure.category == "APPLICATION_BEHAVIOR_FAILURE"
    assert failure.accion_permitida == "route_to_code_repair"
    assert failure.reparable_automaticamente is False
    assert "status_code < 500" not in failure.accion_permitida


def test_classifies_harness_failures_for_harness_regeneration():
    failure = classify_pytest_failure(
        """
        __________________ tests/conftest.py __________________
        E   Fixture "client" called directly. Fixtures are not meant to be called directly
        FAILED tests/test_http_behavior.py::test_contract_get_products
        """
    )

    assert failure.category == "TEST_INFRASTRUCTURE_FAILURE"
    assert failure.called_fixture == "client"
    assert failure.accion_permitida == "regenerate_harness_or_fix_fixture_reference"
    assert failure.reparable_automaticamente is True


def test_classifies_external_io_leaks_as_override_problems():
    failure = classify_pytest_failure(
        """
        __________________ tests/test_http_behavior.py __________________
        FAILED tests/test_http_behavior.py::test_contract_post_products
        requests.exceptions.ConnectionError: HTTPSConnectionPool(host='api.example.com', port=443)
        """
    )

    assert failure.category == "EXTERNAL_IO_LEAK"
    assert failure.accion_permitida == "regenerate_harness_or_override_dependency"
    assert failure.reparable_automaticamente is True


def test_classifies_plan_and_rendering_failures_separately():
    plan_failure = classify_pytest_failure(
        """
        __________________ tests/test_semantic_scenarios.py __________________
        E   SpecValidationError: invalid plan for scenario_plans
        """
    )
    rendering_failure = classify_pytest_failure(
        """
        __________________ tests/test_semantic_scenarios.py __________________
        E   SyntaxError: f-string: expecting '}'
        """
    )

    assert plan_failure.category == "PLAN_VALIDATION_FAILURE"
    assert plan_failure.accion_permitida == "repair_plan_only"
    assert rendering_failure.category == "RENDERING_FAILURE"
    assert rendering_failure.accion_permitida == "repair_renderer_output"
