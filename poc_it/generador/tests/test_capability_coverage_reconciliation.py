from __future__ import annotations

from typing import Any, Dict, List

import pytest

from poc_it.analisis.normalizador_contexto import (
    _build_direct_contract_coverage,
    _build_action_index,
    _build_contract_index,
    _find_unique_structural_coverage,
    _reconcile_capability_coverage_inplace,
)
from poc_it.generador.request_ir import (
    build_request_ir_from_context,
    validate_capability_coverage,
)


def _mk_ctx_base(**overrides: Any) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {
        "nombre": "PoC",
        "objetivo_tecnico": "objetivo",
        "funcionalidades_clave": [],
        "integraciones_externas": [],
        "restricciones_tecnicas": [],
        "technology_signals": [],
        "assumptions": [],
        "evidence": [],
        "integrations": [],
        "configuration": [],
        "contratos_api": [],
        "contratos_api_explicitos": [],
        "contratos_api_propuestos": [],
        "capability_coverage": [],
        "open_questions": [],
    }
    ctx.update(overrides)
    return ctx


def _contract(
    method: str,
    path: str,
    *,
    evidence: str = "",
    assumption: str = "",
    request_type: str = "none",
    schema_hint: Dict[str, Any] | None = None,
    actions: List[Dict[str, Any]] | None = None,
    integration_refs: List[str] | None = None,
) -> Dict[str, Any]:
    req: Dict[str, Any] = {"type": request_type, "schema_hint": schema_hint or {}}
    if evidence:
        req["evidence"] = evidence
    if assumption:
        req["assumption"] = assumption
    return {
        "method": method,
        "path": path,
        "request": req,
        "response": {"json_example": {}},
        "actions": actions or [],
        "errors": [],
        "integration_refs": integration_refs or [],
    }


