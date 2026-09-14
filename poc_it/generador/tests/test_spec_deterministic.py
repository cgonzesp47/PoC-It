from __future__ import annotations

from copy import deepcopy

from poc_it.generador.request_ir import build_request_ir_from_context
from poc_it.generador.spec_builder import build_spec_from_request_ir

# PoC-it siempre añade un TechnologySignal baseline (pydantic-settings) para
# app/core/config.py, independientemente de lo que el usuario haya declarado. Los tests de este
# módulo verifican trazabilidad de señales *detectadas del usuario*, así que filtramos esa señal
# baseline antes de comparar (ver poc_it.generador.spec_builder._baseline_technology_signals).
_BASELINE_TECHNOLOGY_SIGNAL_IDS = {"fastapi", "uvicorn", "pydantic", "pydantic-settings"}


def _user_technology_signals(spec: dict) -> list[dict]:
    return [
        item
        for item in spec.get("technology_signals", [])
        if str(item.get("id") or "") not in _BASELINE_TECHNOLOGY_SIGNAL_IDS
    ]


def test_build_request_ir_minimo_when_no_contracts() -> None:
    ir = build_request_ir_from_context({}, descripcion_global="x")
    assert ir.explicit_api_contracts == []
    assert ir.proposed_api_contracts == []
    assert ir.persistence.required is False


def test_spec_health_default_when_no_endpoints() -> None:
    ir = build_request_ir_from_context({}, descripcion_global="x")
    spec = build_spec_from_request_ir(ir)
    assert spec["schema_version"] == "pocit.spec.v1"
    assert spec["status"] == "draft"
    assert _user_technology_signals(spec) == []
    assert any(item["id"] == "pydantic-settings" for item in spec["technology_signals"])
    assert spec["integrations"] == []
    assert spec["configuration"] == []
    assert spec["open_questions"] == []
    eps = spec["endpoints"]
    assert len(eps) == 1
    assert eps[0]["method"] == "GET"
    assert eps[0]["path"] == "/health"
    assert eps[0]["file"] == "app/api/endpoints/health.py"
    assert eps[0]["source"]["type"] == "builder_default"
    assert eps[0]["actions"] == []
    assert eps[0]["integration_refs"] == []
    assert spec["source"]["from_user"] == []
    assert any("endpoint por defecto" in x for x in spec["source"]["from_assumptions"])


def test_explicit_endpoint_post_products_file_and_response_default() -> None:
    ir = build_request_ir_from_context(
        {
            "contratos_api": [
                {
                    "method": "POST",
                    "path": "/products",
                    "request": {"type": "json", "schema_hint": {}, "evidence": "POST /products", "assumption": ""},
                    "response": {"json_example": {}, "evidence": "devuelve producto"},
                }
            ],
            "persistence": {"required": True, "kind": "unknown", "evidence": ["guardar productos"]},
            "actores_principales": ["admin"],
        },
        descripcion_global="x",
    )
    spec = build_spec_from_request_ir(ir)
    ep = spec["endpoints"][0]
    assert ep["file"] == "app/api/endpoints/products.py"
    assert ep["source"]["type"] == "explicit"
    assert "POST /products" in ep["source"]["evidence"]
    assert ep["response"]["json_example"] == {"ok": True}
    assert any("Response example vacío" in a for a in spec["assumptions"])
    su = spec["source"]["from_user"]
    assert any("POST /products" in x for x in su)
    assert "guardar productos" in su
    assert any("Actor:" in x for x in su)


def test_proposed_endpoint_marks_source_and_assumption() -> None:
    ir = build_request_ir_from_context(
        {
            "contratos_api": [
                {
                    "method": "GET",
                    "path": "/items",
                    "request": {"type": "none", "schema_hint": {}, "evidence": "", "assumption": ""},
                    "response": {"json_example": {"ok": True}, "evidence": ""},
                }
            ]
        },
        descripcion_global="x",
    )
    spec = build_spec_from_request_ir(ir)
    ep = spec["endpoints"][0]
    assert ep["source"]["type"] in ("explicit", "proposed")
    if ep["source"]["type"] == "proposed":
        assert any("contratos propuestos" in a for a in spec["assumptions"])


def test_persistence_required_false_has_no_env_and_no_db_files() -> None:
    ir = build_request_ir_from_context(
        {
            "persistence": {"required": False},
            "contratos_api": [
                {
                    "method": "GET",
                    "path": "/health",
                    "request": {"type": "none", "schema_hint": {}, "evidence": "x", "assumption": ""},
                    "response": {"json_example": {"ok": True}, "evidence": "x"},
                }
            ],
        },
        descripcion_global="x",
    )
    spec = build_spec_from_request_ir(ir)
    assert spec["persistence"]["required"] is False
    assert spec["env"] == []
    assert all("db" not in f.lower() for f in spec["files"])


