from __future__ import annotations

import copy

import pytest

from poc_it.generador.request_ir import build_request_ir_from_context
from poc_it.generador.spec_builder import build_spec_from_request_ir
from poc_it.generador.spec_traceability import validate_request_ir_to_spec_traceability
from poc_it.generador.spec_validation import repair_spec_deterministic, validate_spec


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


def _fixture_context_with_structured_semantics() -> dict:
    return {
        "objetivo_tecnico": "API de subida y verificación",
        "funcionalidades_clave": ["subir archivos", "verificar acceso"],
        "integraciones_externas": ["Google Drive API"],
        "restricciones_tecnicas": ["Usar FastAPI", "Configurar credenciales por variables de entorno"],
        "contratos_api": [],
        "contratos_api_explicitos": [
            {
                "method": "POST",
                "path": "/upload",
                "request": {"type": "multipart", "evidence": "subir archivo"},
                "response": {"json_example": {"file_id": "abc"}, "evidence": "devolver file_id"},
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
                "errors": [
                    {
                        "status_code": 401,
                        "code": "unauthorized",
                        "description": "Credenciales inválidas",
                        "required": True,
                        "source": "explicit",
                        "evidence": "401 si no está autorizado",
                    }
                ],
                "integration_refs": ["google_drive"],
            }
        ],
        "contratos_api_propuestos": [
            {
                "method": "GET",
                "path": "/health",
                "request": {
                    "type": "none",
                    "assumption": "Se propone endpoint de verificación de acceso.",
                },
                "response": {"json_example": {"ok": True}},
            }
        ],
        "capability_coverage": [
            {
                "capability": "subir archivos",
                "contract_refs": ["POST /upload"],
                "action_refs": ["POST /upload#upload_to_drive"],
                "integration_refs": ["google_drive"],
                "status": "covered",
                "source": "explicit",
                "evidence": "subirlo a Google Drive",
            },
            {
                "capability": "verificar acceso",
                "contract_refs": ["GET /health"],
                "status": "covered",
                "source": "inferred",
                "assumption": "Se propone endpoint de verificación.",
            },
        ],
        "technology_signals": [
            {
                "name": "FastAPI",
                "package": "fastapi",
                "category": "framework",
                "role": "api",
                "evidence": "Usar FastAPI",
                "confidence": "explicit",
            },
            {
                "name": "Google Drive API",
                "package": "google-api-python-client",
                "category": "integration",
                "role": "storage",
                "evidence": "subirlo a Google Drive",
                "confidence": "explicit",
            },
        ],
        "integrations": [
            {
                "id": "google_drive",
                "name": "Google Drive API",
                "kind": "external_api",
                "role": "almacenar archivos subidos",
                "required": True,
                "implementation_level": "integration_skeleton",
                "authentication": {
                    "mechanism": "service_account",
                    "source": "explicit",
                    "evidence": "usar service account",
                    "assumption": "",
                },
                "technology_refs": ["Google Drive API"],
                "configuration_refs": ["GOOGLE_DRIVE_FOLDER_ID", "GOOGLE_APPLICATION_CREDENTIALS"],
                "source": "explicit",
                "evidence": "subirlo a Google Drive",
                "assumption": "",
            }
        ],
        "configuration": [
            {
                "key": "GOOGLE_DRIVE_FOLDER_ID",
                "purpose": "Carpeta de destino",
                "required": True,
                "secret": False,
                "source": "explicit",
                "evidence": "usar folder id de Drive",
                "assumption": "",
                "delivery": "env",
            },
            {
                "key": "GOOGLE_APPLICATION_CREDENTIALS",
                "purpose": "Ruta a credenciales",
                "required": True,
                "secret": True,
                "source": "explicit",
                "evidence": "usar service account",
                "assumption": "",
                "delivery": "env",
            },
        ],
        "persistence": {"required": False},
        "modo_recomendado": "PARCIAL",
        "framework_objetivo": "fastapi",
    }


