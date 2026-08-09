from poc_it.orquestacion.contract_test_renderer import render_tests_from_test_plan
from poc_it.orquestacion.pytest_failure_classifier import classify_pytest_failure


def test_renderer_emits_test_startup_and_not_test_smoke_import():
    patch = render_tests_from_test_plan(
        structure={},
        runtime_contracts=None,
        runtime_facts=None,
    )

    assert "tests/test_startup.py" in patch
    assert "tests/test_smoke_import.py" not in patch
    startup = patch["tests/test_startup.py"]
    assert "def test_application_can_be_imported()" in startup
    assert "def test_application_lifespan_starts()" in startup
    assert "def test_openapi_document_can_be_generated()" in startup


def test_renderer_keeps_openapi_validation_independent_from_business_endpoints():
    patch = render_tests_from_test_plan(
        structure={},
        runtime_contracts=None,
        runtime_facts=None,
    )

    openapi_test = patch["tests/test_openapi_contract.py"]
    assert "client.get('/openapi.json')" in openapi_test
    assert "/openapi.json" in openapi_test


def test_classifier_detects_startup_import_failure():
    output = """
    __________________ test_application_can_be_imported __________________
    E   ModuleNotFoundError: No module named 'app.main'
    """

    failure = classify_pytest_failure(output)
    assert failure.kind in {"STARTUP_IMPORT_FAILURE", "TEST_INFRASTRUCTURE_FAILURE"}
    assert failure.missing_module == "app.main"


def test_classifier_detects_startup_lifespan_failure():
    output = """
    __________________ test_application_lifespan_starts __________________
    with TestClient(app) as client:
    RuntimeError: while handling lifespan startup event
    """

    failure = classify_pytest_failure(output)
    assert failure.kind in {"STARTUP_LIFESPAN_FAILURE", "TEST_INFRASTRUCTURE_FAILURE"}
