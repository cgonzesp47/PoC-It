from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from poc_it.generador.file_contracts import build_file_contracts_from_spec
from poc_it.generador.request_ir import build_request_ir_from_context
from poc_it.generador.spec_builder import build_spec_from_request_ir
from poc_it.generador.spec_validation import repair_spec_deterministic, validate_spec
from poc_it.materializacion.generador_artefactos import generar_proyecto_completo


def _fixture_context_crud_productos() -> dict:
    return {
        "objetivo_tecnico": "API CRUD básica de productos en FastAPI",
        "funcionalidades_clave": ["crud productos"],
        "integraciones_externas": [],
        "restricciones_tecnicas": [
            "Sin autenticación",
            "Configurar conexión vía variables de entorno",
        ],
        "contratos_api": [],
        "contratos_api_explicitos": [],
        "contratos_api_propuestos": [
            {
                "method": "POST",
                "path": "/productos",
                "request": {
                    "type": "json",
                    "schema_hint": {
                        "nombre": "string",
                        "descripcion": "string",
                        "precio": "number",
                        "disponible": "boolean",
                    },
                    "assumption": "fixture",
                },
                "response": {"json_example": {"id": "number"}},
            },
            {
                "method": "GET",
                "path": "/productos",
                "request": {"type": "none", "assumption": "fixture"},
                "response": {"json_example": [{"id": "number"}]},
            },
            {
                "method": "GET",
                "path": "/productos/{id}",
                "request": {"type": "none", "assumption": "fixture"},
                "response": {"json_example": {"id": "number"}},
            },
            {
                "method": "PUT",
                "path": "/productos/{id}",
                "request": {
                    "type": "json",
                    "schema_hint": {
                        "nombre": "string",
                        "descripcion": "string",
                        "precio": "number",
                        "disponible": "boolean",
                    },
                    "assumption": "fixture",
                },
                "response": {"json_example": {"id": "number"}},
            },
            {
                "method": "DELETE",
                "path": "/productos/{id}",
                "request": {"type": "none", "assumption": "fixture"},
                "response": {"json_example": {"ok": True}},
            },
        ],
        "capability_coverage": [
            {
                "capability": "crud productos",
                "contract_refs": [
                    "POST /productos",
                    "GET /productos",
                    "GET /productos/{id}",
                    "PUT /productos/{id}",
                    "DELETE /productos/{id}",
                ],
                "status": "covered",
                "source": "inferred",
                "assumption": "fixture",
            }
        ],
        "persistence": {"required": False},
        "modo_recomendado": "PARCIAL",
        "framework_objetivo": "fastapi",
    }


def _load_fixture_spec_ok() -> dict:
    req_ir = build_request_ir_from_context(_fixture_context_crud_productos(), descripcion_global="x")
    return build_spec_from_request_ir(req_ir)


def _minimal_context() -> Dict[str, Any]:
    return {
        "framework_objetivo": "fastapi",
        "funcionalidades_clave": ["crud productos"],
        "integraciones_externas": [],
        "contratos_api": [],
        "contratos_api_propuestos": [
            {
                "method": "GET",
                "path": "/productos",
                "request": {"type": "none", "assumption": "fixture"},
                "response": {"json_example": [{"id": "number"}]},
            }
        ],
        "capability_coverage": [
            {
                "capability": "crud productos",
                "contract_refs": ["GET /productos"],
                "status": "covered",
                "source": "inferred",
                "assumption": "fixture",
            }
        ],
        "persistence": {"required": False},
    }


def test_validate_spec_crud_can_be_marked_valid():
    spec = _load_fixture_spec_ok()
    errs = validate_spec(spec)
    assert not any(e.severity == "fatal" for e in errs)

    _ = build_file_contracts_from_spec(spec)


def test_repair_adds_path_params_and_removes_fatal():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)

    id_idx = next((i for i, ep in enumerate(spec2["endpoints"]) if "{id}" in (ep.get("path") or "")), None)
    if id_idx is None:
        pytest.skip("El spec fixture no contiene endpoint con {id}")

    spec2["endpoints"][id_idx]["request"].pop("path_params", None)
    errs0 = validate_spec(spec2)
    assert any(e.severity == "fatal" and e.code == "REQUEST_PATH_PARAMS_MISSING" for e in errs0)

    repaired, rep_changes = repair_spec_deterministic(spec2)
    assert any(e.code == "REPAIR_ADD_PATH_PARAMS" for e in rep_changes)

    errs1 = validate_spec(repaired)
    assert not any(e.severity == "fatal" for e in errs1)


def test_id_endpoint_query_request_stays_fatal_after_repair():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)

    id_idx = next((i for i, ep in enumerate(spec2["endpoints"]) if "{id}" in (ep.get("path") or "")), None)
    if id_idx is None:
        pytest.skip("El spec fixture no contiene endpoint con {id}")

    spec2["endpoints"][id_idx]["request"]["type"] = "query"
    repaired, _ = repair_spec_deterministic(spec2)
    errs = validate_spec(repaired)
    assert any(e.severity == "fatal" and e.code == "REQUEST_ID_AS_QUERY" for e in errs)


def test_persistence_required_true_without_evidence_is_fatal():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)

    spec2["persistence"]["required"] = True
    spec2["persistence"]["kind"] = spec2["persistence"].get("kind") or "unknown"
    spec2["persistence"]["durable_state"] = True
    spec2["persistence"]["evidence"] = []
    spec2["test_strategy"]["requires_dependency_overrides"] = True

    errs = validate_spec(spec2)
    assert any(e.severity == "fatal" and e.code == "PERSISTENCE_EVIDENCE_MISSING" for e in errs)


def test_generation_gate_returns_spec_errors_and_no_files_when_spec_fatal(monkeypatch):
    from poc_it.generador import spec_builder as sb

    original = sb.build_spec_from_request_ir

    def _bad_builder(*args, **kwargs):
        s = _load_fixture_spec_ok()
        s["source"] = "not-a-dict"
        return s

    monkeypatch.setattr(sb, "build_spec_from_request_ir", _bad_builder)

    out = generar_proyecto_completo(
        descripcion_global="x",
        contexto_normalizado=_minimal_context(),
        intentos=1,
    )

    assert out.get("files") == []
    assert isinstance(out.get("spec"), dict)
    assert out["spec"].get("status") == "degraded"
    assert out.get("spec_errors"), "Debe devolver spec_errors si hay fatales tras repair"

    monkeypatch.setattr(sb, "build_spec_from_request_ir", original)


def test_builder_does_not_fallback_to_health_when_capability_has_no_contracts():
    context = {
        "objetivo_tecnico": "Subir documentos a un sistema externo",
        "funcionalidades_clave": ["Subir documentos a un sistema externo"],
        "contratos_api": [],
        "contratos_api_explicitos": [],
        "contratos_api_propuestos": [],
        "capability_coverage": [
            {
                "capability": "Subir documentos a un sistema externo",
                "status": "uncovered",
                "source": "unknown",
            }
        ],
        "integrations": [],
        "persistence": {"required": False},
    }

    with pytest.raises(ValueError, match="Capacidad obligatoria sin cobertura"):
        build_request_ir_from_context(context, descripcion_global="x")