def test_persistence_required_true_unknown_kind_adds_assumption_and_test_strategy() -> None:
    ir = build_request_ir_from_context(
        {
            "persistence": {"required": True, "kind": "unknown", "durable_state": True, "business_entities": ["orders"], "evidence": ["guardar pedidos"]},
            "contratos_api": [
                {
                    "method": "GET",
                    "path": "/health",
                    "request": {"type": "none", "schema_hint": {}, "evidence": "x", "assumption": ""},
                    "response": {"json_example": {"ok": True}, "evidence": "x"},
                }
            ],
        },
        descripcion_global="x",
    )
    spec = build_spec_from_request_ir(ir)
    assert spec["persistence"]["required"] is True
    assert spec["persistence"]["kind"] == "unknown"
    assert any("kind='unknown'" in a for a in spec["assumptions"])
    assert spec["test_strategy"]["requires_dependency_overrides"] is True


def test_bundle_files_are_in_files_and_files_deduped() -> None:
    ir = build_request_ir_from_context(
        {
            "contratos_api": [
                {
                    "method": "GET",
                    "path": "/health",
                    "request": {"type": "none", "schema_hint": {}, "evidence": "x", "assumption": ""},
                    "response": {"json_example": {"ok": True}, "evidence": "x"},
                }
            ]
        },
        descripcion_global="x",
    )
    spec = build_spec_from_request_ir(ir)
    files = spec["files"]
    assert len(files) == len(set(files))
    for ep in spec["endpoints"]:
        for bf in ep["bundle_files"]:
            assert bf in files


def test_explicit_information_is_not_lost_from_request_ir_to_spec() -> None:
    ir = build_request_ir_from_context(
        {
            "technology_signals": [
                {
                    "name": "google-api-python-client",
                    "category": "external_api",
                    "role": "drive client",
                    "evidence": "google-api-python-client",
                    "confidence": "explicit",
                },
                {
                    "name": "google-auth",
                    "category": "auth",
                    "role": "ADC auth",
                    "evidence": "google-auth",
                    "confidence": "explicit",
                },
            ],
            "integraciones_externas": ["Google Drive"],
            "integrations": [
                {
                    "id": "google_drive",
                    "name": "Google Drive API",
                    "kind": "external_api",
                    "role": "subida de archivos",
                    "required": True,
                    "implementation_level": "integration_skeleton",
                    "authentication": {
                        "mechanism": "ADC",
                        "source": "explicit",
                        "evidence": "ADC",
                        "assumption": "",
                    },
                    "technology_refs": ["google-api-python-client", "google-auth"],
                    "configuration_refs": ["DRIVE_FOLDER_ID"],
                    "source": "explicit",
                    "evidence": "subirlo a Google Drive",
                    "assumption": "",
                }
            ],
            "configuration": [
                {
                    "key": "DRIVE_FOLDER_ID",
                    "purpose": "carpeta destino",
                    "required": True,
                    "secret": False,
                    "source": "inferred",
                    "evidence": "",
                    "assumption": "Hace falta una carpeta destino",
                }
            ],
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
                    "response": {"json_example": {"ok": True}, "evidence": "devuelve resultado"},
                    "actions": [
                        {
                            "id": "generate_test_file",
                            "kind": "internal_processing",
                            "description": "Generar fichero temporal de prueba",
                            "required": True,
                            "integration_ref": None,
                            "source": "inferred",
                            "evidence": "",
                            "assumption": "acción previa necesaria",
                        },
                        {
                            "id": "upload_to_drive",
                            "kind": "external_call",
                            "description": "Subir archivo a Google Drive",
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
                    "integration_refs": ["google_drive"],
                }
            ],
            "assumptions": ["usar una carpeta configurada"],
            "persistence": {"required": False},
        },
        descripcion_global="x",
    )

    spec = build_spec_from_request_ir(ir)

    assert [item["name"] for item in _user_technology_signals(spec)] == [
        "google-api-python-client",
        "google-auth",
    ]
    assert spec["integrations"][0]["id"] == "google_drive"
    assert spec["integrations"][0]["authentication"]["mechanism"] == "ADC"
    assert spec["configuration"][0]["key"] == "DRIVE_FOLDER_ID"
    assert spec["external_integrations"] == ["Google Drive"]

    endpoint = spec["endpoints"][0]
    assert endpoint["method"] == "POST"
    assert endpoint["path"] == "/upload"
    assert endpoint["integration_refs"] == ["google_drive"]
    assert [action["id"] for action in endpoint["actions"]] == [
        "generate_test_file",
        "upload_to_drive",
    ]
    assert endpoint["actions"][1]["integration_ref"] == "google_drive"

    got_errors = {(item["status_code"], item["code"], item["source"]) for item in endpoint["errors"]}
    assert (401, "unauthorized", "explicit") in got_errors
    assert (403, "forbidden", "explicit") in got_errors
    assert (404, "not_found", "explicit") in got_errors
    assert len([item for item in endpoint["errors"] if item["status_code"] == 404 and item["code"] == "not_found"]) == 1

    from_user = spec["source"]["from_user"]
    assert "subirlo a Google Drive" in from_user
    assert "ADC" in from_user
    assert "google-api-python-client" in from_user
    assert "google-auth" in from_user
    assert "401" in from_user
    assert "403" in from_user
    assert "404" in from_user

    from_assumptions = spec["source"]["from_assumptions"]
    assert any("carpeta" in item for item in from_assumptions)


