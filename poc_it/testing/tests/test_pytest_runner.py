from poc_it.testing.execution.pytest_runner import _extract_collected_tests


def test_extract_collected_tests_from_pytest_summary_format() -> None:
    output = """
    ============================= test session starts =============================
    collected 11 items
    """

    assert _extract_collected_tests(output) == 11


def test_extract_collected_tests_from_collect_only_file_listing_format() -> None:
    output = """
    tests/test_startup.py: 3
    tests/test_openapi_contract.py: 2
    tests/test_request_validation.py: 6
    """

    assert _extract_collected_tests(output) == 11


def test_extract_collected_tests_prefers_summary_format_when_present() -> None:
    output = """
    collected 4 items
    tests/test_startup.py: 3
    tests/test_openapi_contract.py: 2
    """

    assert _extract_collected_tests(output) == 4


def test_extract_collected_tests_returns_zero_when_format_is_unknown() -> None:
    output = "no tests ran"

    assert _extract_collected_tests(output) == 0
