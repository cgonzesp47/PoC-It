from __future__ import annotations

from poc_it.materializacion.codegen.models import GeneratedFile
from poc_it.materializacion.codegen.structural_validation import (
    validate_generated_file_structure,
)
from poc_it.materializacion.generador_artefactos import _format_structural_issues


def _validate_single_generated_file_against_contract(*, file: dict, contract: dict) -> list[str]:
    issues = validate_generated_file_structure(
        generated_file=GeneratedFile(
            path=str(file.get("path") or ""),
            content=str(file.get("content") or ""),
        ),
        file_contract=contract,
    )
    return _format_structural_issues(issues)


def test_single_validation_rejects_wrong_path() -> None:
    c = {"path": "app/main.py", "kind": "main", "required_symbols": ["app"]}
    f = {"path": "app/other.py", "content": "x=1"}
    errs = _validate_single_generated_file_against_contract(file=f, contract=c)
    assert any("[CODEGEN_PATH_MISMATCH]" in e for e in errs)


def test_single_validation_rejects_empty_content_for_non_init() -> None:
    c = {"path": "app/main.py", "kind": "main", "required_symbols": ["app"]}
    f = {"path": "app/main.py", "content": "   "}
    errs = _validate_single_generated_file_against_contract(file=f, contract=c)
    assert any("[CODEGEN_EMPTY_CONTENT]" in e for e in errs)


def test_single_validation_allows_empty_package_init() -> None:
    c = {"path": "app/__init__.py", "kind": "package_init", "required_symbols": []}
    f = {"path": "app/__init__.py", "content": ""}
    errs = _validate_single_generated_file_against_contract(file=f, contract=c)
    assert errs == []


def test_single_validation_rejects_syntax_error_python() -> None:
    c = {
        "path": "app/core/config.py",
        "kind": "config",
        "required_symbols": ["Settings", "get_settings"],
    }
    f = {"path": "app/core/config.py", "content": "def x(:\n  pass\n"}
    errs = _validate_single_generated_file_against_contract(file=f, contract=c)
    assert any("[CODEGEN_SYNTAX_ERROR]" in e for e in errs)


def test_single_validation_endpoint_requires_router_assignment_and_defs() -> None:
    c = {
        "path": "app/api/endpoints/productos.py",
        "kind": "endpoint",
        "required_symbols": ["router", "create_producto"],
    }
    f = {
        "path": "app/api/endpoints/productos.py",
        "content": "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n# missing def create_producto\nx = 'create_producto('\n",
    }
    errs = _validate_single_generated_file_against_contract(file=f, contract=c)
    assert any("[CODEGEN_MISSING_REQUIRED_SYMBOL_DEF]" in e for e in errs)