def _load_fixture_spec_ok() -> dict:
    req_ir = build_request_ir_from_context(_fixture_context_crud_productos(), descripcion_global="x")
    return build_spec_from_request_ir(req_ir)


def _load_structured_semantics_ir_and_spec():
    req_ir = build_request_ir_from_context(_fixture_context_with_structured_semantics(), descripcion_global="x")
    spec = build_spec_from_request_ir(req_ir)
    return req_ir, spec


def _errs(spec: dict) -> list[str]:
    return [f"{e.severity}:{e.code}:{e.path}" for e in validate_spec(spec)]


def test_spec_ok_crud_has_no_fatal_errors():
    spec = _load_fixture_spec_ok()
    errs = validate_spec(spec)
    assert not any(e.severity == "fatal" for e in errs), _errs(spec)


def test_missing_required_file_router_is_fatal_or_repairable():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)
    spec2["files"] = [p for p in spec2["files"] if p != "app/api/router.py"]
    errs = validate_spec(spec2)
    assert any(e.severity == "fatal" and e.code == "FILES_MISSING_REQUIRED" for e in errs)

    repaired, rep_errs = repair_spec_deterministic(spec2)
    assert "app/api/router.py" in repaired["files"]
    assert any(e.code == "REPAIR_ADD_REQUIRED_FILE" for e in rep_errs)


def test_endpoint_file_missing_in_files_is_fatal_or_repairable():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)
    ep_file = spec2["endpoints"][0]["file"]
    spec2["files"] = [p for p in spec2["files"] if p != ep_file]

    errs = validate_spec(spec2)
    assert any(e.severity == "fatal" and e.code == "FILES_ENDPOINT_FILE_MISSING" for e in errs)

    repaired, rep_errs = repair_spec_deterministic(spec2)
    assert ep_file in repaired["files"]
    assert any(e.code == "REPAIR_ADD_ENDPOINT_FILE" for e in rep_errs)


def test_duplicate_method_path_is_fatal():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)
    spec2["endpoints"].append(copy.deepcopy(spec2["endpoints"][0]))
    errs = validate_spec(spec2)
    assert any(e.severity == "fatal" and e.code == "ENDPOINT_DUPLICATE_METHOD_PATH" for e in errs)


def test_proposed_endpoint_without_assumption_is_fatal():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)
    spec2["endpoints"][0]["source"] = {"type": "proposed"}
    errs = validate_spec(spec2)
    assert any(e.severity == "fatal" and e.code == "ENDPOINT_PROPOSED_MISSING_ASSUMPTION" for e in errs)


def test_explicit_endpoint_without_evidence_is_fatal():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)
    spec2["endpoints"][0]["source"] = {"type": "explicit"}
    errs = validate_spec(spec2)
    assert any(e.severity == "fatal" and e.code == "ENDPOINT_EXPLICIT_MISSING_EVIDENCE" for e in errs)


def test_id_endpoint_query_request_is_fatal():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)

    id_idx = next((i for i, ep in enumerate(spec2["endpoints"]) if "{id}" in ep.get("path", "")), None)
    if id_idx is None:
        ep = copy.deepcopy(spec2["endpoints"][0])
        ep["method"] = "GET"
        ep["path"] = "/items/{id}"
        ep["func"] = "get_item"
        ep["file"] = "app/api/endpoints/items.py"
        ep["request"] = {"type": "query", "path_params": {"id": "1"}, "schema": {}}
        ep["response"] = {"json_example": {"id": "1"}}
        ep["source"] = {"type": "builder_default"}
        ep["errors"] = []
        ep["actions"] = []
        ep["integration_refs"] = []
        spec2["endpoints"].append(ep)
        spec2["files"].append(ep["file"])
        id_idx = len(spec2["endpoints"]) - 1

    spec2["endpoints"][id_idx]["request"]["type"] = "query"
    errs = validate_spec(spec2)
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


