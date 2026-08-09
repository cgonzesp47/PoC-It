from __future__ import annotations

from pathlib import Path

from poc_it.orquestacion import generacion_tests


def test_legacy_generator_modules_are_removed():
    assert not Path("poc_it/generador_tests_unitarios_hermetic.py").exists()
    assert not Path("poc_it/generador_tests_unitarios_spec.py").exists()


def test_generation_entrypoint_only_depends_on_new_service():
    source = Path("poc_it/orquestacion/generacion_tests.py").read_text(encoding="utf-8")

    assert "from poc_it.testing.test_generation_service import TestGenerationService" in source
    assert "generador_tests_unitarios_hermetic" not in source
    assert "generador_tests_unitarios_spec" not in source
    assert "fallback_to_legacy_minimal" not in source
    assert "_hermetic_suite_from_llm" not in source
    assert "test_endpoints_spec.py" not in source
    assert "test_endpoints_hermetic.py" not in source


def test_generation_entrypoint_exposes_historical_public_function():
    assert callable(generacion_tests.generar_tests_unitarios)
