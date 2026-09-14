from __future__ import annotations

import pytest

from poc_it.orquestacion import generacion_documentacion as mod
from poc_it.modulos.models import PlantillaUsuario, ProjectContext


class _FakeRuntimeEnv:
    runtime_environment_status = "ready"
    python_executable = "python"
    detail = ""


def _base_context() -> ProjectContext:
    return ProjectContext(
        plantilla=PlantillaUsuario(
            nombre="demo",
            problema="problema",
            usuarios="usuarios",
            funcionalidades="funcionalidades",
            limites="limites",
            tecnologias="tecnologias",
        )
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch, *, generar_opciones_impl=None) -> dict:
    calls = {"generar_opciones": 0}

    def _generar_opciones(**kwargs):
        calls["generar_opciones"] += 1
        if generar_opciones_impl is not None:
            return generar_opciones_impl(**kwargs)
        return ["opcion"]

    monkeypatch.setattr(mod, "prepare_poc_runtime_environment", lambda *_a, **_k: _FakeRuntimeEnv())
    monkeypatch.setattr(mod, "runtime_verify_fastapi_project", lambda *_a, **_k: (True, ""))
    monkeypatch.setattr(mod, "generar_opciones", _generar_opciones)
    monkeypatch.setattr(mod, "generar_readme_final", lambda *_a, **_k: "final")
    monkeypatch.setattr(mod, "generar_readme_asesor", lambda *_a, **_k: "asesor")
    monkeypatch.setattr(mod, "generar_readme_manual", lambda *_a, **_k: "manual")
    monkeypatch.setattr(mod, "materializar_proyecto", lambda **_k: None)
    return calls


def _run(modo: str, monkeypatch: pytest.MonkeyPatch, **patch_kwargs) -> dict:
    calls = _patch_common(monkeypatch, **patch_kwargs)
    mod.generar_documentacion(
        nombre_proyecto="demo",
        descripcion_global="desc",
        tecnologias="fastapi",
        context=_base_context(),
        modo_generacion=modo,
        estructura={},
        resultado={},
        estimacion_generada=None,
        estimacion_manual=None,
        t_clasificacion_inicio=0.0,
        t_clasificacion_fin=0.0,
        t_generacion_inicio=0.0,
        t_generacion_fin=0.0,
    )
    return calls


def test_generar_opciones_not_called_in_parcial_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regresión: `opciones_estrategicas` solo lo consume el README de modo ASESOR, pero se
    calculaba SIEMPRE (incluso en PARCIAL/COMPLETO, donde el resultado se descartaba). Eso era
    una llamada LLM cara y desperdiciada que, si fallaba (p.ej. truncamiento), tumbaba todo el
    pipeline con 'Error no recuperable' después de ya haber completado la generación de código."""
    calls = _run("PARCIAL", monkeypatch)
    assert calls["generar_opciones"] == 0


def test_generar_opciones_failure_in_asesor_mode_does_not_crash_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En modo ASESOR sí se necesita, pero un fallo (truncamiento LLM, etc.) no debe abortar
    toda la generación de documentación: debe degradar sin la sección en vez de propagar."""

    def _boom(**_kwargs):
        raise RuntimeError("All providers failed.")

    calls = _run("ASESOR", monkeypatch, generar_opciones_impl=_boom)
    assert calls["generar_opciones"] == 1