def test_persistence_required_true_override_targets_empty_is_warning():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)
    spec2["persistence"]["required"] = True
    spec2["persistence"]["kind"] = spec2["persistence"].get("kind") or "unknown"
    spec2["persistence"]["durable_state"] = True
    spec2["persistence"]["evidence"] = ["context says DB needed"]
    spec2["test_strategy"]["requires_dependency_overrides"] = True
    spec2["test_strategy"]["override_targets"] = []

    errs = validate_spec(spec2)
    assert any(e.severity == "warning" and e.code == "OVERRIDE_TARGETS_MISSING" for e in errs)


def test_get_list_with_ok_true_example_is_warning():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)

    idx = next(
        (i for i, ep in enumerate(spec2["endpoints"]) if ep.get("method") == "GET" and "{id}" not in ep.get("path", "")),
        None,
    )
    if idx is None:
        pytest.skip("No hay endpoint GET list en el spec de fixture")

    ep = spec2["endpoints"][idx]
    ep["response"]["json_example"] = {"ok": True}
    ep["source"] = {"type": "explicit", "evidence": "fixture forcing warning"}
    errs = validate_spec(spec2)
    assert any(e.severity == "warning" and e.code == "RESPONSE_EXAMPLE_WEAK" for e in errs)


def test_spec_validation_module_has_no_duplicate_public_helpers():
    import poc_it.generador.spec_validation as m

    expected = {
        "validate_spec",
        "validar_spec",
        "repair_spec_deterministic",
        "_extraer_json_tolerante",
        "normalizar_paths",
        "carpetas_de_codigo",
        "completar_inits_en_files",
        "persistir_spec_debug",
    }
    for name in expected:
        assert hasattr(m, name), f"missing {name}"


def test_repair_does_not_mutate_original_spec():
    spec = _load_fixture_spec_ok()
    spec_before = copy.deepcopy(spec)

    repaired, _ = repair_spec_deterministic(spec)
    assert spec == spec_before
    assert repaired == repaired


def test_global_source_invalid_is_fatal():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)
    spec2["source"] = "not-a-dict"
    errs = validate_spec(spec2)
    assert any(e.severity == "fatal" and e.code == "SOURCE_NOT_OBJECT" for e in errs)


def test_global_source_missing_from_user_is_warning():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)
    if not isinstance(spec2.get("source"), dict):
        spec2["source"] = {}
    spec2["source"].pop("from_user", None)

    errs = validate_spec(spec2)
    assert any(e.severity == "warning" and e.code == "SOURCE_LIST_MISSING" and e.path == "$.source.from_user" for e in errs)


def test_repair_adds_path_params_for_id_endpoint():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)

    id_idx = next((i for i, ep in enumerate(spec2["endpoints"]) if "{id}" in ep.get("path", "")), None)
    if id_idx is None:
        pytest.skip("No hay endpoint con {id} en el spec de fixture")

    spec2["endpoints"][id_idx]["request"].pop("path_params", None)
    repaired, rep_errs = repair_spec_deterministic(spec2)

    req = repaired["endpoints"][id_idx]["request"]
    assert isinstance(req.get("path_params"), dict)
    assert "id" in req["path_params"]
    assert any(e.code == "REPAIR_ADD_PATH_PARAMS" for e in rep_errs)


def test_id_endpoint_query_request_remains_fatal_after_repair():
    spec = _load_fixture_spec_ok()
    spec2 = copy.deepcopy(spec)

    id_idx = next((i for i, ep in enumerate(spec2["endpoints"]) if "{id}" in ep.get("path", "")), None)
    if id_idx is None:
        pytest.skip("No hay endpoint con {id} en el spec de fixture")

    spec2["endpoints"][id_idx]["request"]["type"] = "query"
    repaired, _ = repair_spec_deterministic(spec2)
    errs = validate_spec(repaired)
    assert any(e.severity == "fatal" and e.code == "REQUEST_ID_AS_QUERY" for e in errs)


def test_structured_semantics_spec_is_traceable_and_valid():
    req_ir, spec = _load_structured_semantics_ir_and_spec()

    spec_errors = validate_spec(spec)
    trace_errors = validate_request_ir_to_spec_traceability(req_ir, spec)

    assert not any(e.severity == "fatal" for e in spec_errors), [e.code for e in spec_errors]
    assert trace_errors == []


