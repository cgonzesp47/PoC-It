import json

from poc_it.orquestacion import generacion_tests as compat_module
from poc_it.testing.test_generation_service import TestGenerationService


def test_service_generates_contract_first_patch_with_traceability():
    service = TestGenerationService(feature_flag_enabled=True, fallback_to_legacy_minimal=False)

    runtime_contracts = {
        "allowed_dependency_overrides": [],
        "endpoints": [],
        "openapi": {"openapi": "3.1.0", "paths": {"/items": {"get": {"responses": {"200": {"description": "ok"}}}}}},
    }
    runtime_facts = {"app_module": "app.main"}

    result = service.generate(
        project_structure={"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
        runtime_contracts=runtime_contracts,
        runtime_facts=runtime_facts,
        spec={"mode": "PARCIAL"},
        mode="PARCIAL",
        project_name="Demo",
    )

    assert result.ok is True
    assert result.strategy == "contract-first"
    assert ".poc_it/test_plan.json" in result.structure_patch
    assert ".poc_it/test_validation_report.json" in result.structure_patch
    report = json.loads(result.structure_patch[".poc_it/test_validation_report.json"])
    assert report["strategy"] == "contract-first"
    assert report["rendered_tests"]["strategy_by_file"]["tests/test_startup.py"] == "contract-first"
    assert result.strategy_by_file()["tests/test_startup.py"] == "contract-first"


def test_service_can_be_disabled_by_feature_flag():
    service = TestGenerationService(feature_flag_enabled=False, fallback_to_legacy_minimal=True)

    result = service.generate(
        project_structure={"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
        runtime_contracts=None,
        runtime_facts=None,
        spec={"mode": "PARCIAL"},
        mode="PARCIAL",
        project_name="Demo",
    )

    assert result.ok is True
    assert result.strategy == "minimal-fallback"
    assert "tests/test_startup.py" in result.structure_patch
    assert result.strategy_by_file()["tests/test_startup.py"] == "minimal-fallback"


def test_compat_module_delegates_to_test_generation_service(monkeypatch):
    calls = {}

    class FakeService:
        def __init__(self, *args, **kwargs):
            calls["init"] = kwargs

        def generate(self, **kwargs):
            calls["generate"] = kwargs
            return type(
                "FakeResult",
                (),
                {
                    "warnings": [],
                    "structure_patch": {"tests/test_startup.py": "x"},
                    "validation_report": None,
                    "test_plan": None,
                },
            )()

        def materialize_result(self, **kwargs):
            calls["materialize"] = kwargs
            return True

    monkeypatch.setattr("poc_it.orquestacion.generacion_tests.TestGenerationService", FakeService)

    ok = compat_module.generar_tests_unitarios(
        nombre_proyecto="Demo",
        resultado={"spec": {"mode": "PARCIAL"}},
        estructura={"app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
        archivos_creados=[],
        modo_generacion="PARCIAL",
    )

    assert ok is True
    assert calls["generate"]["project_name"] == "Demo"
    assert calls["generate"]["mode"] == "PARCIAL"
    assert "materialize" in calls
