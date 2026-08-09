import pytest

from poc_it.orquestacion.contract_test_renderer import _render_assertion_lines


def test_render_assertion_lines_happy_path_uses_concrete_success_status():
    code = _render_assertion_lines(
        [
            {"kind": "STATUS_EQUALS", "expected": 201},
            {"kind": "JSON_HAS_KEYS", "expected": ["id", "name"]},
        ]
    )

    assert "assert resp.status_code == 201" in code
    assert "status_code < 500" not in code
    assert "status_code in" not in code
    assert "for _key in ['id', 'name']" in code


def test_render_assertion_lines_invalid_case_expects_contractual_status():
    code = _render_assertion_lines(
        [
            {"kind": "STATUS_EQUALS", "expected": 422},
        ]
    )

    assert "assert resp.status_code == 422" in code
    assert "assert resp.status_code == 200" not in code
    assert "assert resp.status_code == 201" not in code
    assert "status_code < 500" not in code


def test_render_assertion_lines_404_cannot_make_happy_path_pass():
    code = _render_assertion_lines(
        [
            {"kind": "STATUS_EQUALS", "expected": 201},
        ]
    )

    assert "assert resp.status_code == 201" in code
    assert "404" not in code


def test_render_assertion_lines_rejects_unknown_assertion():
    with pytest.raises(ValueError, match="Unsupported assertion kind"):
        _render_assertion_lines([{"kind": "NOT_SUPPORTED", "expected": True}])
