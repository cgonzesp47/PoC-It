from pydantic import ValidationError

from poc_it.generador.request_ir import build_request_ir_from_context
from poc_it.modulos.models import ConfigurationItemModel, ContextoNormalizado


def _build_roundtrip_context() -> dict:
    return {
        "objetivo_tecnico": "Validar una integración externa",
        "actores_principales": ["Operador"],
        "funcionalidades_clave": [
            "Enviar un documento mediante POST /documents"
        ],
        "integraciones_externas": ["Document storage"],
        "restricciones_tecnicas": [],
        "requisitos_no_funcionales": [],
        "riesgos_inherentes": [],
        "complejidad_inferida": "MEDIA",
        "modo_recomendado": "PARCIAL",
        "contratos_api_explicitos": [
            {
                "method": "POST",
                "path": "/documents",
                "request": {
                    "type": "json",
                    "schema_hint": {},
                    "evidence": "POST /documents",
                    "assumption": "",
                },
                "response": {
                    "json_example": {},
                    "evidence": "POST /documents",
                },
                "notes": "",
                "actions": [
                    {
                        "id": "store_document",
                        "kind": "external_call",
                        "description": "Guardar documento en almacenamiento externo",
                        "required": True,
                        "integration_ref": "document_storage",
                        "source": "explicit",
                        "evidence": "almacenamiento externo",
                        "assumption": "",
                    }
                ],
                "errors": [],
                "integration_refs": ["document_storage"],
            }
        ],
        "contratos_api_propuestos": [],
        "contratos_api": [],
        "integrations": [
            {
                "id": "document_storage",
                "name": "Document storage",
                "kind": "object_storage",
                "role": "Guardar documentos",
                "required": True,
                "implementation_level": "integration_skeleton",
                "authentication": {
                    "mechanism": "runtime_identity",
                    "credential_source": "runtime",
                    "allows_embedded_secret": False,
                    "allows_static_credential_file": False,
                    "source": "explicit",
                    "evidence": "credenciales proporcionadas por el entorno de ejecución",
                    "assumption": "",
                },
                "technology_refs": [],
                "configuration_refs": [],
                "source": "explicit",
                "evidence": "almacenamiento externo",
                "assumption": "",
            }
        ],
        "configuration": [
            {
                "key": "STORAGE_FOLDER_ID",
                "purpose": "Identificador de la carpeta de destino",
                "required": True,
                "secret": False,
                "source": "explicit",
                "evidence": "carpeta de destino",
                "assumption": "",
                "delivery": "env",
            }
        ],
        "capability_coverage": [
            {
                "capability_id": "send_document",
                "capability": "Enviar un documento mediante POST /documents",
                "contract_refs": ["POST /documents"],
                "action_refs": ["POST /documents#store_document"],
                "integration_refs": ["document_storage"],
                "status": "covered",
                "source": "explicit",
                "evidence": "Enviar un documento",
                "assumption": "",
            }
        ],
        "open_questions": [
            "Confirmar la carpeta de destino"
        ],
        "persistence": {
            "required": False,
            "kind": None,
            "durable_state": False,
            "business_entities": [],
            "evidence": [],
            "uncertainty": "",
        },
        "technology_signals": [],
        "domain_entities": [],
        "operation_groups": [],
        "state_requirements": {
            "durable": False,
            "entities": [],
            "evidence": [],
        },
        "assumptions": [],
        "evidence": [],
    }


def _build_google_drive_context() -> dict:
    return {
        "objetivo_tecnico": "Subir un fichero a Google Drive y exponer health check",
        "actores_principales": ["Operador"],
        "funcionalidades_clave": [
            "Subir un fichero a Google Drive mediante POST /upload",
            "Verificar estado del servicio mediante GET /health",
        ],
        "integraciones_externas": ["Google Drive API"],
        "restricciones_tecnicas": [],
        "requisitos_no_funcionales": [],
        "riesgos_inherentes": [],
        "complejidad_inferida": "MEDIA",
        "modo_recomendado": "PARCIAL",
        "contratos_api_explicitos": [
            {
                "method": "POST",
                "path": "/upload",
                "request": {
                    "type": "multipart",
                    "schema_hint": {},
                    "evidence": "POST /upload",
                    "assumption": "",
                },
                "response": {
                    "json_example": {},
                    "evidence": "POST /upload",
                },
                "notes": "",
                "actions": [
                    {
                        "id": "generate_test_file",
                        "kind": "internal_processing",
                        "description": "Generar internamente un fichero de prueba",
                        "required": True,
                        "integration_ref": None,
                        "source": "explicit",
                        "evidence": "generar internamente un fichero de prueba",
                        "assumption": "",
                    },
                    {
                        "id": "upload_to_google_drive",
                        "kind": "external_call",
                        "description": "Subir el fichero a Google Drive",
                        "required": True,
                        "integration_ref": "google_drive_api",
                        "source": "explicit",
                        "evidence": "Google Drive",
                        "assumption": "",
                    },
                ],
                "errors": [],
                "integration_refs": ["google_drive_api"],
            },
            {
                "method": "GET",
                "path": "/health",
                "request": {
                    "type": "none",
                    "schema_hint": {},
                    "evidence": "GET /health",
                    "assumption": "",
                },
                "response": {
                    "json_example": {},
                    "evidence": "GET /health",
                },
                "notes": "",
                "actions": [],
                "errors": [],
                "integration_refs": [],
            },
        ],
        "contratos_api_propuestos": [],
        "contratos_api": [],
        "integrations": [
            {
                "id": "google_drive_api",
                "name": "Google Drive API",
                "kind": "object_storage",
                "role": "Subir el fichero de prueba",
                "required": True,
                "implementation_level": "integration_skeleton",
                "authentication": {
                    "mechanism": "adc",
                    "source": "explicit",
                    "evidence": "Application Default Credentials",
                },
                "technology_refs": [
                    "google-api-python-client",
                    "google-auth",
                ],
                "configuration_refs": [
                    "GOOGLE_DRIVE_FOLDER_ID"
                ],
                "source": "explicit",
                "evidence": "Google Drive",
                "assumption": "",
            }
        ],
        "configuration": [
            {
                "key": "GOOGLE_DRIVE_FOLDER_ID",
                "purpose": "ID de la carpeta de destino",
                "required": True,
                "secret": False,
                "source": "explicit",
                "evidence": "carpeta concreta de Google Drive",
                "assumption": "",
            }
        ],
        "capability_coverage": [
            {
                "capability_id": "upload_file",
                "capability": "Subir un fichero a Google Drive mediante POST /upload",
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
                "capability": "Verificar estado del servicio mediante GET /health",
                "contract_refs": ["GET /health"],
                "action_refs": [],
                "integration_refs": [],
                "status": "covered",
                "source": "explicit",
                "evidence": "GET /health",
                "assumption": "",
            },
        ],
        "open_questions": [],
        "persistence": {
            "required": False,
            "kind": None,
            "durable_state": False,
            "business_entities": [],
            "evidence": [],
            "uncertainty": "",
        },
        "technology_signals": [],
        "domain_entities": [],
        "operation_groups": [],
        "state_requirements": {
            "durable": False,
            "entities": [],
            "evidence": [],
        },
        "assumptions": [],
        "evidence": [],
    }


