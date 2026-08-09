from __future__ import annotations

from poc_it.materializacion.generador_artefactos import _parse_single_file_response


def test_parse_single_file_accepts_path_content_shape() -> None:
    raw = '{"path":"app/main.py","content":"print(1)"}'
    file_obj, errs = _parse_single_file_response(raw=raw, expected_path="app/main.py")
    assert errs == []
    assert file_obj == {"path": "app/main.py", "content": "print(1)"}


def test_parse_single_file_accepts_files_array_with_single_item() -> None:
    raw = '{"files":[{"path":"app/main.py","content":"print(2)"}]}'
    file_obj, errs = _parse_single_file_response(raw=raw, expected_path="app/main.py")
    assert errs == []
    assert file_obj == {"path": "app/main.py", "content": "print(2)"}


def test_parse_single_file_rejects_multiple_files() -> None:
    raw = '{"files":[{"path":"a.py","content":"x"},{"path":"b.py","content":"y"}]}'
    file_obj, errs = _parse_single_file_response(raw=raw, expected_path="a.py")
    assert file_obj is None
    assert "multiple_files_returned" in errs


def test_parse_single_file_rejects_invalid_json() -> None:
    raw = '{"path":"app/main.py","content":"print(1)"'  # missing closing }
    file_obj, errs = _parse_single_file_response(raw=raw, expected_path="app/main.py")
    assert file_obj is None
    assert "json_parse_failed" in errs
