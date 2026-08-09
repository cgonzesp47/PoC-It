from __future__ import annotations

from copy import deepcopy

import pytest

from poc_it.generador.file_planner import (
    _safe_module_name,
    enrich_spec_files_for_implementation,
)


def _base_spec() -> dict:
    return {
        "files": ["app/main.py", "app/api/endpoints/upload.py"],
        "endpoints": [
            {
                "method": "POST",
                "path": "/upload",
                "file": "app/api/endpoints/upload.py",
                "integration_refs": ["google-drive"],
            }
        ],
        "integrations": [
            {"id": "google-drive", "required": True},
            {"id": "slack", "required": True},
            {"id": "optional-mail", "required": False},
        ],
    }


def test_safe_module_name_normalizes_hyphens():
    assert _safe_module_name("google-drive") == "google_drive"


def test_safe_module_name_rejects_invalid_values():
    with pytest.raises(ValueError):
        _safe_module_name("!!!")


def test_planner_generates_module_for_required_referenced_integration():
    spec = _base_spec()
    result = enrich_spec_files_for_implementation(spec, implementation_contracts=[])

    assert "app/integrations/__init__.py" in result.spec["files"]
    assert "app/integrations/google_drive.py" in result.spec["files"]
    assert "app/integrations/google_drive.py" in result.generated_paths
    assert {
        item["path"] for item in result.spec["implementation_files"]
    } == {"app/integrations/google_drive.py"}


def test_planner_does_not_generate_module_for_unreferenced_integration():
    spec = _base_spec()
    spec["endpoints"][0]["integration_refs"] = []
    result = enrich_spec_files_for_implementation(spec, implementation_contracts=[])

    assert "app/integrations/google_drive.py" not in result.spec["files"]
    assert "app/integrations/slack.py" not in result.spec["files"]


def test_planner_does_not_generate_module_for_optional_unreferenced_integration():
    spec = _base_spec()
    result = enrich_spec_files_for_implementation(spec, implementation_contracts=[])

    assert "app/integrations/optional_mail.py" not in result.spec["files"]


def test_planner_does_not_mutate_original_spec():
    spec = _base_spec()
    snapshot = deepcopy(spec)

    _ = enrich_spec_files_for_implementation(spec, implementation_contracts=[])

    assert spec == snapshot


def test_planner_is_deterministic():
    spec = _base_spec()

    first = enrich_spec_files_for_implementation(spec, implementation_contracts=[])
    second = enrich_spec_files_for_implementation(spec, implementation_contracts=[])

    assert first == second


def test_planner_does_not_generate_duplicates():
    spec = _base_spec()
    spec["files"].extend(
        ["app/integrations/__init__.py", "app/integrations/google_drive.py"]
    )
    spec["implementation_files"] = [
        {
            "path": "app/integrations/google_drive.py",
            "kind": "integration",
            "integration_ref": "google-drive",
            "generated_by": "existing",
        }
    ]

    result = enrich_spec_files_for_implementation(spec, implementation_contracts=[])

    assert result.generated_paths == []
    assert result.spec["files"].count("app/integrations/google_drive.py") == 1


def test_planner_respects_explicit_valid_module_path():
    spec = _base_spec()
    spec["integrations"][0]["module_path"] = "app/adapters/google_drive_client.py"

    result = enrich_spec_files_for_implementation(spec, implementation_contracts=[])

    assert "app/adapters/google_drive_client.py" in result.spec["files"]
    assert {
        item["path"] for item in result.spec["implementation_files"]
    } == {"app/adapters/google_drive_client.py"}


def test_planner_rejects_module_path_outside_app():
    spec = _base_spec()
    spec["integrations"][0]["module_path"] = "src/google_drive.py"

    with pytest.raises(ValueError):
        enrich_spec_files_for_implementation(spec, implementation_contracts=[])