def test_contexto_normalizado_preserves_request_ir_fields():
    raw_context = _build_roundtrip_context()

    model = ContextoNormalizado(**raw_context)
    dumped = model.model_dump(mode="json")

    assert dumped["capability_coverage"] == raw_context["capability_coverage"]
    assert dumped["integrations"] == raw_context["integrations"]
    assert dumped["configuration"] == raw_context["configuration"]
    configuration = dumped["configuration"][0]
    assert configuration["delivery"] == raw_context["configuration"][0]["delivery"]
    assert dumped["open_questions"] == raw_context["open_questions"]

    auth = dumped["integrations"][0]["authentication"]
    expected_auth = raw_context["integrations"][0]["authentication"]

    assert auth["credential_source"] == expected_auth["credential_source"]
    assert auth["allows_embedded_secret"] is expected_auth["allows_embedded_secret"]
    assert (
        auth["allows_static_credential_file"]
        is expected_auth["allows_static_credential_file"]
    )


def test_contexto_normalizado_fields_reach_request_ir():
    raw_context = _build_roundtrip_context()

    normalized = ContextoNormalizado(**raw_context)
    serialized = normalized.model_dump(mode="json")

    ir = build_request_ir_from_context(
        serialized,
        raw_context["objetivo_tecnico"],
    )

    assert len(ir.capability_coverage) == 1
    assert ir.capability_coverage[0].capability_id == "send_document"
    assert len(ir.integrations) == 1
    assert ir.integrations[0].id == "document_storage"


def test_contexto_normalizado_rejects_unknown_fields():
    raw_context = _build_roundtrip_context()
    raw_context["unexpected_new_field"] = {"value": True}

    try:
        ContextoNormalizado(**raw_context)
    except ValidationError:
        return

    raise AssertionError("ContextoNormalizado debería rechazar campos desconocidos")


def test_configuration_model_uses_default_delivery():
    model = ConfigurationItemModel(
        key="RESOURCE_ID",
        purpose="Identificador del recurso",
    )

    dumped = model.model_dump(mode="json")

    assert dumped["delivery"] == "env"


def test_configuration_model_preserves_explicit_delivery():
    model = ConfigurationItemModel(
        key="RESOURCE_ID",
        purpose="Identificador del recurso",
        delivery="runtime",
    )

    dumped = model.model_dump(mode="json")

    assert dumped["delivery"] == "runtime"


def test_authentication_model_uses_structural_defaults():
    model = ContextoNormalizado(
        **{
            **_build_roundtrip_context(),
            "integrations": [
                {
                    **_build_roundtrip_context()["integrations"][0],
                    "authentication": {
                        "mechanism": "some_mechanism",
                        "source": "explicit",
                        "evidence": "evidence",
                    },
                }
            ],
        }
    )

    dumped = model.model_dump(mode="json")
    auth = dumped["integrations"][0]["authentication"]

    assert auth["credential_source"] == "unknown"
    assert auth["allows_embedded_secret"] is False
    assert auth["allows_static_credential_file"] is True
    assert auth["mechanism"] == "some_mechanism"


def test_google_drive_context_reaches_request_ir_without_losing_coverage():
    raw_context = _build_google_drive_context()

    normalized = ContextoNormalizado(**raw_context)
    serialized = normalized.model_dump(mode="json")

    assert len(serialized["capability_coverage"]) == 2

    ir = build_request_ir_from_context(
        serialized,
        raw_context["objetivo_tecnico"],
    )

    assert len(ir.capability_coverage) == 2
    assert any(
        integration.id == "google_drive_api"
        for integration in ir.integrations
    )