def test_defaults_crud_404_still_work_and_are_marked_as_default() -> None:
    ir = build_request_ir_from_context(
        {
            "contratos_api_propuestos": [
                {
                    "method": "GET",
                    "path": "/productos/{id}",
                    "request": {"type": "query", "schema_hint": {}, "evidence": "", "assumption": "crud propuesto"},
                    "response": {"json_example": {"ok": True}, "evidence": ""},
                }
            ],
            "persistence": {"required": False},
        },
        descripcion_global="x",
    )
    spec = build_spec_from_request_ir(ir)
    endpoint = spec["endpoints"][0]
    assert endpoint["request"]["type"] == "none"
    assert endpoint["errors"] == [
        {
            "status_code": 404,
            "code": "not_found",
            "description": "",
            "required": False,
            "source": "default",
            "evidence": "",
            "assumption": "Default técnico del SPEC builder",
        }
    ]


def test_same_request_ir_produces_same_spec_dictionary() -> None:
    ir = build_request_ir_from_context(
        {
            "technology_signals": [
                {
                    "name": "google-auth",
                    "category": "auth",
                    "role": "ADC auth",
                    "evidence": "google-auth",
                    "confidence": "explicit",
                }
            ],
            "integrations": [
                {
                    "id": "google_drive",
                    "name": "Google Drive API",
                    "kind": "external_api",
                    "source": "explicit",
                    "evidence": "Google Drive",
                }
            ],
            "configuration": [
                {
                    "key": "DRIVE_FOLDER_ID",
                    "purpose": "carpeta destino",
                    "required": True,
                    "secret": False,
                    "source": "inferred",
                    "evidence": "",
                    "assumption": "cfg",
                }
            ],
            "contratos_api_explicitos": [
                {
                    "method": "POST",
                    "path": "/upload",
                    "request": {"type": "multipart", "schema_hint": {}, "evidence": "POST /upload", "assumption": ""},
                    "response": {"json_example": {"ok": True}, "evidence": "ok"},
                    "actions": [
                        {
                            "id": "upload_to_drive",
                            "kind": "external_call",
                            "description": "Subir archivo",
                            "required": True,
                            "integration_ref": "google_drive",
                            "source": "explicit",
                            "evidence": "Google Drive",
                            "assumption": "",
                        }
                    ],
                    "errors": [
                        {
                            "status_code": 401,
                            "code": "unauthorized",
                            "description": "No autorizado",
                            "required": True,
                            "source": "explicit",
                            "evidence": "401",
                            "assumption": "",
                        }
                    ],
                    "integration_refs": ["google_drive"],
                }
            ],
            "open_questions": ["¿Cómo validar el token?"],
            "persistence": {"required": False},
        },
        descripcion_global="x",
    )

    spec_1 = build_spec_from_request_ir(ir)
    spec_2 = build_spec_from_request_ir(deepcopy(ir))
    assert spec_1 == spec_2


def test_legacy_request_ir_still_generates_valid_spec() -> None:
    ir = build_request_ir_from_context(
        {
            "integraciones_externas": ["Google Drive", "SendGrid"],
            "contratos_api": [
                {
                    "method": "GET",
                    "path": "/health",
                    "request": {"type": "none", "schema_hint": {}, "evidence": "GET /health", "assumption": ""},
                    "response": {"json_example": {"ok": True}, "evidence": "ok"},
                }
            ],
            "persistence": {"required": False},
        },
        descripcion_global="x",
    )

    spec = build_spec_from_request_ir(ir)
    assert spec["schema_version"] == "pocit.spec.v1"
    assert spec["integrations"] == []
    assert spec["configuration"] == []
    assert spec["external_integrations"] == ["Google Drive", "SendGrid"]
    assert spec["endpoints"][0]["method"] == "GET"
    assert spec["endpoints"][0]["path"] == "/health"


