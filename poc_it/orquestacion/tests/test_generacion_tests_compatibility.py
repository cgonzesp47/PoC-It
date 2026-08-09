from __future__ import annotations

from poc_it.orquestacion import generacion_tests as compat_module
from poc_it.orquestacion import test_plan as legacy_test_plan
from poc_it.orquestacion.contract_test_renderer import render_tests_from_test_plan as legacy_render_tests_from_test_plan
from poc_it.testing.domain.models import AssertionSpec, FileSpec, RequestSpec
from poc_it.testing.rendering.contract_test_renderer import render_tests_from_test_plan


def test_legacy_test_plan_reexports_identical_model_classes():
    assert legacy_test_plan.RequestSpec is RequestSpec
    assert legacy_test_plan.FileSpec is FileSpec
    assert legacy_test_plan.AssertionSpec is AssertionSpec


def test_legacy_renderer_reexports_canonical_renderer():
    assert legacy_render_tests_from_test_plan is render_tests_from_test_plan


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
                    "structure_patch": {"tests/test_smoke_import.py": "x"},
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
