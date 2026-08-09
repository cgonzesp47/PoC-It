from __future__ import annotations

import json
from typing import Any

import pytest

from poc_it.analisis.normalizador_contexto import normalizar_plantilla
from poc_it.modulos.models import PlantillaUsuario


def _mk_template(text: str) -> PlantillaUsuario:
    # PlantillaUsuario es un modelo del proyecto; en tests solo necesitamos poblar campos.
    # Nota: la implementación real puede tener defaults; aquí usamos los mínimos observados.
    return PlantillaUsuario(
        nombre="PoC",
        problema=text,
        usuarios="",
        funcionalidades=text,
        limites="",
        tecnologias="",
    )


def test_crud_productos_produce_endpoints_propuestos(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        # Sin evidencia literal de endpoints => propuestos con assumption
        return json.dumps(
            {
                "objetivo_tecnico": "crud productos",
                "contratos_api_explicitos": [],
                "contratos_api_propuestos": [
                    {
                        "method": "GET",
                        "path": "/products",
                        "request": {"type": "none", "schema_hint": {}, "assumption": "CRUD típico propuesto"},
                        "response": {"json_example": {}},
                    }
                ],
                "assumptions": [],
                "evidence": [],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("quiero un CRUD de productos")
    ctx = normalizar_plantilla(tpl)

    assert ctx["contratos_api_explicitos"] == []
    assert len(ctx["contratos_api_propuestos"]) == 1
    assert ctx["contratos_api_propuestos"][0]["request"]["assumption"]
    assert ctx["contratos_api_propuestos"][0]["actions"] == []
    assert ctx["contratos_api_propuestos"][0]["errors"] == []
    assert ctx["contratos_api_propuestos"][0]["integration_refs"] == []
    # legacy: contratos_api solo explícitos
    assert ctx["contratos_api"] == []


def test_crud_productos_postgresql_template_fallbacks_keep_safe_without_inventing_persistence_or_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        # Simula respuesta pobre: sin fields útiles
        return json.dumps({})

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = PlantillaUsuario(
        nombre="PoC",
        problema="Validar técnicamente que un microservicio desarrollado en FastAPI puede conectarse a una base de datos PostgreSQL externa y realizar operaciones CRUD básicas sobre una entidad simple",
        usuarios="",
        funcionalidades="El sistema debe permitir gestionar productos mediante operaciones básicas de creación, consulta, modificación y eliminación. La información debe persistirse en una base de datos PostgreSQL.",
        limites="La conexión a la base de datos debe configurarse mediante variables de entorno.",
        tecnologias="fastapi, uvicorn, pydantic, sqlalchemy, psycopg2-binary, PostgreSQL",
    )

    ctx = normalizar_plantilla(tpl)

    # funcionalidades/limitaciones salen de la plantilla aunque el LLM falle
    assert ctx["funcionalidades_clave"]
    assert any(
        "gestionar productos" in f.lower() or "crud" in f.lower() for f in ctx["funcionalidades_clave"]
    )
    assert ctx["restricciones_tecnicas"]
    assert any("variables de entorno" in r.lower() for r in ctx["restricciones_tecnicas"])

    # technology signals salen de tecnologias declaradas
    assert any(s["name"].lower() == "fastapi" for s in ctx["technology_signals"])
    assert any(s["name"].lower() == "postgresql" for s in ctx["technology_signals"])

    # persistencia NO se infiere por texto libre si el normalizador LLM no la estructuró con evidence
    assert ctx["persistence"]["required"] is False
    assert ctx["persistence"]["evidence"] == []
    assert ctx["persistence"]["business_entities"] == []
    assert ctx["persistence"]["uncertainty"] in (
        "persistence_not_structured_from_normalizer",
        "durable_state_without_evidence",
        "",
    )

    # endpoints NO se proponen sin operation_groups + domain_entities estructurados
    assert ctx["contratos_api_explicitos"] == []
    assert ctx["contratos_api_propuestos"] == []


def test_endpoint_literal_post_products_produce_explicito_with_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "objetivo_tecnico": "api",
                "contratos_api_explicitos": [
                    {
                        "method": "POST",
                        "path": "/products",
                        "request": {"type": "json", "schema_hint": {}, "evidence": "crear endpoint POST /products"},
                        "response": {"json_example": {}, "evidence": ""},
                    }
                ],
                "contratos_api_propuestos": [],
                "assumptions": [],
                "evidence": [],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("crear endpoint POST /products")
    ctx = normalizar_plantilla(tpl)

    assert len(ctx["contratos_api_explicitos"]) == 1
    c = ctx["contratos_api_explicitos"][0]
    assert c["method"] == "POST"
    assert c["path"] == "/products"
    assert c["request"]["evidence"]
    assert c["actions"] == []
    assert c["errors"] == []
    assert c["integration_refs"] == []
    assert ctx["contratos_api_propuestos"] == []
    # legacy: contratos_api refleja explícitos
    assert len(ctx["contratos_api"]) == 1


def test_tech_signal_postgresql_does_not_enable_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "objetivo_tecnico": "x",
                "contratos_api_explicitos": [],
                "contratos_api_propuestos": [],
                "technology_signals": [
                    {
                        "name": "PostgreSQL",
                        "category": "persistence",
                        "role": "database",
                        "evidence": "Usar PostgreSQL",
                        "confidence": "explicit",
                    }
                ],
                "persistence": {
                    "required": False,
                    "kind": None,
                    "durable_state": False,
                    "business_entities": [],
                    "evidence": [],
                    "uncertainty": "",
                },
                "state_requirements": {"durable": False, "entities": [], "evidence": []},
                "assumptions": [],
                "evidence": [],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("Usar PostgreSQL")
    ctx = normalizar_plantilla(tpl)

    assert ctx["persistence"]["required"] is False
    assert any(s["name"] == "PostgreSQL" for s in ctx["technology_signals"])
    assert any("Usar PostgreSQL" in s["evidence"] for s in ctx["technology_signals"])


def test_state_durable_business_requires_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "objetivo_tecnico": "x",
                "contratos_api_explicitos": [],
                "contratos_api_propuestos": [],
                "persistence": {
                    "required": True,
                    "kind": "unknown",
                    "durable_state": True,
                    "business_entities": ["pedidos"],
                    "evidence": ["Guardar pedidos y consultarlos después"],
                    "uncertainty": "",
                },
                "state_requirements": {
                    "durable": True,
                    "entities": ["pedidos"],
                    "evidence": ["Guardar pedidos y consultarlos después"],
                },
                "technology_signals": [],
                "assumptions": [],
                "evidence": [],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("Guardar pedidos y consultarlos después")
    ctx = normalizar_plantilla(tpl)

    assert ctx["persistence"]["required"] is True
    assert ctx["persistence"]["durable_state"] is True
    assert "pedidos" in ctx["persistence"]["business_entities"]
    assert any("Guardar pedidos" in ev for ev in ctx["persistence"]["evidence"])
    assert ctx["state_requirements"]["durable"] is True


def test_redis_cache_signal_does_not_enable_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "objetivo_tecnico": "x",
                "contratos_api_explicitos": [],
                "contratos_api_propuestos": [],
                "technology_signals": [
                    {
                        "name": "Redis",
                        "category": "cache",
                        "role": "cache",
                        "evidence": "Usar Redis como cache",
                        "confidence": "explicit",
                    }
                ],
                "persistence": {"required": False, "kind": None, "durable_state": False, "business_entities": [], "evidence": [], "uncertainty": ""},
                "state_requirements": {"durable": False, "entities": [], "evidence": []},
                "assumptions": [],
                "evidence": [],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("Usar Redis como cache")
    ctx = normalizar_plantilla(tpl)

    assert ctx["persistence"]["required"] is False
    assert any(s["name"] == "Redis" and s["category"] == "cache" for s in ctx["technology_signals"])


def test_stateless_no_db(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "objetivo_tecnico": "x",
                "contratos_api_explicitos": [],
                "contratos_api_propuestos": [],
                "persistence": {"required": False, "kind": None, "durable_state": False, "business_entities": [], "evidence": [], "uncertainty": ""},
                "state_requirements": {"durable": False, "entities": [], "evidence": []},
                "technology_signals": [],
                "assumptions": [],
                "evidence": [],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("Servicio stateless sin base de datos")
    ctx = normalizar_plantilla(tpl)

    assert ctx["persistence"]["required"] is False
    assert ctx["state_requirements"]["durable"] is False


def test_durable_state_true_without_evidence_does_not_enable_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "persistence": {
                    "required": False,
                    "kind": None,
                    "durable_state": True,
                    "business_entities": ["orders"],
                    "evidence": [],
                    "uncertainty": "",
                },
                "state_requirements": {"durable": False, "entities": [], "evidence": []},
                "contratos_api_explicitos": [],
                "contratos_api_propuestos": [],
                "technology_signals": [],
                "assumptions": [],
                "evidence": [],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("x")
    ctx = normalizar_plantilla(tpl)

    assert ctx["persistence"]["required"] is False
    assert ctx["persistence"]["durable_state"] is False
    assert ctx["persistence"]["uncertainty"] == "durable_state_without_evidence"


def test_state_requirements_durable_with_evidence_enables_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "persistence": {
                    "required": False,
                    "kind": None,
                    "durable_state": False,
                    "business_entities": [],
                    "evidence": [],
                    "uncertainty": "",
                },
                "state_requirements": {
                    "durable": True,
                    "entities": ["pedidos"],
                    "evidence": ["Guardar pedidos y consultarlos después"],
                },
                "contratos_api_explicitos": [],
                "contratos_api_propuestos": [],
                "technology_signals": [],
                "assumptions": [],
                "evidence": [],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("Guardar pedidos y consultarlos después")
    ctx = normalizar_plantilla(tpl)

    assert ctx["persistence"]["required"] is True
    assert ctx["persistence"]["durable_state"] is True
    assert "pedidos" in ctx["persistence"]["business_entities"]
    assert any("Guardar pedidos" in ev for ev in ctx["persistence"]["evidence"])
    assert ctx["persistence"]["kind"] == "unknown"


def test_persistence_required_true_with_evidence_aligns_state_requirements(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "persistence": {
                    "required": True,
                    "kind": "unknown",
                    "durable_state": True,
                    "business_entities": ["pedidos"],
                    "evidence": ["Guardar pedidos y consultarlos después"],
                    "uncertainty": "",
                },
                "state_requirements": {"durable": False, "entities": [], "evidence": []},
                "contratos_api_explicitos": [],
                "contratos_api_propuestos": [],
                "technology_signals": [],
                "assumptions": [],
                "evidence": [],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("Guardar pedidos y consultarlos después")
    ctx = normalizar_plantilla(tpl)

    assert ctx["persistence"]["required"] is True
    assert ctx["state_requirements"]["durable"] is True
    assert "pedidos" in ctx["state_requirements"]["entities"]
    assert any("Guardar pedidos" in ev for ev in ctx["state_requirements"]["evidence"])


def test_contradiction_without_evidence_prefers_required_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "persistence": {
                    "required": True,
                    "kind": "unknown",
                    "durable_state": True,
                    "business_entities": ["pedidos"],
                    "evidence": [],
                    "uncertainty": "",
                },
                "state_requirements": {"durable": True, "entities": ["pedidos"], "evidence": []},
                "contratos_api_explicitos": [],
                "contratos_api_propuestos": [],
                "technology_signals": [],
                "assumptions": [],
                "evidence": [],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("x")
    ctx = normalizar_plantilla(tpl)

    assert ctx["persistence"]["required"] is False
    assert ctx["state_requirements"]["durable"] is False
    assert any("Contradicción" in a or "sin evidence" in a for a in ctx["assumptions"])


def test_llm_partial_response_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        # Parcial: solo devuelve un signal sin evidence y un contrato explícito mal formado
        return json.dumps(
            {
                "technology_signals": [{"name": "PostgreSQL"}],
                "contratos_api_explicitos": [
                    {
                        "method": "GET",
                        "path": "/x",
                        "request": {"type": "none", "schema_hint": {}, "evidence": ""},
                        "response": {"json_example": {}, "evidence": ""},
                    }
                ],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    tpl = _mk_template("texto")
    ctx = normalizar_plantilla(tpl)

    # No se aceptan explícitos sin evidence => se mueven a propuestos
    assert ctx["contratos_api_explicitos"] == []
    assert len(ctx["contratos_api_propuestos"]) == 1
    assert ctx["contratos_api_propuestos"][0]["request"]["assumption"]

    # Señales sin evidence se descartan
    assert ctx["technology_signals"] == []
    assert ctx["persistence"]["required"] is False
    assert ctx["state_requirements"]["durable"] is False


def test_llm_failure_fallback_does_not_propose_crud_endpoints_without_structured_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: "<<<not json>>>",
    )

    tpl = _mk_template("quiero un CRUD de productos")
    ctx = normalizar_plantilla(tpl)

    assert ctx["contratos_api_explicitos"] == []
    assert ctx["contratos_api_propuestos"] == []
    assert ctx["contratos_api"] == []
    assert ctx["persistence"]["required"] is False
    assert ctx["technology_signals"] == []
    assert ctx["state_requirements"]["durable"] is False


def test_preserva_integracion_explicita(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "integrations": [
                    {
                        "id": "google_drive",
                        "name": "Google Drive API",
                        "kind": "external_api",
                        "required": True,
                        "implementation_level": "integration_skeleton",
                        "source": "explicit",
                        "evidence": "subirlo a Google Drive",
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    ctx = normalizar_plantilla(_mk_template("subirlo a Google Drive"))

    assert len(ctx["integrations"]) == 1
    integration = ctx["integrations"][0]
    assert integration["id"] == "google_drive"
    assert integration["name"] == "Google Drive API"
    assert integration["kind"] == "external_api"
    assert integration["implementation_level"] == "integration_skeleton"
    assert integration["source"] == "explicit"
    assert integration["evidence"] == "subirlo a Google Drive"


def test_accion_externa_enlazada_se_conserva(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "integrations": [
                    {
                        "id": "google_drive",
                        "name": "Google Drive API",
                        "kind": "external_api",
                        "required": True,
                        "implementation_level": "integration_skeleton",
                        "source": "explicit",
                        "evidence": "subirlo a Google Drive",
                    }
                ],
                "contratos_api_propuestos": [
                    {
                        "method": "POST",
                        "path": "/upload",
                        "request": {"type": "multipart", "schema_hint": {}, "assumption": "endpoint propuesto"},
                        "response": {"json_example": {}},
                        "actions": [
                            {
                                "id": "upload_to_drive",
                                "kind": "external_call",
                                "description": "Subir archivo a Google Drive",
                                "required": True,
                                "integration_ref": "google_drive",
                                "source": "explicit",
                                "evidence": "subirlo a Google Drive",
                            }
                        ],
                        "integration_refs": ["google_drive"],
                    }
                ],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    ctx = normalizar_plantilla(_mk_template("subirlo a Google Drive"))

    assert len(ctx["contratos_api_propuestos"]) == 1
    contract = ctx["contratos_api_propuestos"][0]
    assert contract["integration_refs"] == ["google_drive"]
    assert len(contract["actions"]) == 1
    action = contract["actions"][0]
    assert action["kind"] == "external_call"
    assert action["integration_ref"] == "google_drive"


def test_errores_explicitos_401_403_404_se_conservan(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "contratos_api_explicitos": [
                    {
                        "method": "GET",
                        "path": "/files/{id}",
                        "request": {"type": "none", "schema_hint": {}, "evidence": "GET /files/{id}"},
                        "response": {"json_example": {}, "evidence": "GET /files/{id}"},
                        "errors": [
                            {
                                "status_code": 401,
                                "code": "unauthorized",
                                "description": "No autenticado",
                                "required": True,
                                "source": "explicit",
                                "evidence": "devolver 401",
                            },
                            {
                                "status_code": 403,
                                "code": "forbidden",
                                "description": "Sin permisos",
                                "required": True,
                                "source": "explicit",
                                "evidence": "devolver 403",
                            },
                            {
                                "status_code": 404,
                                "code": "file_not_found",
                                "description": "Archivo no encontrado",
                                "required": True,
                                "source": "explicit",
                                "evidence": "devolver 404",
                            },
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    ctx = normalizar_plantilla(_mk_template("GET /files/{id} devolver 401 403 404"))
    errors = ctx["contratos_api_explicitos"][0]["errors"]

    assert [error["status_code"] for error in errors] == [401, 403, 404]
    assert [error["code"] for error in errors] == ["unauthorized", "forbidden", "file_not_found"]


def test_respuesta_legacy_sin_bloques_nuevos_produce_listas_vacias(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "contratos_api_explicitos": [
                    {
                        "method": "POST",
                        "path": "/upload",
                        "request": {"type": "multipart", "schema_hint": {}, "evidence": "endpoint /upload"},
                        "response": {"json_example": {}, "evidence": ""},
                    }
                ]
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    ctx = normalizar_plantilla(_mk_template("endpoint /upload"))

    assert ctx["integrations"] == []
    assert ctx["configuration"] == []
    assert ctx["contratos_api_explicitos"][0]["actions"] == []
    assert ctx["contratos_api_explicitos"][0]["errors"] == []
    assert ctx["contratos_api_explicitos"][0]["integration_refs"] == []


def test_explicit_sin_evidence_se_degrada_a_inferred_con_assumption(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat_completion_json(**kwargs: Any) -> str:
        return json.dumps(
            {
                "integrations": [
                    {
                        "id": "drive",
                        "name": "Drive",
                        "kind": "external_api",
                        "required": True,
                        "implementation_level": "integration_skeleton",
                        "source": "explicit",
                        "evidence": "",
                    }
                ],
                "contratos_api_propuestos": [
                    {
                        "method": "POST",
                        "path": "/upload",
                        "request": {"type": "multipart", "schema_hint": {}, "assumption": "endpoint propuesto"},
                        "response": {"json_example": {}},
                        "actions": [
                            {
                                "id": "call_drive",
                                "kind": "external_call",
                                "description": "Llamar a Drive",
                                "required": True,
                                "integration_ref": "drive",
                                "source": "explicit",
                                "evidence": "",
                            }
                        ],
                    }
                ],
            }
        )

    monkeypatch.setattr(
        "poc_it.analisis.normalizador_contexto.chat_completion_json",
        lambda **kwargs: fake_chat_completion_json(**kwargs),
    )

    ctx = normalizar_plantilla(_mk_template("subida de archivos"))

    integration = ctx["integrations"][0]
    action = ctx["contratos_api_propuestos"][0]["actions"][0]

    assert integration["source"] == "inferred"
    assert integration["assumption"]
    assert integration["evidence"] == ""

    assert action["source"] == "inferred"
    assert action["assumption"]
    assert action["evidence"] == ""
