import pytest
from poc_it.materializacion.generador_artefactos import (
    _extract_single_generated_file,
    _generate_files_from_contracts,
    _parse_single_file_response,
)


def test_package_init_empty_content_is_accepted_direct_shape() -> None:
    data = {"path": "app/__init__.py", "content": ""}
    f = _extract_single_generated_file(
        data=data,
        expected_path="app/__init__.py",
        allow_empty_content=True,
    )
    assert f == {"path": "app/__init__.py", "content": ""}


def test_package_init_empty_content_is_accepted_files_shape() -> None:
    data = {"files": [{"path": "app/__init__.py", "content": ""}]}
    f = _extract_single_generated_file(
        data=data,
        expected_path="app/__init__.py",
        allow_empty_content=True,
    )
    assert f == {"path": "app/__init__.py", "content": ""}


def test_endpoint_empty_content_is_rejected_with_specific_error() -> None:
    raw = '{"path":"app/api/endpoints/productos.py","content":""}'
    f, errs = _parse_single_file_response(
        raw=raw,
        expected_path="app/api/endpoints/productos.py",
        allow_empty_content=False,
    )
    assert f is None
    assert errs == ["empty_content_not_allowed"]


def test_path_mismatch_is_rejected() -> None:
    raw = '{"path":"app/other.py","content":"x=1"}'
    f, errs = _parse_single_file_response(
        raw=raw,
        expected_path="app/expected.py",
        allow_empty_content=False,
    )
    assert f is None
    assert errs == ["single_file_extract_failed"]


def test_multiple_files_is_rejected() -> None:
    raw = '{"files":[{"path":"a.py","content":"1"},{"path":"b.py","content":"2"}]}'
    f, errs = _parse_single_file_response(
        raw=raw,
        expected_path="a.py",
        allow_empty_content=False,
    )
    assert f is None
    assert errs == ["multiple_files_returned"]


def test_package_init_is_generated_deterministically_without_llm() -> None:
    spec = {"files": ["app/__init__.py"], "endpoints": []}
    file_contracts = [{"path": "app/__init__.py", "kind": "package_init"}]

    files = _generate_files_from_contracts(
        spec=spec,
        file_contracts=file_contracts,
        intentos=2,
        descripcion_global="",
        contexto_normalizado=None,
    )
    assert files == [{"path": "app/__init__.py", "content": ""}]
