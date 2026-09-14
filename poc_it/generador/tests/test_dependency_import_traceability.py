from __future__ import annotations

from poc_it.generador.file_contracts import build_file_contracts_from_spec, file_contracts_to_dict
from poc_it.generador.file_contracts_validation import (
    validate_dependency_imports_declared,
    validate_file_contracts,
)
from poc_it.generador.request_ir import build_request_ir_from_context
from poc_it.generador.spec_builder import build_spec_from_request_ir


def _minimal_spec(**overrides: object) -> dict:
    base = {
        "schema_version": "pocit.spec.v1",
        "status": "draft",
        "files": ["app/__init__.py", "app/main.py"],
        "dependencies": [],
        "dev_dependencies": [],
        "technology_signals": [],
    }
    base.update(overrides)
    return base


def _contract(path: str, allowed_imports: list[str]) -> dict:
    return {"path": path, "kind": "unknown", "allowed_imports": allowed_imports}


# ---------------------------------------------------------------------------
# 1) import_root cubierto por TechnologySignal con nombre distinto al package
#    -> requirements.txt incluye el package (caso real: pydantic_settings).
# ---------------------------------------------------------------------------


def test_baseline_pydantic_settings_signal_covers_config_import_and_declares_package() -> None:
    ir = build_request_ir_from_context({}, descripcion_global="x")
    spec = build_spec_from_request_ir(ir)

    assert "pydantic-settings" in spec["dependencies"]
    signal = next(
        item for item in spec["technology_signals"] if item["id"] == "pydantic-settings"
    )
    assert list(signal["import_roots"]) == ["pydantic_settings"]
    assert list(signal["packages"]) == ["pydantic-settings"]

    file_contracts = build_file_contracts_from_spec(spec)
    config_contract = next(c for c in file_contracts if c.path == "app/core/config.py")
    assert "pydantic_settings" in config_contract.allowed_imports

    errors = validate_file_contracts(
        spec=spec,
        implementation_contracts=[],
        file_contracts=file_contracts_to_dict(file_contracts),
    )
    dependency_errors = [e for e in errors if e.code == "DEPENDENCY_IMPORT_NOT_DECLARED"]
    assert dependency_errors == []


# ---------------------------------------------------------------------------
# 2) import externo requerido/permitido sin TechnologySignal -> error estructurado.
# ---------------------------------------------------------------------------


def test_external_import_without_technology_signal_is_flagged() -> None:
    spec = _minimal_spec(technology_signals=[])
    file_contracts = [_contract("app/integrations/drive.py", ["googleapiclient.discovery"])]

    errors = validate_dependency_imports_declared(spec=spec, file_contracts=file_contracts)

    assert len(errors) == 1
    error = errors[0]
    assert error.code == "DEPENDENCY_IMPORT_NOT_DECLARED"
    assert error.path == "app/integrations/drive.py"
    assert error.details["import_root"] == "googleapiclient"
    assert error.details["file"] == "app/integrations/drive.py"
    assert error.details["reason"] == "No declared installable package provides this import"


def test_technology_signal_covers_import_but_package_missing_from_dependencies_is_flagged() -> None:
    spec = _minimal_spec(
        dependencies=[],
        technology_signals=[
            {
                "id": "google-drive",
                "name": "google-drive",
                "packages": ["google-api-python-client"],
                "import_roots": ["googleapiclient"],
                "confidence": "explicit",
            }
        ],
    )
    file_contracts = [_contract("app/integrations/drive.py", ["googleapiclient.discovery"])]

    errors = validate_dependency_imports_declared(spec=spec, file_contracts=file_contracts)

    assert len(errors) == 1
    assert errors[0].code == "DEPENDENCY_IMPORT_NOT_DECLARED"
    assert errors[0].details["reason"] == "TechnologySignal packages are not present in declared dependencies"


# ---------------------------------------------------------------------------
# 3) stdlib no requiere TechnologySignal/package.
# ---------------------------------------------------------------------------


def test_stdlib_import_does_not_require_technology_signal() -> None:
    spec = _minimal_spec(technology_signals=[], dependencies=[])
    file_contracts = [_contract("app/core/config.py", ["functools", "pathlib", "typing", "os"])]

    errors = validate_dependency_imports_declared(spec=spec, file_contracts=file_contracts)

    assert errors == []


# ---------------------------------------------------------------------------
# 4) import interno del proyecto no requiere package externo.
# ---------------------------------------------------------------------------


def test_internal_project_import_does_not_require_package() -> None:
    spec = _minimal_spec(
        files=["app/__init__.py", "app/main.py", "app/core/config.py"],
        technology_signals=[],
        dependencies=[],
    )
    file_contracts = [_contract("app/api/router.py", ["app.core.config"])]

    errors = validate_dependency_imports_declared(spec=spec, file_contracts=file_contracts)

    assert errors == []


# ---------------------------------------------------------------------------
# 5) import_root y package con nombres distintos funcionan correctamente.
# ---------------------------------------------------------------------------


def test_import_root_and_package_with_different_names_are_traced_correctly() -> None:
    spec = _minimal_spec(
        dependencies=["google-api-python-client"],
        technology_signals=[
            {
                "id": "google-drive",
                "name": "google-drive",
                "packages": ["google-api-python-client"],
                "import_roots": ["googleapiclient"],
                "confidence": "explicit",
            }
        ],
    )
    file_contracts = [_contract("app/integrations/drive.py", ["googleapiclient.discovery"])]

    errors = validate_dependency_imports_declared(spec=spec, file_contracts=file_contracts)

    assert errors == []


def test_package_never_inferred_from_signal_id_name_or_import_root() -> None:
    """Aunque id/name/import_root del TechnologySignal coincidan con un nombre plausible de
    package, si `packages` no lo declara explícitamente, el import NO se considera cubierto."""
    spec = _minimal_spec(
        dependencies=["some-other-totally-unrelated-package"],
        technology_signals=[
            {
                "id": "googleapiclient",
                "name": "googleapiclient",
                "packages": [],  # deliberadamente vacío
                "import_roots": ["googleapiclient"],
                "confidence": "explicit",
            }
        ],
    )
    file_contracts = [_contract("app/integrations/drive.py", ["googleapiclient.discovery"])]

    errors = validate_dependency_imports_declared(spec=spec, file_contracts=file_contracts)

    assert len(errors) == 1
    assert errors[0].code == "DEPENDENCY_IMPORT_NOT_DECLARED"
