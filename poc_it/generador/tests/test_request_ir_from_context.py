from __future__ import annotations

from typing import Any, Dict, List

import pytest

from poc_it.generador.prompts_file_contracts import (
    _summarize_related_contract,
    build_prompt_file_contract,
    build_prompt_file_contract_fix,
)
from poc_it.generador.request_ir import (
    ApiContractIR,
    PersistenceIR,
    RequestIR,
    build_request_ir_from_context,
    request_ir_to_dict,
    validate_request_ir,
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
    }
    ctx.update(overrides)
    return ctx


def _contract(
    method: str,
    path: str,
    *,
    evidence: str = "",
    assumption: str = "",
    actions: List[Dict[str, Any]] | None = None,
    errors: List[Dict[str, Any]] | None = None,
    integration_refs: List[str] | None = None,
) -> Dict[str, Any]:
    req: Dict[str, Any] = {"type": "none", "schema_hint": {}}
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
        "errors": errors or [],
        "integration_refs": integration_refs or [],
    }


def test_proposed_contracts_consumed_when_legacy_empty() -> None:
    ctx = _mk_ctx_base(
        contratos_api=[],
        contratos_api_explicitos=[],
        contratos_api_propuestos=[_contract("GET", "/products", assumption="propuesto")],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert ir.explicit_api_contracts == []
    assert ir.proposed_api_contracts
    assert ir.proposed_api_contracts[0].path == "/products"
    assert ir.proposed_api_contracts[0].assumption


def test_explicit_contracts_with_evidence_go_to_explicit() -> None:
    ctx = _mk_ctx_base(
        contratos_api_explicitos=[_contract("POST", "/products", evidence="POST /products literal")],
        contratos_api_propuestos=[],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert ir.explicit_api_contracts
    assert ir.explicit_api_contracts[0].method == "POST"
    assert ir.explicit_api_contracts[0].evidence
    assert ir.proposed_api_contracts == []


def test_explicit_contract_without_evidence_is_moved_to_proposed() -> None:
    ctx = _mk_ctx_base(
        contratos_api_explicitos=[_contract("GET", "/x")],
        contratos_api_propuestos=[],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert ir.explicit_api_contracts == []
    assert ir.proposed_api_contracts
    assert ir.proposed_api_contracts[0].path == "/x"
    assert ir.proposed_api_contracts[0].assumption


def test_persistence_required_true_is_preserved_from_ctx_persistence() -> None:
    ctx = _mk_ctx_base(
        persistence={
            "required": True,
            "kind": "relational",
            "durable_state": True,
            "business_entities": ["productos"],
            "evidence": ["La informaciรณn debe persistirse"],
            "uncertainty": "",
        }
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert ir.persistence.required is True
    assert ir.persistence.kind in ("relational", "unknown")
    assert ir.persistence.evidence


def test_state_requirements_durable_with_evidence_can_build_persistence() -> None:
    ctx = _mk_ctx_base(
        persistence={},
        state_requirements={"durable": True, "entities": ["productos"], "evidence": ["Guardar productos"]},
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert ir.persistence.required is True
    assert ir.persistence.kind == "unknown"
    assert ir.persistence.evidence == ["Guardar productos"]


def test_realistic_crud_ctx_preserves_entities_ops_contracts_and_persistence() -> None:
    ctx = _mk_ctx_base(
        contratos_api=[],
        contratos_api_explicitos=[],
        contratos_api_propuestos=[
            _contract("GET", "/products", assumption="crud propuesto"),
            _contract("POST", "/products", assumption="crud propuesto"),
        ],
        persistence={
            "required": True,
            "kind": "relational",
            "durable_state": True,
            "business_entities": ["productos"],
            "evidence": ["La informaciรณn debe persistirse en una base de datos PostgreSQL."],
            "uncertainty": "",
        },
        domain_entities=[
            {
                "name": "Producto",
                "singular": "producto",
                "plural": "productos",
                "slug": "products",
                "evidence": "gestionar productos",
                "confidence": "explicit",
            }
        ],
        operation_groups=[{"type": "crud", "entity": "productos", "evidence": "operaciones CRUD", "confidence": "explicit"}],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert ir.proposed_api_contracts
    assert ir.persistence.required is True
    assert ir.domain_entities
    assert ir.operation_groups


def test_google_drive_integration_auth_configuration_and_endpoint_details_are_parsed() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=[
            "Generar internamente un fichero de prueba",
            "Subirlo a Google Drive",
            "Exponer un health check local",
        ],
        technology_signals=[
            {
                "name": "Google Drive API",
                "category": "external_api",
                "role": "almacenamiento",
                "evidence": "Google Drive",
                "confidence": "explicit",
            }
        ],
        integrations=[
            {
                "id": "google_drive",
                "name": "Google Drive API",
                "kind": "external_api",
                "role": "subida de archivos",
                "required": True,
                "implementation_level": "integration_skeleton",
                "authentication": {
                    "mechanism": "adc",
                    "source": "explicit",
                    "evidence": "ADC",
                    "assumption": "",
                },
                "technology_refs": ["Google Drive API"],
                "configuration_refs": ["DRIVE_FOLDER_ID"],
                "source": "explicit",
                "evidence": "subirlo a Google Drive",
                "assumption": "",
            }
        ],
        configuration=[
            {
                "key": "DRIVE_FOLDER_ID",
                "purpose": "carpeta destino",
                "required": True,
                "secret": False,
                "source": "explicit",
                "evidence": "usar DRIVE_FOLDER_ID",
                "assumption": "",
            }
        ],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/upload",
                evidence="POST /upload",
                actions=[
                    {
                        "id": "generate_test_file",
                        "kind": "internal_processing",
                        "description": "Generar internamente un fichero de prueba",
                        "required": True,
                        "integration_ref": None,
                        "source": "explicit",
                        "evidence": "Generar internamente un fichero de prueba",
                        "assumption": "",
                    },
                    {
                        "id": "upload_to_google_drive",
                        "kind": "external_call",
                        "description": "Subir archivo a Google Drive",
                        "required": True,
                        "integration_ref": "google_drive",
                        "source": "explicit",
                        "evidence": "subirlo a Google Drive",
                        "assumption": "",
                    },
                ],
                errors=[
                    {
                        "status_code": 401,
                        "code": "unauthorized",
                        "description": "No autorizado",
                        "required": True,
                        "source": "explicit",
                        "evidence": "401",
                        "assumption": "",
                    },
                    {
                        "status_code": 403,
                        "code": "forbidden",
                        "description": "Prohibido",
                        "required": True,
                        "source": "explicit",
                        "evidence": "403",
                        "assumption": "",
                    },
                    {
                        "status_code": 404,
                        "code": "not_found",
                        "description": "No encontrado",
                        "required": True,
                        "source": "explicit",
                        "evidence": "404",
                        "assumption": "",
                    },
                ],
                integration_refs=["google_drive"],
            )
        ],
        contratos_api_propuestos=[],
        capability_coverage=[
            {
                "capability": "Generar internamente un fichero de prueba",
                "contract_refs": ["POST /upload"],
                "action_refs": ["POST /upload#generate_test_file"],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "generar internamente un fichero de prueba",
                "assumption": "",
            },
            {
                "capability": "Subirlo a Google Drive",
                "contract_refs": ["POST /upload"],
                "action_refs": ["POST /upload#upload_to_google_drive"],
                "integration_refs": ["google_drive"],
                "status": "covered",
                "source": "explicit",
                "evidence": "subirlo a Google Drive",
                "assumption": "",
            },
            {
                "capability": "Exponer un health check local",
                "contract_refs": ["GET /health"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "GET /health",
                "assumption": "",
            },
        ],
        contratos_api=[_contract("GET", "/health", evidence="GET /health")],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")

    assert len(ir.integrations) == 1
    assert ir.integrations[0].id == "google_drive"
    assert ir.integrations[0].authentication.mechanism == "adc"
    assert ir.integrations[0].authentication.source == "explicit"
    assert ir.integrations[0].configuration_refs == ["DRIVE_FOLDER_ID"]

    assert len(ir.configuration) == 1
    assert ir.configuration[0].key == "DRIVE_FOLDER_ID"

    assert len(ir.explicit_api_contracts) == 2
    contract = next(c for c in ir.explicit_api_contracts if c.path == "/upload")
    assert contract.integration_refs == ["google_drive"]
    assert len(contract.actions) == 2
    assert contract.actions[0].id == "generate_test_file"
    assert contract.actions[0].kind == "internal_processing"
    assert contract.actions[1].kind == "external_call"
    assert contract.actions[1].integration_ref == "google_drive"
    assert len(ir.capability_coverage) == 3
    assert [(e.status_code, e.code) for e in contract.errors] == [
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
    ]


def test_explicit_contract_moved_to_proposed_preserves_actions_and_errors() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Subirlo a Google Drive"],
        integrations=[
            {
                "id": "google_drive",
                "name": "Google Drive API",
            }
        ],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/upload",
                actions=[
                    {
                        "id": "upload_to_google_drive",
                        "kind": "external_call",
                        "description": "Subir archivo a Google Drive",
                        "required": True,
                        "integration_ref": "google_drive",
                        "source": "inferred",
                        "evidence": "",
                        "assumption": "acciรณn propuesta",
                    }
                ],
                errors=[
                    {
                        "status_code": 401,
                        "code": "unauthorized",
                        "description": "No autorizado",
                        "required": True,
                        "source": "inferred",
                        "evidence": "",
                        "assumption": "error propuesto",
                    }
                ],
                integration_refs=["google_drive"],
            )
        ],
        contratos_api_propuestos=[],
        capability_coverage=[
            {
                "capability": "Subirlo a Google Drive",
                "contract_refs": ["POST /upload"],
                "action_refs": ["POST /upload#upload_to_google_drive"],
                "integration_refs": ["google_drive"],
                "status": "covered",
                "source": "explicit",
                "evidence": "subirlo a Google Drive",
                "assumption": "",
            }
        ],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert ir.explicit_api_contracts == []
    assert len(ir.proposed_api_contracts) == 1
    proposed = ir.proposed_api_contracts[0]
    assert proposed.actions
    assert proposed.actions[0].id == "upload_to_google_drive"
    assert proposed.actions[0].integration_ref == "google_drive"
    assert proposed.errors
    assert proposed.errors[0].code == "unauthorized"
    assert proposed.integration_refs == ["google_drive"]


def test_request_ir_to_dict_serializes_new_objects() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Subirlo a Google Drive"],
        technology_signals=[
            {
                "name": "Google Drive API",
                "category": "external_api",
                "role": "almacenamiento",
                "evidence": "Google Drive",
                "confidence": "explicit",
            }
        ],
        integrations=[
            {
                "id": "google_drive",
                "name": "Google Drive API",
                "kind": "external_api",
                "authentication": {"mechanism": "adc", "source": "explicit", "evidence": "ADC"},
                "technology_refs": ["Google Drive API"],
                "configuration_refs": ["DRIVE_FOLDER_ID"],
                "source": "explicit",
                "evidence": "subirlo a Google Drive",
            }
        ],
        configuration=[
            {
                "key": "DRIVE_FOLDER_ID",
                "purpose": "carpeta destino",
                "source": "explicit",
                "evidence": "DRIVE_FOLDER_ID",
            }
        ],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/upload",
                evidence="POST /upload",
                actions=[
                    {
                        "id": "upload_to_google_drive",
                        "kind": "external_call",
                        "description": "Subir archivo a Google Drive",
                        "integration_ref": "google_drive",
                        "source": "explicit",
                        "evidence": "subirlo a Google Drive",
                    }
                ],
                errors=[
                    {
                        "status_code": 401,
                        "code": "unauthorized",
                        "description": "No autorizado",
                        "source": "explicit",
                        "evidence": "401",
                    }
                ],
                integration_refs=["google_drive"],
            )
        ],
        capability_coverage=[
            {
                "capability": "Subirlo a Google Drive",
                "contract_refs": ["POST /upload"],
                "action_refs": ["POST /upload#upload_to_google_drive"],
                "integration_refs": ["google_drive"],
                "status": "covered",
                "source": "explicit",
                "evidence": "subirlo a Google Drive",
                "assumption": "",
            }
        ],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    payload = request_ir_to_dict(ir)

    assert payload["integrations"][0]["id"] == "google_drive"
    assert payload["integrations"][0]["authentication"]["mechanism"] == "adc"
    assert payload["configuration"][0]["key"] == "DRIVE_FOLDER_ID"
    assert payload["explicit_api_contracts"][0]["actions"][0]["id"] == "upload_to_google_drive"
    assert payload["explicit_api_contracts"][0]["errors"][0]["code"] == "unauthorized"
    assert payload["explicit_api_contracts"][0]["integration_refs"] == ["google_drive"]


def test_validate_request_ir_detects_missing_integration_ref_target() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Subirlo a Google Drive"],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/upload",
                evidence="POST /upload",
                actions=[
                    {
                        "id": "upload_to_google_drive",
                        "kind": "external_call",
                        "description": "Subir archivo a Google Drive",
                        "integration_ref": "missing_integration",
                        "source": "explicit",
                        "evidence": "subirlo a Google Drive",
                    }
                ],
                integration_refs=["missing_integration"],
            )
        ],
        capability_coverage=[
            {
                "capability": "Subirlo a Google Drive",
                "contract_refs": ["POST /upload"],
                "action_refs": ["POST /upload#upload_to_google_drive"],
                "integration_refs": ["missing_integration"],
                "status": "covered",
                "source": "explicit",
                "evidence": "subirlo a Google Drive",
                "assumption": "",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="missing_integration",
    ):
        build_request_ir_from_context(ctx, descripcion_global="x")


def test_legacy_context_without_integrations_objects_still_builds_request_ir() -> None:
    ctx = _mk_ctx_base(
        integraciones_externas=["Google Drive", "SendGrid"],
        contratos_api=[_contract("GET", "/health", evidence="GET /health")],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")

    assert ir.external_integrations == ["Google Drive", "SendGrid"]
    assert ir.integrations == []
    assert ir.explicit_api_contracts
    assert validate_request_ir(ir) == []


def test_validation_requires_evidence_for_explicit_structured_objects() -> None:
    ir = RequestIR(
        product_name="PoC",
        objective="objetivo",
        explicit_api_contracts=[
            ApiContractIR(
                method="POST",
                path="/upload",
                evidence="POST /upload",
                actions=[],
                errors=[],
                integration_refs=[],
            )
        ],
        integrations=[
            # type: ignore[arg-type]
            pytest.importorskip("dataclasses") and None
        ],  # placeholder overwritten below
    )
    # reconstrucciรณn explรญcita para evitar imports adicionales y mantener test legible
    ir = build_request_ir_from_context(
        _mk_ctx_base(
            integrations=[
                {
                    "id": "google_drive",
                    "name": "Google Drive API",
                    "source": "explicit",
                    "evidence": "",
                }
            ],
            configuration=[
                {
                    "key": "DRIVE_FOLDER_ID",
                    "purpose": "carpeta destino",
                    "source": "explicit",
                    "evidence": "",
                }
            ],
            contratos_api_explicitos=[
                _contract(
                    "POST",
                    "/upload",
                    evidence="POST /upload",
                    actions=[
                        {
                            "id": "upload_to_google_drive",
                            "kind": "external_call",
                            "description": "Subir archivo a Google Drive",
                            "integration_ref": "google_drive",
                            "source": "explicit",
                            "evidence": "",
                        }
                    ],
                    errors=[
                        {
                            "status_code": 401,
                            "code": "unauthorized",
                            "description": "No autorizado",
                            "source": "explicit",
                            "evidence": "",
                        }
                    ],
                )
            ],
        ),
        descripcion_global="x",
    )

    errors = validate_request_ir(ir)
    assert any("integration.source=explicit requiere evidence" in err for err in errors)
    assert any("configuration.source=explicit requiere evidence" in err for err in errors)
    assert any("action.source=explicit requiere evidence" in err for err in errors)
    assert any("error.source=explicit requiere evidence" in err for err in errors)


def test_capability_coverage_requires_explicit_presence_when_capabilities_exist() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Enviar una notificaciรณn mediante un proveedor externo"],
        contratos_api_explicitos=[_contract("POST", "/notifications", evidence="POST /notifications")],
    )

    with pytest.raises(
        ValueError,
        match="Existen capacidades funcionales, pero el normalizador no produjo capability_coverage.",
    ):
        build_request_ir_from_context(ctx, descripcion_global="x")


def test_capability_coverage_generic_external_integration_is_valid() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Enviar una notificaciรณn mediante un proveedor externo"],
        integrations=[
            {
                "id": "notification_provider",
                "name": "Notification Provider",
            }
        ],
        contratos_api_explicitos=[
            _contract(
                "POST",
                "/notifications",
                evidence="POST /notifications",
                actions=[
                    {
                        "id": "send_notification",
                        "kind": "external_call",
                        "description": "Enviar una notificaciรณn mediante un proveedor externo",
                        "integration_ref": "notification_provider",
                    }
                ],
                integration_refs=["notification_provider"],
            )
        ],
        capability_coverage=[
            {
                "capability": "Enviar una notificaciรณn mediante un proveedor externo",
                "contract_refs": ["POST /notifications"],
                "action_refs": ["POST /notifications#send_notification"],
                "integration_refs": ["notification_provider"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Enviar una notificaciรณn mediante un proveedor externo",
                "assumption": "",
            }
        ],
    )

    ir = build_request_ir_from_context(ctx, descripcion_global="x")
    assert validate_request_ir(ir) == []


def test_capability_coverage_internal_processing_with_upload_verb_is_not_external() -> None:
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
    assert validate_request_ir(ir) == []


def test_capability_coverage_reconciles_unambiguous_external_action() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Guardar un documento en almacenamiento externo"],
        integrations=[
            {
                "id": "document_storage",
                "name": "Document Storage",
            }
        ],
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


def test_capability_coverage_ambiguous_external_actions_fail_validation() -> None:
    ctx = _mk_ctx_base(
        funcionalidades_clave=["Publicar un evento en un broker"],
        integrations=[
            {
                "id": "event_broker",
                "name": "Event Broker",
            }
        ],
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
        match="Publicar un evento en un broker",
    ):
        build_request_ir_from_context(ctx, descripcion_global="x")


def _prompt_spec() -> dict:
    return {
        "entrypoint": "app.main:app",
        "dependencies": ["google-api-python-client", "google-auth"],
        "technology_signals": [
            {
                "name": "google_drive_sdk",
                "packages": ["google-api-python-client", "google-auth"],
                "import_roots": ["googleapiclient", "google.oauth2"],
            }
        ],
        "integrations": [
            {
                "id": "google_drive",
                "authentication": "ADC",
                "configuration_refs": ["DRIVE_FOLDER_ID"],
            }
        ],
        "configuration": [
            {
                "key": "DRIVE_FOLDER_ID",
                "required": True,
                "secret": False,
                "delivery": "env",
            }
        ],
        "implementation_files": [
            {
                "path": "app/integrations/google_drive.py",
                "kind": "integration",
                "integration_ref": "google_drive",
                "generated_by": "file_planner",
            }
        ],
        "endpoints": [
            {
                "method": "POST",
                "path": "/upload",
                "file": "app/api/endpoints/upload.py",
                "func": "upload_file",
                "actions": [
                    {"id": "generate_test_file", "required": True},
                    {
                        "id": "upload_to_drive",
                        "required": True,
                        "integration_ref": "google_drive",
                    },
                ],
                "errors": [
                    {"status_code": 401, "code": "unauthorized"},
                    {"status_code": 403, "code": "forbidden"},
                    {"status_code": 404, "code": "not_found"},
                ],
                "integration_refs": ["google_drive"],
            },
            {
                "method": "GET",
                "path": "/health",
                "file": "app/api/endpoints/health.py",
                "func": "health",
                "actions": [],
                "errors": [],
                "integration_refs": [],
            },
        ],
        "files": [
            "app/api/endpoints/upload.py",
            "app/api/endpoints/health.py",
            "app/integrations/google_drive.py",
            "app/core/config.py",
            "requirements.txt",
        ],
    }


def _endpoint_contract_prompt() -> dict:
    return {
        "path": "app/api/endpoints/upload.py",
        "kind": "endpoint",
        "required_symbols": ["router", "upload_file"],
        "must_implement": [
            "Validate the declared request",
            "Execute all required actions",
            "Build the declared response",
        ],
        "must_not": [
            "Do not return a hard-coded success response",
            "Do not open external connections at import time",
        ],
        "implementation_plan": [
            "Parse multipart payload",
            "Invoke internal integration module",
        ],
        "actions": [
            {"id": "generate_test_file", "required": True},
            {
                "id": "upload_to_drive",
                "required": True,
                "integration_ref": "google_drive",
            },
        ],
        "errors": [
            {"status_code": 401, "code": "unauthorized"},
            {"status_code": 403, "code": "forbidden"},
            {"status_code": 404, "code": "not_found"},
        ],
        "integration_refs": ["google_drive"],
        "external_dependencies": [
            {
                "id": "google_drive",
                "name": "Google Drive",
                "configuration_refs": ["DRIVE_FOLDER_ID"],
                "authentication": "ADC",
            }
        ],
        "implementation_levels": ["integration_skeleton"],
        "configuration": [{"key": "DRIVE_FOLDER_ID", "delivery": "env"}],
        "responsibilities": ["implementar handlers"],
    }


def _integration_contract_prompt() -> dict:
    return {
        "path": "app/integrations/google_drive.py",
        "kind": "integration",
        "required_symbols": ["build_client"],
        "must_implement": [
            "Build the integration client lazily",
            "Apply the declared authentication flow",
        ],
        "must_not": [
            "Do not store credentials",
            "Do not convert provider exceptions directly into FastAPI responses",
        ],
        "implementation_plan": [
            "Build ADC-authenticated client",
            "Expose callable upload operation",
        ],
        "actions": [
            {
                "id": "upload_to_drive",
                "required": True,
                "integration_ref": "google_drive",
            }
        ],
        "errors": [],
        "integration_refs": ["google_drive"],
        "external_dependencies": [
            {
                "id": "google_drive",
                "name": "Google Drive",
                "configuration_refs": ["DRIVE_FOLDER_ID"],
                "authentication": "ADC",
            }
        ],
        "implementation_levels": ["integration_skeleton"],
        "configuration": [{"key": "DRIVE_FOLDER_ID", "delivery": "env"}],
        "responsibilities": ["encapsular integración"],
        "source": {"from_user": ["foo"]},
        "evidence": ["bar"],
        "notes": ["baz"],
    }


def test_file_contract_prompt_is_compact_and_preserves_contract_rules() -> None:
    related_contracts = []
    for idx in range(8):
        contract = _integration_contract_prompt().copy()
        contract["path"] = f"app/integrations/service_{idx}.py"
        contract["required_symbols"] = [f"build_client_{idx}"]
        related_contracts.append(contract)

    prompt = build_prompt_file_contract(
        spec=_prompt_spec(),
        file_contract=_integration_contract_prompt(),
        related_contracts=related_contracts,
        descripcion_global="Upload files to external storage",
        contexto_normalizado={"objetivo_tecnico": "Integración externa"},
    )

    assert len(prompt) < 25000
    assert "CONTRATOS RELACIONADOS (RESUMEN INTER-ARCHIVO; SOLO CONTEXTO)" in prompt
    assert "Construye el cliente externamente de forma lazy." in prompt
    assert '"path": "app/integrations/google_drive.py"' in prompt

    related_section = prompt.split(
        "CONTRATOS RELACIONADOS (RESUMEN INTER-ARCHIVO; SOLO CONTEXTO)",
        1,
    )[1].split("SPEC (RESUMEN; REFERENCIA SECUNDARIA)", 1)[0]

    assert '"source"' not in related_section
    assert '"evidence"' not in related_section


def test_file_contract_fix_prompt_preserves_required_guarantees() -> None:
    prompt = build_prompt_file_contract_fix(
        spec=_prompt_spec(),
        file_contract=_endpoint_contract_prompt(),
        previous_content="def broken():\n    pass\n",
        errors=["missing required actions"],
        related_contracts=[_integration_contract_prompt()],
    )
    normalized_prompt = " ".join(prompt.split())

    assert "No elimines obligaciones ya implementadas para resolver un error aislado." in normalized_prompt
    assert "No conviertas una integración real en pass, TODO, fake o NotImplementedError." in normalized_prompt
    assert "Mantén todas las acciones required=true." in normalized_prompt
    assert "Execute all required actions" in prompt
    assert "DRIVE_FOLDER_ID" in prompt
    assert "TODO" in prompt


def test_related_contract_summary_keeps_only_interface_fields() -> None:
    summary = _summarize_related_contract(_integration_contract_prompt())

    assert summary["path"] == "app/integrations/google_drive.py"
    assert summary["kind"] == "integration"
    assert "provided_interfaces" in summary
    assert "required_internal_calls" in summary
    assert "configuration" in summary
    assert "source" not in summary
    assert "evidence" not in summary
    assert "notes" not in summary
