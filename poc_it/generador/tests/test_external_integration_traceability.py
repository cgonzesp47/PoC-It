from __future__ import annotations

import copy

from poc_it.generador.request_ir import build_request_ir_from_context, validate_request_ir
from poc_it.generador.spec_builder import build_spec_from_request_ir
from poc_it.generador.spec_traceability import validate_request_ir_to_spec_traceability
from poc_it.generador.spec_validation import validate_spec


def _fixture_context_external_integration() -> dict:
    return {
        "nombre_proyecto": "Drive verification PoC",
        "objetivo_tecnico": "Subir un fichero de prueba a Google Drive mediante ADC",
        "funcionalidades_clave": [
            "Generar internamente un fichero de prueba",
            "Subirlo a Google Drive",
            "Exponer un health check local",
        ],
        "integraciones_externas": ["Google Drive API"],
        "technology_signals": [
            {
                "name": "Google API Python Client",
                "packages": ["google-api-python-client"],
                "import_roots": ["googleapiclient"],
                "category": "external_api",
                "role": "Cliente oficial de Google Drive",
                "evidence": "google-api-python-client",
                "confidence": "explicit",
            },
            {
                "name": "Google Auth",
                "packages": ["google-auth"],
                "import_roots": ["google.auth"],
                "category": "auth",
                "role": "ADC",
                "evidence": "google-auth",
                "confidence": "explicit",
            },
        ],
        "configuration": [
            {
                "key": "DRIVE_FOLDER_ID",
                "purpose": "Carpeta de destino",
                "required": True,
                "secret": False,
                "delivery": "env",
                "source": "inferred",
                "evidence": "",
                "assumption": "La operación necesita identificar la carpeta de destino",
            }
        ],
        "integrations": [
            {
                "id": "google_drive",
                "name": "Google Drive API",
                "kind": "external_api",
                "role": "Subir el fichero generado",
                "required": True,
                "implementation_level": "integration_skeleton",
                "authentication": {
                    "mechanism": "Service Account with ADC",
                    "credential_source": "runtime",
                    "allows_embedded_secret": False,
                    "allows_static_credential_file": False,
                    "source": "explicit",
                    "evidence": "usar ADC con Service Account",
                    "assumption": "",
                },
                "technology_refs": [
                    "google-api-python-client",
                    "google-auth",
                ],
                "configuration_refs": ["DRIVE_FOLDER_ID"],
                "source": "explicit",
                "evidence": "subir un fichero de prueba a Google Drive",
                "assumption": "",
            }
        ],
        "contratos_api_explicitos": [
            {
                "method": "POST",
                "path": "/upload",
                "request": {
                    "type": "json",
                    "schema_hint": {
                        "filename": "string",
                    },
                    "evidence": "POST /upload recibe únicamente el nombre",
                },
                "response": {
                    "json_example": {
                        "status": "success",
                    },
                    "evidence": "confirmar la subida",
                },
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
                        "id": "upload_to_drive",
                        "kind": "external_call",
                        "description": "Subir el fichero generado a Google Drive",
                        "required": True,
                        "integration_ref": "google_drive",
                        "source": "explicit",
                        "evidence": "subirlo a Google Drive",
                        "assumption": "",
                    },
                ],
                "errors": [
                    {
                        "status_code": 401,
                        "code": "authentication_error",
                        "description": "Credenciales inválidas",
                        "required": True,
                        "source": "explicit",
                        "evidence": "gestionar errores 401",
                        "assumption": "",
                    },
                    {
                        "status_code": 403,
                        "code": "permission_denied",
                        "description": "Permisos insuficientes",
                        "required": True,
                        "source": "explicit",
                        "evidence": "gestionar errores 403",
                        "assumption": "",
                    },
                    {
                        "status_code": 404,
                        "code": "target_not_found",
                        "description": "Carpeta no encontrada",
                        "required": True,
                        "source": "explicit",
                        "evidence": "gestionar errores 404",
                        "assumption": "",
                    },
                ],
                "integration_refs": ["google_drive"],
                "notes": "No recibe multipart",
            },
            {
                "method": "GET",
                "path": "/health",
                "request": {
                    "type": "none",
                    "schema_hint": {},
                    "evidence": "GET /health",
                },
                "response": {
                    "json_example": {
                        "status": "ok",
                    },
                    "evidence": "devuelve status ok",
                },
                "actions": [],
                "errors": [],
                "integration_refs": [],
                "notes": "No depende de servicios externos",
            },
        ],
        "contratos_api_propuestos": [],
        "capability_coverage": [
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
                "action_refs": ["POST /upload#upload_to_drive"],
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
        "persistence": {
            "required": False,
            "kind": None,
            "durable_state": False,
            "business_entities": [],
            "evidence": [],
            "uncertainty": "",
        },
    }


