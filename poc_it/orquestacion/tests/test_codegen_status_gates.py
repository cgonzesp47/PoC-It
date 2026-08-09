import json
from dataclasses import replace
from pathlib import Path

import pytest

from poc_it.modulos.models import PlantillaUsuario, ProjectContext, ContextoNormalizado, ModoGeneracion
from poc_it.orquestacion.orquestador_parcial import OrquestadorParcial


def _minimal_context() -> ProjectContext:
    # ContextoNormalizado real del proyecto es pydantic; para estos tests basta con un objeto válido mínimo.
    # Si el modelo exige más campos, ajustaremos en el test al ejecutarlo.
    cn = ContextoNormalizado(
        objetivo_tecnico="x",
        actores_principales=[],
        funcionalidades_clave=[],
        integraciones_externas=[],
        restricciones_tecnicas=[],
        requisitos_no_funcionales=[],
        riesgos_inherentes=[],
        complejidad_inferida="BAJA",
        modo_recomendado="PARCIAL",
        contratos_api_explicitos=[],
        contratos_api_propuestos=[],
        contratos_api=[],
        persistence={"required": False},
        technology_signals=[],
        domain_entities=[],
        operation_groups=[],
        state_requirements={"durable": False, "entities": []},
        assumptions=[],
        evidence=[],
    )
    return ProjectContext(plantilla=None, contexto_normalizado=cn)


def _minimal_plantilla() -> PlantillaUsuario:
    return PlantillaUsuario(
        nombre="TmpProject",
        problema="Problema",
        usuarios="x",
        funcionalidades="x",
        limites="x",
        tecnologias="fastapi",
        integraciones=[],
        alcance="api",
        observaciones="",
    )


def test_codegen_failed_aborts_before_materialization(monkeypatch, tmp_path):
    # Arrange
    def fake_generar_proyecto_completo(*args, **kwargs):
        return {
            "files": [],
            "spec": {"schema_version": "pocit.spec.v1"},
            "codegen_status": "failed",
            "materializable": False,
            "codegen_errors": ["file_contract_generation_failed"],
        }

    monkeypatch.setattr(
        "poc_it.orquestacion.orquestador_parcial.generar_proyecto_completo", fake_generar_proyecto_completo
    )

    def fake_generar_proyecto_desde_spec(*args, **kwargs):
        return {
            "files": [],
            "spec": {"schema_version": "pocit.spec.v1", "files": ["app/__init__.py"]},
            "codegen_status": "failed",
            "materializable": False,
            "codegen_errors": ["file_contract_generation_failed"],
        }

    monkeypatch.setattr(
        "poc_it.orquestacion.orquestador_parcial.generar_proyecto_desde_spec", fake_generar_proyecto_desde_spec
    )

    plantilla = _minimal_plantilla()
    ctx = _minimal_context()
    ctx.plantilla = plantilla

    orch = OrquestadorParcial(plantilla=plantilla, modo_generacion="PARCIAL", context=ctx)

    # Act / Assert
    with pytest.raises(ValueError, match="Codegen failed before materialization"):
        orch._generar_y_materializar(context=ctx, modo_generacion="PARCIAL")