def test_mixed_explicit_and_proposed_endpoints_are_both_present_in_spec():
    req_ir, spec = _load_structured_semantics_ir_and_spec()

    endpoint_sources = {
        (ep["method"], ep["path"], ep["source"]["type"])
        for ep in spec["endpoints"]
    }

    assert ("POST", "/upload", "explicit") in endpoint_sources
    assert ("GET", "/health", "proposed") in endpoint_sources
    assert any("contratos propuestos" in assumption.lower() for assumption in spec["assumptions"])
    assert req_ir.capability_coverage


def test_missing_integration_is_fatal_and_breaks_traceability():
    req_ir, spec = _load_structured_semantics_ir_and_spec()
    spec2 = copy.deepcopy(spec)
    spec2["integrations"] = []

    spec_errors = validate_spec(spec2)
    trace_errors = validate_request_ir_to_spec_traceability(req_ir, spec2)

    assert any(e.code == "SPEC_ACTION_INTEGRATION_UNKNOWN" for e in spec_errors)
    assert any(e.code == "SPEC_INTEGRATION_REF_UNKNOWN" for e in spec_errors)
    assert any(e.code == "TRACE_INTEGRATION_LOST" for e in trace_errors)


def test_missing_configuration_env_is_fatal_and_breaks_traceability():
    req_ir, spec = _load_structured_semantics_ir_and_spec()
    spec2 = copy.deepcopy(spec)
    spec2["configuration"] = []
    spec2["env"] = []

    spec_errors = validate_spec(spec2)
    trace_errors = validate_request_ir_to_spec_traceability(req_ir, spec2)

    assert any(e.code == "SPEC_INTEGRATION_REF_UNKNOWN" for e in spec_errors)
    assert any(e.code == "TRACE_CONFIGURATION_LOST" for e in trace_errors)
    assert any(e.code == "TRACE_ENV_LOST" for e in trace_errors)


def test_action_external_call_without_integration_ref_is_fatal():
    _, spec = _load_structured_semantics_ir_and_spec()
    spec2 = copy.deepcopy(spec)
    spec2["endpoints"][0]["actions"][0]["integration_ref"] = None

    errs = validate_spec(spec2)
    assert any(e.code == "SPEC_EXTERNAL_ACTION_WITHOUT_INTEGRATION" for e in errs)


def test_explicit_action_without_evidence_is_fatal():
    _, spec = _load_structured_semantics_ir_and_spec()
    spec2 = copy.deepcopy(spec)
    spec2["endpoints"][0]["actions"][0]["evidence"] = ""

    errs = validate_spec(spec2)
    assert any(e.code == "SPEC_EXPLICIT_EVIDENCE_REQUIRED" and ".actions[" in e.path for e in errs)


def test_explicit_configuration_without_evidence_is_fatal():
    _, spec = _load_structured_semantics_ir_and_spec()
    spec2 = copy.deepcopy(spec)
    spec2["configuration"][0]["evidence"] = ""

    errs = validate_spec(spec2)
    assert any(e.code == "SPEC_EXPLICIT_EVIDENCE_REQUIRED" and ".configuration[" in e.path for e in errs)


def test_configuration_embedded_secret_value_is_fatal():
    _, spec = _load_structured_semantics_ir_and_spec()
    spec2 = copy.deepcopy(spec)
    spec2["configuration"][0]["value"] = "real-secret"

    errs = validate_spec(spec2)
    assert any(e.code == "SPEC_CONFIGURATION_EMBEDS_VALUE" for e in errs)


def test_invalid_implementation_level_is_fatal():
    _, spec = _load_structured_semantics_ir_and_spec()
    spec2 = copy.deepcopy(spec)
    spec2["integrations"][0]["implementation_level"] = "real_integration"

    errs = validate_spec(spec2)
    assert any(e.code == "SPEC_INTEGRATION_INVALID_LEVEL" for e in errs)