def test_external_integration_context_reaches_spec_validation_and_traceability() -> None:
    ir = build_request_ir_from_context(_fixture_context_external_integration(), descripcion_global="x")

    assert validate_request_ir(ir) == []

    spec = build_spec_from_request_ir(ir)

    assert "google-api-python-client" in spec["dependencies"]
    assert "google-auth" in spec["dependencies"]
    assert any(
        item["id"] == "google_drive" and item["implementation_level"] == "integration_skeleton"
        for item in spec["integrations"]
    )
    assert any(item["name"] == "DRIVE_FOLDER_ID" for item in spec["env"])

    upload = next(endpoint for endpoint in spec["endpoints"] if endpoint["method"] == "POST" and endpoint["path"] == "/upload")
    assert {action["id"] for action in upload["actions"]} == {
        "generate_test_file",
        "upload_to_drive",
    }
    assert {error["status_code"] for error in upload["errors"]} >= {401, 403, 404}
    assert upload["integration_refs"] == ["google_drive"]

    health = next(endpoint for endpoint in spec["endpoints"] if endpoint["method"] == "GET" and endpoint["path"] == "/health")
    assert health["integration_refs"] == []
    assert health["actions"] == []

    assert validate_spec(spec) == []
    assert validate_request_ir_to_spec_traceability(ir, spec) == []


def test_traceability_detects_lost_upload_actions() -> None:
    ir = build_request_ir_from_context(_fixture_context_external_integration(), descripcion_global="x")
    spec = build_spec_from_request_ir(ir)
    broken = copy.deepcopy(spec)

    upload = next(endpoint for endpoint in broken["endpoints"] if endpoint["method"] == "POST" and endpoint["path"] == "/upload")
    upload["actions"] = []

    errors = validate_request_ir_to_spec_traceability(ir, broken)
    assert any(e.code == "TRACE_ACTION_LOST" for e in errors)


def test_traceability_detects_lost_dependency() -> None:
    ir = build_request_ir_from_context(_fixture_context_external_integration(), descripcion_global="x")
    spec = build_spec_from_request_ir(ir)
    broken = copy.deepcopy(spec)
    broken["dependencies"] = [item for item in broken["dependencies"] if item != "google-auth"]

    errors = validate_request_ir_to_spec_traceability(ir, broken)
    assert any(e.code == "TRACE_DEPENDENCY_LOST" for e in errors)


def test_spec_dependencies_preserve_explicit_and_referenced_packages() -> None:
    context = _fixture_context_external_integration()
    context["technology_signals"].append(
        {
            "name": "Unused Library",
            "packages": ["unused-package"],
            "import_roots": ["unused_library"],
            "category": "library",
            "role": "",
            "evidence": "unused-package",
            "confidence": "explicit",
        }
    )

    ir = build_request_ir_from_context(context, descripcion_global="x")
    spec = build_spec_from_request_ir(ir)

    assert "google-api-python-client" in spec["dependencies"]
    assert "google-auth" in spec["dependencies"]
    assert "unused-package" in spec["dependencies"]


def test_normalizer_open_questions_only_report_unresolved_or_ambiguous_refs() -> None:
    base = _fixture_context_external_integration()

    resolved = copy.deepcopy(base)
    resolved["_debug"] = {
        "technology_ref_reconciliation": [
            {
                "integration_id": "google_drive",
                "input_ref": "google-api-python-client",
                "canonical_id": "google_api_python_client",
                "status": "resolved",
            }
        ]
    }
    from poc_it.analisis.normalizador_contexto import _build_technology_ref_open_questions

    assert _build_technology_ref_open_questions(resolved["_debug"]["technology_ref_reconciliation"]) == []

    unresolved = copy.deepcopy(base)
    unresolved["_debug"] = {
        "technology_ref_reconciliation": [
            {
                "integration_id": "google_drive",
                "input_ref": "missing-package",
                "canonical_id": None,
                "status": "unresolved",
            }
        ]
    }
    unresolved_questions = _build_technology_ref_open_questions(
        unresolved["_debug"]["technology_ref_reconciliation"]
    )
    assert unresolved_questions == [
        "Integración google_drive referencia tecnología no resuelta: missing-package"
    ]

    ambiguous = copy.deepcopy(base)
    ambiguous["_debug"] = {
        "technology_ref_reconciliation": [
            {
                "integration_id": "google_drive",
                "input_ref": "shared-package",
                "canonical_id": None,
                "status": "ambiguous",
            }
        ]
    }
    ambiguous_questions = _build_technology_ref_open_questions(
        ambiguous["_debug"]["technology_ref_reconciliation"]
    )
    assert ambiguous_questions == [
        "Integración google_drive referencia alias tecnológico ambiguo: shared-package"
    ]
