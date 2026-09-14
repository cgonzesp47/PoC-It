from __future__ import annotations

from copy import deepcopy

from poc_it.generador.file_contracts import (
    build_file_contracts_from_spec,
    file_contracts_to_dict,
)
from poc_it.generador.file_contracts_validation import (
    validate_file_contracts,
    validate_generated_python_against_file_contracts,
)
from poc_it.generador.file_planner import enrich_spec_files_for_implementation
from poc_it.generador.implementation_contracts import (
    build_implementation_contracts_from_spec,
)
from poc_it.generador.request_ir import build_request_ir_from_context
from poc_it.generador.spec_builder import build_spec_from_request_ir
from poc_it.materializacion.poc_facts_extractor import extract_poc_facts_from_structure


def _context() -> dict:
    return {
        "nombre": "PoC Google Drive",
        "objetivo_tecnico": "Subir un archivo temporal a Google Drive desde FastAPI",
        "funcionalidades_clave": [],
        "integraciones_externas": [],
        "restricciones_tecnicas": [],
        "technology_signals": [
            {
                "name": "google-api-python-client",
                "package": "google-api-python-client",
                "category": "external_api",
                "role": "google drive",
                "evidence": "Google Drive client",
                "confidence": "explicit",
            },
            {
                "name": "google-auth",
                "package": "google-auth",
                "category": "external_api",
                "role": "authentication",
                "evidence": "ADC",
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
                "technology_refs": [
                    "google-api-python-client",
                    "google-auth",
                ],
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
                "delivery": "env",
                "source": "explicit",
                "evidence": "DRIVE_FOLDER_ID",
                "assumption": "",
            }
        ],
        "contratos_api": [],
        "contratos_api_propuestos": [],
        "contratos_api_explicitos": [
            {
                "method": "POST",
                "path": "/upload",
                "request": {
                    "type": "none",
                    "schema_hint": {},
                    "evidence": "POST /upload",
                },
                "response": {"json_example": {"ok": True}},
                "actions": [
                    {
                        "id": "generate_test_file",
                        "kind": "internal_processing",
                        "description": "Generate test file",
                        "required": True,
                        "source": "explicit",
                        "evidence": "generar archivo de prueba",
                        "assumption": "",
                    },
                    {
                        "id": "upload_to_drive",
                        "kind": "external_call",
                        "description": "Upload file to Google Drive",
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
            },
            {
                "method": "GET",
                "path": "/health",
                "request": {"type": "none", "schema_hint": {}, "evidence": "GET /health"},
                "response": {"json_example": {"status": "ok"}},
                "actions": [],
                "errors": [],
                "integration_refs": [],
            },
        ],
    }


def _build_flow() -> tuple[dict, list[dict], list[dict]]:
    req_ir = build_request_ir_from_context(_context(), descripcion_global="Upload files")
    spec = build_spec_from_request_ir(req_ir)
    implementation_contracts = build_implementation_contracts_from_spec(spec)
    planned = enrich_spec_files_for_implementation(
        spec,
        implementation_contracts=implementation_contracts,
    )
    file_contracts = build_file_contracts_from_spec(
        planned.spec,
        implementation_contracts=implementation_contracts,
    )
    return planned.spec, implementation_contracts, file_contracts_to_dict(file_contracts)


def _find_contract(file_contracts: list[dict], path: str) -> dict:
    for contract in file_contracts:
        if contract.get("path") == path:
            return contract
    raise AssertionError(f"missing contract for {path}")


def _error_codes(errors) -> set[str]:
    return {item.code for item in errors}


def test_external_integration_flow_preserves_traceability_without_validation_errors() -> None:
    spec, implementation_contracts, file_contracts = _build_flow()

    integration_path = "app/integrations/google_drive.py"
    endpoint_path = "app/api/endpoints/upload.py"

    assert integration_path in spec["files"]

    endpoint_contract = _find_contract(file_contracts, endpoint_path)
    integration_contract = _find_contract(file_contracts, integration_path)
    config_contract = _find_contract(file_contracts, "app/core/config.py")
    requirements_contract = _find_contract(file_contracts, "requirements.txt")

    assert endpoint_contract["integration_refs"] == ["google_drive"]
    assert any(action["id"] == "upload_to_drive" for action in endpoint_contract["actions"])
    assert {(item["status_code"], item["code"]) for item in endpoint_contract["errors"]} == {
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
    }

    assert integration_contract["integration_refs"] == ["google_drive"]
    assert "integration_skeleton" in integration_contract["implementation_levels"]
    assert (
        integration_contract["external_dependencies"][0]["authentication"]["mechanism"]
        == "ADC"
    )
    assert any(item["key"] == "DRIVE_FOLDER_ID" for item in integration_contract["configuration"])

    assert any(item["key"] == "DRIVE_FOLDER_ID" for item in config_contract["configuration"])
    assert "google-api-python-client" in requirements_contract["dependencies"]
    assert "google-auth" in requirements_contract["dependencies"]

    errors = validate_file_contracts(
        spec=spec,
        implementation_contracts=implementation_contracts,
        file_contracts=file_contracts,
    )
    assert errors == []


def test_integration_and_endpoint_contracts_require_injectable_client_provider() -> None:
    """Regresión real: la función de acción de la integración (`upload_to_google_drive`)
    construía su propio cliente por dentro (import directo a `build_client`), lo que hacía
    imposible mockear la llamada externa en tests herméticos. El contrato debe exigir que el
    cliente se construya en una función proveedora separada (compatible con `Depends(...)`) y
    que la acción lo reciba como parámetro — SIN dejar de invocarse como llamada directa (eso
    es lo que evita repetir el conflicto con `required_internal_calls` de la vez anterior)."""
    spec, implementation_contracts, file_contracts = _build_flow()

    integration_contract = _find_contract(file_contracts, "app/integrations/google_drive.py")
    endpoint_contract = _find_contract(file_contracts, "app/api/endpoints/upload.py")

    assert any(
        "provider function" in item.lower() and "zero-required-argument" in item.lower()
        for item in integration_contract["must_implement"]
    )
    assert any(
        "receive the client as an explicit parameter" in item.lower()
        for item in integration_contract["must_implement"]
    )
    assert any(
        "depends(build_client)" in item.lower() and "still direct" in item.lower()
        for item in endpoint_contract["must_implement"]
    )
    # El nombre debe ser el mismo, EXACTO, a ambos lados del contrato (integración y endpoint) —
    # esa coordinación es justo lo que faltaba y causó la regresión real.
    assert any("build_client" in item for item in integration_contract["must_implement"])
    # La obligación de invocar la integración se mantiene intacta (no se sustituye, se
    # complementa) — es justo lo que preserva la compatibilidad con required_internal_calls.
    assert "Invoke the referenced service or integration" in endpoint_contract["must_implement"]

    errors = validate_file_contracts(
        spec=spec,
        implementation_contracts=implementation_contracts,
        file_contracts=file_contracts,
    )
    assert errors == []


def test_di_client_provider_pattern_is_compatible_with_required_internal_calls_and_facts_extractor() -> None:
    """Verificación empírica end-to-end del patrón exigido por el contrato: el cliente se
    inyecta vía Depends() en el endpoint y se pasa como argumento extra a la MISMA llamada
    directa que `required_internal_calls` ya validaba. Comprueba, contra código real que sigue
    exactamente ese patrón:
    1) que `validate_generated_python_against_file_contracts` no reporta ninguna violación
       (en particular ninguna de signature/data-flow), y
    2) que `poc_facts_extractor` reconoce el cliente como una dependencia inyectada y
       overrideable (lo que promociona el endpoint a HERMETIC_HTTP en test_plan_builder)."""
    file_contracts = [
        {
            "path": "app/api/endpoints/upload.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.google_drive",
                    "symbol": "upload_to_google_drive",
                    "action_ref": "upload_to_google_drive",
                    "required": True,
                    "interface_ref": "app.integrations.google_drive:upload_to_google_drive",
                    "parameters": [{"name": "filename", "type": "string", "required": True}],
                    "arguments_ref": "",
                }
            ],
        },
        {
            "path": "app/integrations/google_drive.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "upload_to_google_drive",
                    "kind": "function",
                    "action_ref": "upload_to_google_drive",
                    "parameters": [{"name": "filename", "type": "string", "required": True}],
                    "returns": None,
                    "returns_ref": "",
                    "return_hint": None,
                    "purpose": "",
                    "interface_ref": "app.integrations.google_drive:upload_to_google_drive",
                    "interface_required": True,
                    "owner_path": "app/integrations/google_drive.py",
                    "consumer_path": "app/api/endpoints/upload.py",
                }
            ],
        },
    ]

    files_by_path = {
        "app/api/endpoints/upload.py": (
            "from fastapi import APIRouter, Depends\n"
            "from app.integrations.google_drive import upload_to_google_drive, build_client\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/upload')\n"
            "async def create_upload(request: dict, client=Depends(build_client)):\n"
            "    file_id = upload_to_google_drive(request['filename'], client=client)\n"
            "    return {'status': 'success', 'file_id': file_id}\n"
        ),
        "app/integrations/google_drive.py": (
            "from functools import lru_cache\n\n"
            "@lru_cache\n"
            "def build_client():\n"
            "    return object()\n\n"
            "def upload_to_google_drive(filename: str, client) -> str:\n"
            "    result = client.files().create(body={'name': filename}).execute()\n"
            "    return result.get('id')\n"
        ),
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )
    assert violations == []

    facts = extract_poc_facts_from_structure(files_by_path)
    endpoint = next(ep for ep in facts.endpoints if ep.path == "/upload")

    assert endpoint.depends == ["build_client"]
    assert endpoint.depends_imports == ["app.integrations.google_drive.build_client"]
    assert endpoint.uses_injected is True