def test_explicit_post_matches_by_contract_ref_and_rewrites_canonical_id() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=["Subir un fichero mediante POST /upload"],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/upload",
                evidence="POST /upload",
                actions=[
                    {
                        "id": "store_file",
                        "kind": "external_call",
                        "description": "Guardar fichero",
                        "integration_ref": "file_storage",
                    }
                ],
                integration_refs=["file_storage"],
            )
        ],
        capability_coverage=[
            {
                "capability_id": "upload_file",
                "capability": "Subir un fichero",
                "contract_refs": ["POST /upload"],
                "action_refs": ["POST /upload#store_file"],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Subir un fichero",
                "assumption": "",
            }
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    assert len(data["capability_coverage"]) == 1
    coverage = data["capability_coverage"][0]
    assert coverage["capability_id"] == "subir_un_fichero_mediante_post_upload"
    assert coverage["capability"] == "Subir un fichero mediante POST /upload"
    assert coverage["contract_refs"] == ["POST /upload"]
    assert coverage["action_refs"] == ["POST /upload#store_file"]
    assert coverage["integration_refs"] == ["file_storage"]
    assert all(
        "No se pudo vincular inequívocamente capability_coverage con funcionalidades_clave." != msg
        for msg in data["open_questions"]
    )


def test_explicit_get_matches_by_contract_ref() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=["Verificar estado mediante GET /health"],
        contratos_api_explicitos=[_contract("GET", "/health", evidence="GET /health")],
        capability_coverage=[
            {
                "capability_id": "health_check",
                "capability": "Verificar estado",
                "contract_refs": ["GET /health"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Verificar estado",
                "assumption": "",
            }
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    coverage = data["capability_coverage"][0]
    assert coverage["capability_id"] == "verificar_estado_mediante_get_health"
    assert coverage["capability"] == "Verificar estado mediante GET /health"
    assert coverage["contract_refs"] == ["GET /health"]


def test_parameterized_path_matches_by_contract_ref() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=["Obtener usuario mediante GET /users/{id}"],
        contratos_api_explicitos=[_contract("GET", "/users/{id}", evidence="GET /users/{id}")],
        capability_coverage=[
            {
                "capability": "Obtener usuario",
                "contract_refs": ["GET /users/{id}"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Obtener usuario",
                "assumption": "",
            }
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    coverage = data["capability_coverage"][0]
    assert coverage["capability_id"] == "obtener_usuario_mediante_get_users_id"
    assert coverage["capability"] == "Obtener usuario mediante GET /users/{id}"
    assert coverage["contract_refs"] == ["GET /users/{id}"]


def test_functionality_without_method_path_uses_unique_structural_fallback() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=["Enviar una notificación al usuario"],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/notifications",
                evidence="POST /notifications",
                actions=[
                    {
                        "id": "send_notification",
                        "kind": "external_call",
                        "description": "Enviar una notificación",
                        "integration_ref": "notification_provider",
                    }
                ],
                integration_refs=["notification_provider"],
            )
        ],
        capability_coverage=[
            {
                "capability": "Cobertura resumida",
                "contract_refs": ["POST /notifications"],
                "action_refs": [],
                "integration_refs": ["notification_provider"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Cobertura resumida",
                "assumption": "",
            }
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    coverage = data["capability_coverage"][0]
    assert coverage["capability_id"] == "enviar_una_notificaci_n_al_usuario"
    assert coverage["capability"] == "Enviar una notificación al usuario"
    assert coverage["action_refs"] == ["POST /notifications#send_notification"]


def test_functionality_without_method_path_ambiguous_remains_unresolved_and_blocks_generation() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=["Enviar una notificación al usuario"],
        contratos_api_explicitos=[
            _contract("POST", "/notifications/email", evidence="POST /notifications/email"),
            _contract("POST", "/notifications/sms", evidence="POST /notifications/sms"),
        ],
        capability_coverage=[
            {
                "capability": "Cobertura email",
                "contract_refs": ["POST /notifications/email"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "email",
                "assumption": "",
            },
            {
                "capability": "Cobertura sms",
                "contract_refs": ["POST /notifications/sms"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "sms",
                "assumption": "",
            },
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    assert len(data["capability_coverage"]) == 2
    assert any(
        "No se pudo vincular inequívocamente capability_coverage con funcionalidades_clave." == msg
        for msg in data["open_questions"]
    )
    assert any(
        "Capacidad obligatoria sin cobertura vinculada: Enviar una notificación al usuario" == msg
        for msg in data["open_questions"]
    )

    with pytest.raises(
        ValueError,
        match="Capacidad obligatoria sin cobertura",
    ):
        build_request_ir_from_context(data, descripcion_global="x")


def test_llm_capability_id_is_not_used_as_primary_identity() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=["Procesar documento mediante POST /documents"],
        contratos_api_explicitos=[_contract("POST", "/documents", evidence="POST /documents")],
        capability_coverage=[
            {
                "capability_id": "process_doc",
                "capability": "Procesar documento",
                "contract_refs": ["POST /documents"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Procesar documento",
                "assumption": "",
            }
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    coverage = data["capability_coverage"][0]
    assert coverage["capability_id"] == "procesar_documento_mediante_post_documents"
    assert coverage["capability"] == "Procesar documento mediante POST /documents"


def test_exact_text_compatibility_assigns_capability_id() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=["Procesar internamente un fichero"],
        contratos_api_explicitos=[_contract("POST", "/documents", evidence="POST /documents")],
        capability_coverage=[
            {
                "capability": "Procesar internamente un fichero",
                "contract_refs": ["POST /documents"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Procesar internamente un fichero",
                "assumption": "",
            }
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    coverage = data["capability_coverage"][0]
    assert coverage["capability_id"] == "procesar_internamente_un_fichero"
    assert coverage["capability"] == "Procesar internamente un fichero"


def test_generic_external_notification_passes_without_vendor_heuristics() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Enviar una notificación mediante un proveedor externo"],
        integrations=[{"id": "notification_provider", "name": "Notification Provider"}],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/notifications",
                evidence="POST /notifications",
                actions=[
                    {
                        "id": "send_notification",
                        "kind": "external_call",
                        "description": "Enviar una notificación",
                        "integration_ref": "notification_provider",
                    }
                ],
                integration_refs=["notification_provider"],
            )
        ],
        capability_coverage=[
            {
                "capability_id": "enviar_una_notificacion_mediante_un_proveedor_externo",
                "capability": "Enviar una notificación mediante un proveedor externo",
                "contract_refs": ["POST /notifications"],
                "action_refs": ["POST /notifications#send_notification"],
                "integration_refs": ["notification_provider"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Enviar una notificación mediante un proveedor externo",
                "assumption": "",
            }
        ],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert validate_capability_coverage(ir) == []


def test_message_broker_requires_external_call_without_provider_keywords() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Publicar un evento en un broker"],
        integrations=[{"id": "event_broker", "name": "Event Broker"}],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/events",
                evidence="POST /events",
                actions=[
                    {
                        "id": "publish_event",
                        "kind": "external_call",
                        "description": "Publicar evento",
                        "integration_ref": "event_broker",
                    }
                ],
                integration_refs=["event_broker"],
            )
        ],
        capability_coverage=[
            {
                "capability_id": "publicar_un_evento_en_un_broker",
                "capability": "Publicar un evento en un broker",
                "contract_refs": ["POST /events"],
                "action_refs": ["POST /events#publish_event"],
                "integration_refs": ["event_broker"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Publicar un evento en un broker",
                "assumption": "",
            }
        ],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert validate_capability_coverage(ir) == []


def test_upload_verb_does_not_make_internal_processing_external() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Procesar internamente un fichero subido"],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/documents",
                evidence="POST /documents",
                actions=[
                    {
                        "id": "process_uploaded_file",
                        "kind": "internal_processing",
                        "description": "Procesar internamente un fichero subido",
                    }
                ],
            )
        ],
        capability_coverage=[
            {
                "capability_id": "procesar_internamente_un_fichero_subido",
                "capability": "Procesar internamente un fichero subido",
                "contract_refs": ["POST /documents"],
                "action_refs": ["POST /documents#process_uploaded_file"],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Procesar internamente un fichero subido",
                "assumption": "",
            }
        ],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert validate_capability_coverage(ir) == []


def test_integration_without_external_call_fails() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Guardar un documento en almacenamiento externo"],
        integrations=[{"id": "external_storage", "name": "External Storage"}],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/documents",
                evidence="POST /documents",
                actions=[
                    {
                        "id": "prepare_document",
                        "kind": "internal_processing",
                        "description": "Preparar documento",
                    }
                ],
            )
        ],
        capability_coverage=[
            {
                "capability_id": "guardar_un_documento_en_almacenamiento_externo",
                "capability": "Guardar un documento en almacenamiento externo",
                "contract_refs": ["POST /documents"],
                "action_refs": ["POST /documents#prepare_document"],
                "integration_refs": ["external_storage"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Guardar un documento en almacenamiento externo",
                "assumption": "",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="Capacidad con integración externa pero sin acción external_call: Guardar un documento en almacenamiento externo",
    ):
        build_request_ir_from_context(ctx, descripcion_global="x")


def test_external_call_without_integration_fails() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Enviar una notificación mediante un proveedor externo"],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/notifications",
                evidence="POST /notifications",
                actions=[
                    {
                        "id": "send_notification",
                        "kind": "external_call",
                        "description": "Enviar notificación",
                        "integration_ref": None,
                    }
                ],
            )
        ],
        capability_coverage=[
            {
                "capability_id": "enviar_una_notificacion_mediante_un_proveedor_externo",
                "capability": "Enviar una notificación mediante un proveedor externo",
                "contract_refs": ["POST /notifications"],
                "action_refs": ["POST /notifications#send_notification"],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Enviar una notificación mediante un proveedor externo",
                "assumption": "",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="Capacidad con acción external_call pero sin integración asociada: Enviar una notificación mediante un proveedor externo",
    ):
        build_request_ir_from_context(ctx, descripcion_global="x")


def test_reconciliation_adds_unique_external_action() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Guardar un documento en almacenamiento externo"],
        integrations=[{"id": "document_storage", "name": "Document Storage"}],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/documents",
                evidence="POST /documents",
                actions=[
                    {
                        "id": "store_document",
                        "kind": "external_call",
                        "description": "Guardar documento",
                        "integration_ref": "document_storage",
                    }
                ],
                integration_refs=["document_storage"],
            )
        ],
        capability_coverage=[
            {
                "capability_id": "guardar_un_documento_en_almacenamiento_externo",
                "capability": "Guardar un documento en almacenamiento externo",
                "contract_refs": ["POST /documents"],
                "action_refs": [],
                "integration_refs": ["document_storage"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Guardar un documento en almacenamiento externo",
                "assumption": "",
            }
        ],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert ir.capability_coverage[0].action_refs == ["POST /documents#store_document"]


def test_reconciliation_does_not_choose_ambiguous_action() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Publicar un evento en un broker"],
        integrations=[{"id": "event_broker", "name": "Event Broker"}],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/events",
                evidence="POST /events",
                actions=[
                    {
                        "id": "publish_primary",
                        "kind": "external_call",
                        "description": "Publicar evento primario",
                        "integration_ref": "event_broker",
                    },
                    {
                        "id": "publish_secondary",
                        "kind": "external_call",
                        "description": "Publicar evento secundario",
                        "integration_ref": "event_broker",
                    },
                ],
                integration_refs=["event_broker"],
            )
        ],
        capability_coverage=[
            {
                "capability_id": "publicar_un_evento_en_un_broker",
                "capability": "Publicar un evento en un broker",
                "contract_refs": ["POST /events"],
                "action_refs": [],
                "integration_refs": ["event_broker"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Publicar un evento en un broker",
                "assumption": "",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="Capacidad con integración externa pero sin acción external_call: Publicar un evento en un broker",
    ):
        build_request_ir_from_context(ctx, descripcion_global="x")


def test_missing_capability_coverage_fails_without_fabricated_uncovered_entries() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Enviar una notificación mediante un proveedor externo"],
        integrations=[{"id": "notification_provider", "name": "Notification Provider"}],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/notifications",
                evidence="POST /notifications",
                actions=[
                    {
                        "id": "send_notification",
                        "kind": "external_call",
                        "description": "Enviar notificación",
                        "integration_ref": "notification_provider",
                    }
                ],
                integration_refs=["notification_provider"],
            )
        ],
    )

    with pytest.raises(
        ValueError,
        match="Existen capacidades funcionales, pero el normalizador no produjo capability_coverage.",
    ):
        build_request_ir_from_context(ctx, descripcion_global="x")


def test_produced_but_unlinked_coverage_is_preserved_and_not_reported_as_missing() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=["Capacidad canónica", "Segunda capacidad"],
        contratos_api_explicitos=[
            _contract("POST", "/documents", evidence="POST /documents"),
            _contract("GET", "/status", evidence="GET /status"),
        ],
        capability_coverage=[
            {
                "capability": "Cobertura no vinculada",
                "contract_refs": ["POST /documents"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Cobertura no vinculada",
                "assumption": "",
            },
            {
                "capability": "Otra cobertura no vinculada",
                "contract_refs": ["GET /status"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Otra cobertura no vinculada",
                "assumption": "",
            },
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    assert len(data["capability_coverage"]) == 2
    assert any(
        "Capability coverage producida pero no vinculada a funcionalidades_clave: Cobertura no vinculada" == msg
        for msg in data["open_questions"]
    )
    assert any(
        "Capability coverage producida pero no vinculada a funcionalidades_clave: Otra cobertura no vinculada" == msg
        for msg in data["open_questions"]
    )
    assert all(
        "Existen capacidades funcionales, pero el normalizador no produjo capability_coverage." != msg
        for msg in data["open_questions"]
    )


def test_unique_structural_is_blocked_for_explicit_endpoint_capabilities() -> None:
    data = _mk_ctx_base(
        contratos_api_explicitos=[_contract("GET", "/health", evidence="GET /health")],
    )
    contract_index = _build_contract_index(data)
    action_index = _build_action_index(data)

    result = _find_unique_structural_coverage(
        capability="Endpoint POST /upload",
        candidates=[
            {
                "capability": "Endpoint GET /health",
                "contract_refs": ["GET /health"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "GET /health",
                "assumption": "",
            }
        ],
        contract_index=contract_index,
        action_index=action_index,
    )

    assert result is None


def test_build_direct_contract_coverage_returns_minimal_derived_coverage_for_existing_contract() -> None:
    contract_index = {
        "DELETE /users/{id}": _contract(
            "DELETE",
            "/users/{id}",
            evidence="DELETE /users/{id}",
        )
    }

    coverage = _build_direct_contract_coverage(
        capability_id="endpoint_delete_users_id",
        capability="Endpoint DELETE /users/{id}",
        contract_refs=["DELETE /users/{id}"],
        contract_index=contract_index,
    )

    assert coverage == {
        "capability_id": "endpoint_delete_users_id",
        "capability": "Endpoint DELETE /users/{id}",
        "contract_refs": ["DELETE /users/{id}"],
        "action_refs": [],
        "integration_refs": [],
        "status": "covered",
        "source": "derived",
        "evidence": "DELETE /users/{id}",
        "assumption": "",
    }


def test_build_direct_contract_coverage_returns_none_when_contract_does_not_exist() -> None:
    coverage = _build_direct_contract_coverage(
        capability_id="endpoint_delete_users_id",
        capability="Endpoint DELETE /users/{id}",
        contract_refs=["DELETE /users/{id}"],
        contract_index={},
    )

    assert coverage is None


def test_endpoint_capability_with_existing_contract_uses_direct_contract_without_cross_endpoint_fallback() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=[
            "Autenticación con almacenamiento externo mediante credenciales",
            "Subir un fichero de prueba al almacenamiento externo",
            "Endpoint POST /upload",
            "Endpoint GET /health",
        ],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/upload",
                evidence="POST /upload",
                actions=[
                    {
                        "id": "upload_file",
                        "kind": "external_call",
                        "description": "Subir fichero",
                        "integration_ref": "external_storage",
                    }
                ],
                integration_refs=["external_storage"],
            ),
            _contract(
                "GET",
                "/health",
                evidence="GET /health",
            ),
        ],
        capability_coverage=[
            {
                "capability": "Autenticación con almacenamiento externo mediante credenciales",
                "contract_refs": ["POST /upload"],
                "action_refs": ["POST /upload#upload_file"],
                "integration_refs": ["external_storage"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Autenticación con almacenamiento externo mediante credenciales",
                "assumption": "",
            },
            {
                "capability": "Subir un fichero de prueba al almacenamiento externo",
                "contract_refs": ["POST /upload"],
                "action_refs": ["POST /upload#upload_file"],
                "integration_refs": ["external_storage"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Subir un fichero de prueba al almacenamiento externo",
                "assumption": "",
            },
            {
                "capability": "Endpoint GET /health",
                "contract_refs": ["GET /health"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Endpoint GET /health",
                "assumption": "",
            },
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    by_capability = {
        item["capability"]: item
        for item in data["capability_coverage"]
    }
    assert "Endpoint POST /upload" in by_capability
    assert by_capability["Endpoint POST /upload"]["contract_refs"] == ["POST /upload"]
    assert by_capability["Endpoint POST /upload"]["action_refs"] == []
    assert by_capability["Endpoint POST /upload"]["integration_refs"] == []
    assert by_capability["Endpoint POST /upload"]["source"] == "derived"
    assert not any(
        link["capability"] == "Endpoint POST /upload"
        and link["coverage_capability"] == "Endpoint GET /health"
        for link in data.get("_debug", {}).get("links", [])
    )
    assert not any(
        "Capacidad obligatoria sin cobertura vinculada: Endpoint GET /health" == msg
        for msg in data["open_questions"]
    )


def test_functionality_without_method_path_still_uses_unique_structural_fallback_after_fix() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=["Procesar una notificación"],
        contratos_api_explicitos=[
            _contract("POST", "/notifications", evidence="POST /notifications")
        ],
        capability_coverage=[
            {
                "capability": "Cobertura estructural única",
                "contract_refs": ["POST /notifications"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Cobertura estructural única",
                "assumption": "",
            }
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    coverage = data["capability_coverage"][0]
    assert coverage["capability"] == "Procesar una notificación"
    assert coverage["capability_id"] == "procesar_una_notificaci_n"


def test_endpoint_capability_with_missing_contract_remains_unresolved() -> None:
    data = _mk_ctx_base(
        funcionalidades_clave=["Endpoint DELETE /users/{id}"],
        contratos_api_explicitos=[_contract("GET", "/health", evidence="GET /health")],
        capability_coverage=[
            {
                "capability": "Endpoint GET /health",
                "contract_refs": ["GET /health"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Endpoint GET /health",
                "assumption": "",
            }
        ],
    )

    _reconcile_capability_coverage_inplace(data)

    assert any(
        "Capacidad obligatoria sin cobertura vinculada: Endpoint DELETE /users/{id}" == msg
        for msg in data["open_questions"]
    )
    assert all(
        item["capability"] != "Endpoint DELETE /users/{id}"
        for item in data["capability_coverage"]
    )


def test_google_drive_current_case_reconciles_both_capabilities_and_request_ir_is_valid() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=[
            "Subir un fichero a Google Drive mediante POST /upload",
            "Verificar el estado del servicio mediante GET /health",
        ],
        integrations=[{"id": "google_drive_api", "name": "Google Drive API"}],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/upload",
                evidence="POST /upload",
                actions=[
                    {
                        "id": "generate_test_file",
                        "kind": "internal_processing",
                        "description": "Generar fichero de prueba",
                    },
                    {
                        "id": "upload_to_google_drive",
                        "kind": "external_call",
                        "description": "Subir fichero a Google Drive",
                        "integration_ref": "google_drive_api",
                    },
                ],
                integration_refs=["google_drive_api"],
            ),
            _contract(
                "GET",
                "/health",
                evidence="GET /health",
                actions=[
                    {
                        "id": "return_health_status",
                        "kind": "internal_processing",
                        "description": "Devolver estado",
                    }
                ],
            ),
        ],
        capability_coverage=[
            {
                "capability_id": "upload_file",
                "capability": "Subir un fichero a Google Drive",
                "contract_refs": ["POST /upload"],
                "action_refs": [
                    "POST /upload#generate_test_file",
                    "POST /upload#upload_to_google_drive",
                ],
                "integration_refs": ["google_drive_api"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Subir un fichero a Google Drive",
                "assumption": "",
            },
            {
                "capability_id": "health_check",
                "capability": "Verificar el estado del servicio",
                "contract_refs": ["GET /health"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "Verificar el estado del servicio",
                "assumption": "",
            },
        ],
    )

    _reconcile_capability_coverage_inplace(ctx)

    assert len(ctx["capability_coverage"]) == 2
    by_capability = {item["capability"]: item for item in ctx["capability_coverage"]}
    assert "Subir un fichero a Google Drive mediante POST /upload" in by_capability
    assert "Verificar el estado del servicio mediante GET /health" in by_capability
    assert by_capability["Subir un fichero a Google Drive mediante POST /upload"]["capability_id"] == (
        "subir_un_fichero_a_google_drive_mediante_post_upload"
    )
    assert by_capability["Verificar el estado del servicio mediante GET /health"]["capability_id"] == (
        "verificar_el_estado_del_servicio_mediante_get_health"
    )
    assert by_capability["Subir un fichero a Google Drive mediante POST /upload"]["contract_refs"] == [
        "POST /upload"
    ]
    assert by_capability["Verificar el estado del servicio mediante GET /health"]["contract_refs"] == [
        "GET /health"
    ]
    assert not any(
        "No se pudo vincular inequívocamente capability_coverage con funcionalidades_clave." == msg
        for msg in ctx["open_questions"]
    )
    assert not any(
        msg.startswith("Capability coverage producida pero no vinculada a funcionalidades_clave:")
        for msg in ctx["open_questions"]
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert len(ir.capability_coverage) == 2
    assert validate_capability_coverage(ir) == []
    assert {contract.path for contract in ir.explicit_api_contracts} == {"/upload", "/health"}
