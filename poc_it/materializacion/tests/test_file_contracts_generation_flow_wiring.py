from __future__ import annotations

import poc_it.materializacion.generador_artefactos as ga


def test_generar_desde_spec_uses_codegen_orchestrator(monkeypatch) -> None:
    calls: dict[str, int] = {"orchestrator": 0}

    def fake_generate_files_from_contracts(**kwargs):
        calls["orchestrator"] += 1
        return [{"path": "app/__init__.py", "content": ""}]

    monkeypatch.setattr(
        ga,
        "_generate_files_from_contracts",
        fake_generate_files_from_contracts,
    )

    spec = {
        "schema_version": "pocit.spec.v1",
        "status": "valid",
        "files": ["app/__init__.py"],
        "restrictions": [],
        "endpoints": [],
        "dependencies": [],
        "dev_dependencies": [],
        "entrypoint": "app.main:app",
        "run_command": "uvicorn app.main:app --reload",
    }

    res = ga.generar_proyecto_desde_spec(
        spec=spec,
        descripcion_global="x",
        contexto_normalizado={},
        intentos=1,
    )

    assert calls["orchestrator"] == 1
    assert isinstance(res, dict)
    assert res["files"] == [{"path": "app/__init__.py", "content": ""}]