def test_validation_detects_lost_required_action() -> None:
    spec, implementation_contracts, file_contracts = _build_flow()

    for contract in file_contracts:
        contract["actions"] = [
            item for item in (contract.get("actions") or []) if item.get("id") != "upload_to_drive"
        ]

    errors = validate_file_contracts(
        spec=spec,
        implementation_contracts=implementation_contracts,
        file_contracts=file_contracts,
    )
    assert "FILE_REQUIRED_ACTION_LOST" in _error_codes(errors)


def test_validation_detects_missing_integration_owner() -> None:
    spec, implementation_contracts, file_contracts = _build_flow()

    spec = deepcopy(spec)
    spec["files"] = [path for path in spec["files"] if path != "app/integrations/google_drive.py"]
    spec["implementation_files"] = [
        item for item in (spec.get("implementation_files") or []) if item.get("path") != "app/integrations/google_drive.py"
    ]
    file_contracts = [
        item for item in file_contracts if item.get("path") != "app/integrations/google_drive.py"
    ]

    errors = validate_file_contracts(
        spec=spec,
        implementation_contracts=implementation_contracts,
        file_contracts=file_contracts,
    )
    assert "FILE_INTEGRATION_OWNER_MISSING" in _error_codes(errors)


def test_validation_detects_lost_configuration() -> None:
    spec, implementation_contracts, file_contracts = _build_flow()

    config_contract = _find_contract(file_contracts, "app/core/config.py")
    config_contract["configuration"] = []

    errors = validate_file_contracts(
        spec=spec,
        implementation_contracts=implementation_contracts,
        file_contracts=file_contracts,
    )
    assert "FILE_CONFIGURATION_LOST" in _error_codes(errors)