def test_codegen_invalid_materializes_but_skips_runtime_loop(monkeypatch):
    calls = []
    # Arrange: devuelve archivos candidatos con app/main.py, pero inválidos/no materializables
    def fake_generar_proyecto_completo(*args, **kwargs):
        return {
            "files": [
                {"path": "app/main.py", "content": "from fastapi import FastAPI\\napp = FastAPI()\\n"},
                {"path": "README.md", "content": "x"},
            ],
            "spec": {"schema_version": "pocit.spec.v1"},
            "codegen_status": "invalid",
            "materializable": False,
            "codegen_errors": ["final_validation_failed"],
            "validation_report": {"errors": ["X"]},
        }

    monkeypatch.setattr(
        "poc_it.orquestacion.orquestador_parcial.generar_proyecto_completo", fake_generar_proyecto_completo
    )

    # materializar_proyecto no debe tocar disco en unit test: stub
    def fake_materializar_proyecto(*, nombre_proyecto, estructura, limpiar_directorio=True, **kwargs):
        calls.append(dict(estructura))
        return list(estructura.keys())

    monkeypatch.setattr("poc_it.orquestacion.orquestador_parcial.materializar_proyecto", fake_materializar_proyecto)

    # runtime loop no debe ejecutarse
    def boom_runtime(*args, **kwargs):
        raise AssertionError("ejecutar_reparacion_runtime should NOT be called")

    monkeypatch.setattr("poc_it.orquestacion.orquestador_parcial.ejecutar_reparacion_runtime", boom_runtime)

    # docs stub (para que ejecutar() no falle por IO)
    monkeypatch.setattr("poc_it.orquestacion.orquestador_parcial.generar_documentacion", lambda **kwargs: None)
    monkeypatch.setattr(
        "poc_it.orquestacion.orquestador_parcial.parchear_bloque_estimacion", lambda **kwargs: None, raising=False
    )

    plantilla = _minimal_plantilla()
    ctx = _minimal_context()
    ctx.plantilla = plantilla

    orch = OrquestadorParcial(plantilla=plantilla, modo_generacion="PARCIAL", context=ctx)

    out = orch.ejecutar()
    assert out["nombre_proyecto"] == plantilla.nombre
    assert out.get("pytest_ok") in (False, None)
    assert out.get("publishable") in (False, None)
    assert calls
    first_materialization = calls[0]
    assert "app/main.py" in first_materialization
    assert "CODEGEN_VALIDATION_ERROR.json" not in first_materialization


def test_codegen_valid_runs_runtime_loop(monkeypatch):
    called = {"runtime": 0}

    def fake_generar_proyecto_completo(*args, **kwargs):
        return {
            "files": [
                {"path": "app/main.py", "content": "from fastapi import FastAPI\\napp = FastAPI()\\n"},
                {"path": "README.md", "content": "x"},
            ],
            "spec": {"schema_version": "pocit.spec.v1"},
            "codegen_status": "valid",
            "materializable": True,
            "codegen_errors": [],
            "validation_report": {"errors": []},
        }

    monkeypatch.setattr(
        "poc_it.orquestacion.orquestador_parcial.generar_proyecto_completo", fake_generar_proyecto_completo
    )

    def fake_materializar_proyecto(*, nombre_proyecto, estructura, limpiar_directorio=True, **kwargs):
        return list(estructura.keys())

    monkeypatch.setattr("poc_it.orquestacion.orquestador_parcial.materializar_proyecto", fake_materializar_proyecto)

    def fake_runtime(*args, **kwargs):
        called["runtime"] += 1
        # set pytest_repair artifact to avoid downstream reading junit
        res = kwargs.get("resultado")
        if isinstance(res, dict):
            res["pytest_repair"] = {"ok": True, "attempts": 1}

    monkeypatch.setattr("poc_it.orquestacion.orquestador_parcial.ejecutar_reparacion_runtime", fake_runtime)

    # facts extractor stub (para no analizar AST real)
    monkeypatch.setattr(
        "poc_it.orquestacion.orquestador_parcial.extract_poc_facts_from_structure", lambda estructura: type("X", (), {"to_dict": lambda self: {"endpoints": []}})()
    )

    # docs stub
    monkeypatch.setattr("poc_it.orquestacion.orquestador_parcial.generar_documentacion", lambda **kwargs: None)
    monkeypatch.setattr(
        "poc_it.orquestacion.orquestador_parcial.parchear_bloque_estimacion", lambda **kwargs: None, raising=False
    )

    plantilla = _minimal_plantilla()
    ctx = _minimal_context()
    ctx.plantilla = plantilla
    orch = OrquestadorParcial(plantilla=plantilla, modo_generacion="PARCIAL", context=ctx)

    orch.ejecutar()
    assert called["runtime"] == 1