def test_dependencies_are_merged_from_technology_signal_packages_only() -> None:
    ir = build_request_ir_from_context(
        {
            "technology_signals": [
                {
                    "name": "Google Drive API",
                    "package": "google-api-python-client",
                    "category": "external_api",
                    "role": "drive client",
                    "evidence": "Google Drive API",
                    "confidence": "explicit",
                },
                {
                    "name": "google-auth",
                    "package": "google-auth",
                    "category": "auth",
                    "role": "ADC auth",
                    "evidence": "google-auth",
                    "confidence": "explicit",
                },
                {
                    "name": "Cloud Run",
                    "package": "",
                    "category": "runtime",
                    "role": "deployment target",
                    "evidence": "Cloud Run",
                    "confidence": "explicit",
                },
            ],
            "persistence": {"required": False},
        },
        descripcion_global="x",
    )

    spec = build_spec_from_request_ir(ir)

    assert "fastapi" in spec["dependencies"]
    assert "uvicorn" in spec["dependencies"]
    assert "google-api-python-client" in spec["dependencies"]
    assert "google-auth" in spec["dependencies"]
    assert "Google Drive API" not in spec["dependencies"]
    assert "Cloud Run" not in spec["dependencies"]


def test_dependencies_are_deduped_case_insensitive_and_can_include_integration_packages() -> None:
    ir = build_request_ir_from_context(
        {
            "technology_signals": [
                {
                    "name": "google-auth",
                    "package": "google-auth",
                    "category": "auth",
                    "role": "auth",
                    "evidence": "google-auth",
                    "confidence": "explicit",
                },
                {
                    "name": "Google Auth Duplicate",
                    "package": "Google-Auth",
                    "category": "auth",
                    "role": "auth duplicate",
                    "evidence": "Google-Auth",
                    "confidence": "explicit",
                },
            ],
            "integrations": [
                {
                    "id": "drive",
                    "name": "Google Drive API",
                    "kind": "external_api",
                    "packages": ["google-api-python-client", "GOOGLE-AUTH"],
                    "source": "explicit",
                    "evidence": "Drive",
                }
            ],
            "persistence": {"required": False},
        },
        descripcion_global="x",
    )

    spec = build_spec_from_request_ir(ir)
    lowered = [item.lower() for item in spec["dependencies"]]

    assert lowered.count("google-auth") == 1
    assert lowered.count("google-api-python-client") == 1


def test_env_is_projected_from_configuration_preserving_secret_required_and_without_values() -> None:
    ir = build_request_ir_from_context(
        {
            "configuration": [
                {
                    "key": "DRIVE_FOLDER_ID",
                    "purpose": "carpeta destino",
                    "required": True,
                    "secret": False,
                    "source": "explicit",
                    "evidence": "DRIVE_FOLDER_ID",
                    "assumption": "",
                },
                {
                    "key": "GOOGLE_APPLICATION_CREDENTIALS_JSON",
                    "purpose": "credenciales ADC",
                    "required": True,
                    "secret": True,
                    "source": "inferred",
                    "evidence": "",
                    "assumption": "La integración necesita credenciales",
                },
            ],
            "persistence": {"required": False},
        },
        descripcion_global="x",
    )

    spec = build_spec_from_request_ir(ir)

    assert spec["env"] == [
        {
            "name": "DRIVE_FOLDER_ID",
            "purpose": "carpeta destino",
            "required": True,
            "secret": False,
            "source": "explicit",
            "evidence": "DRIVE_FOLDER_ID",
            "assumption": "",
        },
        {
            "name": "GOOGLE_APPLICATION_CREDENTIALS_JSON",
            "purpose": "credenciales ADC",
            "required": True,
            "secret": True,
            "source": "inferred",
            "evidence": "",
            "assumption": "La integración necesita credenciales",
        },
    ]
    assert all("value" not in item for item in spec["env"])


def test_env_ignores_non_env_delivery_and_empty_configuration() -> None:
    ir = build_request_ir_from_context(
        {
            "configuration": [
                {
                    "key": "README_CONFIG_PATH",
                    "purpose": "ruta en documentación",
                    "required": False,
                    "secret": False,
                    "source": "inferred",
                    "evidence": "",
                    "assumption": "solo documental",
                    "delivery": "file",
                }
            ],
            "persistence": {"required": False},
        },
        descripcion_global="x",
    )
    spec = build_spec_from_request_ir(ir)
    assert spec["env"] == []

    ir_empty = build_request_ir_from_context(
        {
            "persistence": {"required": False},
        },
        descripcion_global="x",
    )
    spec_empty = build_spec_from_request_ir(ir_empty)
    assert spec_empty["env"] == []
