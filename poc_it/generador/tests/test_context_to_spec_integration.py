from __future__ import annotations

from typing import Any, Dict

from poc_it.generador.request_ir import build_request_ir_from_context
from poc_it.generador.spec_builder import build_spec_from_request_ir


def _contract(
    method: str,
    path: str,
    *,
    evidence: str = "",
    assumption: str = "",
    actions: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
    integration_refs: list[str] | None = None,
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


def test_context_with_proposed_contract_and_persistence_builds_spec_with_endpoint_and_persistence() -> None:
    contexto_normalizado: Dict[str, Any] = {
        "nombre": "PoC",
        "objetivo_tecnico": "crud productos",
        "funcionalidades_clave": [],
        "integraciones_externas": [],
        "restricciones_tecnicas": [],
        "technology_signals": [],
        "assumptions": [],
        "evidence": [],
        "contratos_api": [],
        "contratos_api_explicitos": [],
        "contratos_api_propuestos": [
            _contract("POST", "/productos", assumption="crud propuesto"),
            _contract("GET", "/productos", assumption="crud propuesto"),
            _contract("GET", "/productos/{id}", assumption="crud propuesto"),
            _contract("PUT", "/productos/{id}", assumption="crud propuesto"),
            _contract("PATCH", "/productos/{id}", assumption="crud propuesto"),
            _contract("DELETE", "/productos/{id}", assumption="crud propuesto"),
        ],
        "persistence": {
            "required": True,
            "kind": "relational",
            "durable_state": True,
            "business_entities": ["productos"],
            "evidence": ["Persistir productos en BD"],
            "uncertainty": "",
        },
        "state_requirements": {"durable": True, "entities": ["productos"], "evidence": ["Guardar productos"]},
        "domain_entities": [
            {
                "name": "Producto",
                "singular": "producto",
                "plural": "productos",
                "slug": "productos",
                "evidence": "gestionar productos",
                "confidence": "explicit",
            }
        ],
        "operation_groups": [
            {"type": "crud", "entity": "productos", "evidence": "CRUD", "confidence": "explicit"}
        ],
    }

    req_ir = build_request_ir_from_context(contexto_normalizado, descripcion_global="x")
    assert req_ir.proposed_api_contracts
    assert req_ir.persistence.required is True

    spec = build_spec_from_request_ir(req_ir)
    endpoints = spec.get("endpoints") or []
    assert any((e.get("method") == "POST" and e.get("path") == "/productos") for e in endpoints)

    persistence = spec.get("persistence") or {}
    assert persistence.get("required") is True
    assert spec["technology_signals"] == []
    assert spec["integrations"] == []
    assert spec["configuration"] == []


def test_crud_operation_group_should_generate_five_endpoints_list_included_and_id_endpoints_have_404_and_no_query_request() -> None:
    contexto_normalizado: Dict[str, Any] = {
        "nombre": "PoC",
        "objetivo_tecnico": "crud productos",
        "funcionalidades_clave": [],
        "integraciones_externas": [],
        "restricciones_tecnicas": [],
        "technology_signals": [],
        "assumptions": [],
        "evidence": [],
        "contratos_api_explicitos": [],
        "contratos_api_propuestos": [],
        "persistence": {"required": True, "kind": "relational", "durable_state": True, "business_entities": ["productos"], "evidence": ["db"], "uncertainty": ""},
        "domain_entities": [{"name": "Producto", "singular": "producto", "plural": "productos", "slug": "productos", "evidence": "gestionar productos", "confidence": "explicit"}],
        "operation_groups": [{"type": "crud", "entity": "productos", "evidence": "CRUD", "confidence": "explicit"}],
    }

    from poc_it.analisis.normalizador_contexto import _propose_api_contracts_from_crud_if_evidenced

    _propose_api_contracts_from_crud_if_evidenced(contexto_normalizado)
    proposed = contexto_normalizado.get("contratos_api_propuestos") or []
    assert len(proposed) == 6

    req_ir = build_request_ir_from_context(contexto_normalizado, descripcion_global="x")
    spec = build_spec_from_request_ir(req_ir)
    endpoints = spec.get("endpoints") or []

    expected = {
        ("POST", "/productos"),
        ("GET", "/productos"),
        ("GET", "/productos/{id}"),
        ("PUT", "/productos/{id}"),
        ("PATCH", "/productos/{id}"),
        ("DELETE", "/productos/{id}"),
    }
    got = {(e.get("method"), e.get("path")) for e in endpoints}
    assert expected.issubset(got)

    ep_by_key = {(e.get("method"), e.get("path")): e for e in endpoints}

    assert (ep_by_key[("GET", "/productos/{id}")].get("request") or {}).get("type") == "none"
    assert (ep_by_key[("DELETE", "/productos/{id}")].get("request") or {}).get("type") == "none"

    for m in ("GET", "PUT", "PATCH", "DELETE"):
        errors = ep_by_key[(m, "/productos/{id}")].get("errors") or []
        assert any(e.get("status_code") == 404 for e in errors)
        assert any(e.get("source") == "default" for e in errors if e.get("status_code") == 404)

    assert ep_by_key[("POST", "/productos")]["func"] == "create_producto"
    assert ep_by_key[("GET", "/productos")]["func"] == "list_productos"
    assert ep_by_key[("GET", "/productos/{id}")]["func"] == "get_producto"
    assert ep_by_key[("PUT", "/productos/{id}")]["func"] == "replace_producto"
    assert ep_by_key[("PATCH", "/productos/{id}")]["func"] == "update_producto"
    assert ep_by_key[("DELETE", "/productos/{id}")]["func"] == "delete_producto"


def test_structured_integrations_configuration_actions_and_errors_are_preserved_in_spec() -> None:
    contexto_normalizado: Dict[str, Any] = {
        "nombre": "PoC",
        "objetivo_tecnico": "subir archivos",
        "funcionalidades_clave": [],
        "integraciones_externas": ["Google Drive"],
        "restricciones_tecnicas": [],
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
        "assumptions": [],
        "evidence": [],
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
            _contract(
                "POST",
                "/upload",
                evidence="POST /upload",
                actions=[
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
        "contratos_api_propuestos": [],
        "open_questions": ["¿Cómo validar el token?"],
        "persistence": {"required": False},
    }

    req_ir = build_request_ir_from_context(contexto_normalizado, descripcion_global="x")
    assert req_ir.open_questions == ["¿Cómo validar el token?"]
    spec = build_spec_from_request_ir(req_ir)

    assert [item["name"] for item in spec["technology_signals"]] == [
        "google-api-python-client",
        "google-auth",
    ]
    assert spec["integrations"][0]["id"] == "google_drive"
    assert spec["configuration"][0]["key"] == "DRIVE_FOLDER_ID"
    assert spec["external_integrations"] == ["Google Drive"]
    assert spec["open_questions"] == ["¿Cómo validar el token?"]

    endpoint = spec["endpoints"][0]
    assert endpoint["path"] == "/upload"
    assert endpoint["integration_refs"] == ["google_drive"]
    assert [action["id"] for action in endpoint["actions"]] == [
        "generate_test_file",
        "upload_to_drive",
    ]
    assert endpoint["actions"][1]["integration_ref"] == "google_drive"

    error_keys = [(item["status_code"], item["code"], item["source"]) for item in endpoint["errors"]]
    assert (401, "unauthorized", "explicit") in error_keys
    assert (403, "forbidden", "explicit") in error_keys
    assert (404, "not_found", "explicit") in error_keys
    assert len([item for item in endpoint["errors"] if item["status_code"] == 404 and item["code"] == "not_found"]) == 1