def test_validation_detects_lost_dependency() -> None:
    spec, implementation_contracts, file_contracts = _build_flow()

    requirements_contract = _find_contract(file_contracts, "requirements.txt")
    requirements_contract["dependencies"] = [
        item for item in requirements_contract["dependencies"] if item != "google-auth"
    ]

    errors = validate_file_contracts(
        spec=spec,
        implementation_contracts=implementation_contracts,
        file_contracts=file_contracts,
    )
    assert "FILE_DEPENDENCY_LOST" in _error_codes(errors)


def test_validation_detects_external_dependency_in_health() -> None:
    spec, implementation_contracts, file_contracts = _build_flow()

    health_contract = _find_contract(file_contracts, "app/api/endpoints/health.py")
    health_contract["integration_refs"] = ["google_drive"]

    errors = validate_file_contracts(
        spec=spec,
        implementation_contracts=implementation_contracts,
        file_contracts=file_contracts,
    )
    assert "FILE_HEALTH_EXTERNAL_DEPENDENCY" in _error_codes(errors)


def test_validation_detects_lost_dependency_for_tuple_packages() -> None:
    spec, implementation_contracts, file_contracts = _build_flow()

    spec = deepcopy(spec)
    spec["technology_signals"] = [
        {
            "id": "client_sdk",
            "name": "Client SDK",
            "packages": ("client-package",),
        }
    ]
    spec["integrations"] = [
        {
            "id": "external_service",
            "technology_refs": ["client_sdk"],
        }
    ]

    requirements_contract = _find_contract(file_contracts, "requirements.txt")
    requirements_contract["dependencies"] = [
        item for item in requirements_contract["dependencies"] if item != "client-package"
    ]

    errors = validate_file_contracts(
        spec=spec,
        implementation_contracts=implementation_contracts,
        file_contracts=file_contracts,
    )

    assert "FILE_DEPENDENCY_LOST" in _error_codes(errors)
