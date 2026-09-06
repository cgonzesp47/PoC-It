from __future__ import annotations

from poc_it.generador.prompts_file_contracts import (
    _summarize_related_contract,
    build_prompt_file_contract,
    build_prompt_file_contract_fix,
)


def _spec() -> dict:
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


def _endpoint_contract() -> dict:
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


def _integration_contract() -> dict:
    return {
        "path": "app/integrations/google_drive.py",
        "kind": "integration",
        "required_symbols": ["build_client"],
        "configuration_access": {
            "provider_module": "app.core.config",
            "provider_symbol": "get_settings",
            "allowed_fields": ["RESOURCE_ID"],
            "access_mode": "lazy",
        },
        "authentication_constraints": {
            "credential_source": "runtime",
            "allows_embedded_secret": False,
            "allows_static_credential_file": False,
        },
        "authentication_runtime_contract": {
            "discovery": "ambient",
            "requires_configuration_field": False,
            "requires_static_credential_file": False,
            "requires_embedded_secret": False,
        },
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


def test_enriched_endpoint_prompt_contains_contract_first_sections():
    prompt = build_prompt_file_contract(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        related_contracts=[_integration_contract()],
        descripcion_global="Upload files to Google Drive",
        contexto_normalizado={"objetivo_tecnico": "Subir archivos"},
    )

    assert "FILE CONTRACT (ACTUAL, FUENTE DE VERDAD)" in prompt
    assert "Execute all required actions" in prompt
    assert "Do not return a hard-coded success response" in prompt
    assert 'implementation_level="integration_skeleton"' in prompt
    assert "No devuelvas una respuesta de éxito si no se ejecutaron las acciones requeridas." in prompt
    assert "generate_test_file" in prompt
    assert "upload_to_drive" in prompt
    assert '"status_code": 401' in prompt
    assert '"status_code": 403' in prompt
    assert '"status_code": 404' in prompt
    assert "DRIVE_FOLDER_ID" in prompt
    assert "ADC" in prompt
    assert "No construyas directamente clientes externos si existe un File Contract de integración relacionado." in prompt
    assert "Invoca el módulo interno asignado." in prompt
    assert "Depends(build_client)" in prompt
    assert "la llamada sigue siendo directa" in prompt


def test_enriched_integration_prompt_contains_specific_rules():
    prompt = build_prompt_file_contract(
        spec=_spec(),
        file_contract=_integration_contract(),
        related_contracts=[
            {
                "path": "app/core/config.py",
                "kind": "config",
                "configuration": [{"key": "DRIVE_FOLDER_ID"}],
            },
            _endpoint_contract(),
            {"path": "requirements.txt", "kind": "requirements"},
        ],
        descripcion_global="Upload files to Google Drive",
        contexto_normalizado=None,
    )

    assert "Construye el cliente externamente de forma lazy." in prompt
    assert "Lee configuración mediante app.core.config." in prompt
    assert "Implementa una operación llamable por cada acción externa asignada." in prompt
    assert "No importes FastAPI salvo que el contrato lo permita expresamente." in prompt
    assert "No traduzcas excepciones a HTTPException." in prompt
    assert "DRIVE_FOLDER_ID" in prompt
    assert "ADC" in prompt
    assert "función proveedora" in prompt
    assert "reciba ese cliente como parámetro explícito" in prompt


def test_fix_prompt_preserves_same_guarantees():
    prompt = build_prompt_file_contract_fix(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        previous_content="def broken():\n    pass\n",
        errors=["missing required actions"],
        related_contracts=[_integration_contract()],
    )
    normalized_prompt = " ".join(prompt.split())

    assert "No elimines obligaciones ya implementadas para resolver un error aislado." in normalized_prompt
    assert "No conviertas una integración real en pass, TODO, fake o NotImplementedError." in normalized_prompt
    assert "Mantén todas las acciones required=true." in normalized_prompt
    assert "Corrige únicamente el archivo actual." in normalized_prompt
    assert "Execute all required actions" in prompt
    assert "Do not return a hard-coded success response" in prompt
    assert '"status_code": 401' in prompt
    assert "DRIVE_FOLDER_ID" in prompt
    assert "pass" in prompt
    assert "TODO" in prompt
    assert "fake" in prompt
    assert "NotImplementedError" in prompt


def test_fix_prompt_omits_previous_rejected_attempts_section_when_none_given():
    prompt = build_prompt_file_contract_fix(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        previous_content="def broken():\n    pass\n",
        errors=["missing required actions"],
        related_contracts=[_integration_contract()],
    )

    assert "INTENTOS ANTERIORES RECHAZADOS" not in prompt


def test_fix_prompt_warns_about_previous_rejected_attempts_and_their_new_errors():
    """Regresión: cada ronda de reparación regeneraba el mismo intento fallido porque el prompt
    solo contenía los errores ORIGINALES, sin decirle al LLM qué intentó antes y por qué se
    rechazó. Esto le hacía repetir literalmente el mismo error (p.ej. usar `google.auth.default()`
    sin `import google.auth`) en rondas sucesivas."""
    prompt = build_prompt_file_contract_fix(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        previous_content="def broken():\n    pass\n",
        errors=["missing required actions"],
        related_contracts=[_integration_contract()],
        previous_rejected_attempts=[
            {
                "content": "creds, _ = google.auth.default()",
                "issues": ["Python symbol 'google' is used but not defined or imported."],
            }
        ],
    )

    assert "INTENTOS ANTERIORES RECHAZADOS" in prompt
    assert "google.auth.default()" in prompt
    assert "Python symbol 'google' is used but not defined or imported." in prompt


def test_codegen_and_fix_prompts_warn_against_hardcoded_temp_paths() -> None:
    """Regresión: un proyecto real generó `open(f"/tmp/{filename}", "w")` (ruta Unix
    hardcodeada), que revienta en Windows con FileNotFoundError antes de llegar siquiera a usar
    la integración. Ambos prompts (codegen inicial y reparación) deben indicar el uso de
    `tempfile` en su lugar."""
    codegen_prompt = build_prompt_file_contract(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        related_contracts=[],
        descripcion_global="Servicio con fichero temporal",
        contexto_normalizado=None,
    )
    fix_prompt = build_prompt_file_contract_fix(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        previous_content="def broken():\n    pass\n",
        errors=["missing required actions"],
        related_contracts=[_integration_contract()],
    )

    for prompt in (codegen_prompt, fix_prompt):
        assert "tempfile" in prompt
        assert "/tmp/" in prompt


def test_codegen_and_fix_prompts_warn_against_dotted_access_without_submodule_import() -> None:
    """Regresión: el LLM generó repetidamente (4 veces, idéntico) `google.auth.default()` sin
    `import google.auth` (solo importaba `from google.auth.transport.requests import Request`,
    que no deja `google` disponible como nombre), causando UNRESOLVED_PYTHON_SYMBOL. Ambos
    prompts deben advertir de esta trampa genérica de Python (no específica de ningún SDK)."""
    codegen_prompt = build_prompt_file_contract(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        related_contracts=[],
        descripcion_global="Servicio con acceso a submódulo",
        contexto_normalizado=None,
    )
    fix_prompt = build_prompt_file_contract_fix(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        previous_content="def broken():\n    pass\n",
        errors=["missing required actions"],
        related_contracts=[_integration_contract()],
    )

    for prompt in (codegen_prompt, fix_prompt):
        assert "submódulo" in prompt
        assert "import paquete.submodulo" in prompt


def test_codegen_and_fix_prompts_warn_against_returning_whole_dict_as_scalar_field() -> None:
    """Regresión: `upload_to_google_drive() -> Dict[str, str]` devolvía `{'status': 'ok'}` (sin
    'file_id'), y el endpoint hacía `file_id = upload_to_google_drive(...)` seguido de
    `return {"status": "success", "file_id": file_id}` — asignando el DICT COMPLETO a un campo
    que debía ser un string. FastAPI reventaba con ResponseValidationError -> 500. Ambos prompts
    deben advertir de este patrón (mismo día que el bug de `test_file["filename"]` vs
    `test_file["file_path"]`: pérdida de coherencia entre funciones que el LLM escribe en la
    misma pasada)."""
    codegen_prompt = build_prompt_file_contract(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        related_contracts=[],
        descripcion_global="Servicio que compone datos entre funciones auxiliares",
        contexto_normalizado=None,
    )
    fix_prompt = build_prompt_file_contract_fix(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        previous_content="def broken():\n    pass\n",
        errors=["missing required actions"],
        related_contracts=[_integration_contract()],
    )

    for prompt in (codegen_prompt, fix_prompt):
        assert "dict completo" in prompt
        assert "Dict[str, str]" in prompt


def test_codegen_prompt_includes_authentication_runtime_contract() -> None:
    prompt = build_prompt_file_contract(
        spec=_spec(),
        file_contract=_integration_contract(),
        related_contracts=[],
        descripcion_global="Runtime-auth integration",
        contexto_normalizado=None,
    )

    assert "AUTHENTICATION RUNTIME CONTRACT" in prompt
    assert "Discovery:" in prompt
    assert "ambient" in prompt
    assert "Requires configuration field:" in prompt
    assert "false" in prompt.lower()
    assert "Requires static credential file:" in prompt
    assert "Requires embedded secret:" in prompt
    assert "Do not introduce a new configuration field for credentials when `requires_configuration_field=false`." in prompt


def test_codegen_prompt_forbids_placeholder_credential_arguments_and_from_file_constructors() -> None:
    """Regresión: un proyecto real generó `Credentials.from_service_account_file(None, ...)`
    (una llamada "from file" con un placeholder `None`) pese a `discovery=ambient` y
    `requires_static_credential_file=false`. El prompt debe prohibir explícitamente ambos
    patrones, de forma genérica (sin nombrar ningún SDK concreto)."""
    prompt = build_prompt_file_contract(
        spec=_spec(),
        file_contract=_integration_contract(),
        related_contracts=[],
        descripcion_global="Runtime-auth integration",
        contexto_normalizado=None,
    )

    normalized_prompt = " ".join(prompt.split())

    assert "None" in prompt
    assert "placeholder" in prompt.lower()
    assert "from file" in normalized_prompt.lower() or "from path" in normalized_prompt.lower()
    assert "ambient-credential entry point" in normalized_prompt.lower()

    # Genérico: la regla en sí (no el resto del prompt, que sí incluye datos de ejemplo de la
    # fixture) no debe mencionar ningún SDK/proveedor concreto.
    rule_start = normalized_prompt.lower().index("never pass `none`")
    rule_text = normalized_prompt.lower()[rule_start : rule_start + 600]
    for forbidden in ("google", "aws", "azure", "boto3"):
        assert forbidden not in rule_text


def test_fix_prompt_contains_invalid_field_allowed_fields_and_runtime_contract() -> None:
    prompt = build_prompt_file_contract_fix(
        spec=_spec(),
        file_contract=_integration_contract(),
        previous_content="cfg = get_settings()\nvalue = cfg.UNKNOWN_FIELD\n",
        errors=["Generated code references configuration field 'UNKNOWN_FIELD' not allowed by FileContract."],
        related_contracts=[],
        structured_issues=[
            {
                "code": "CONFIGURATION_FIELD_MISSING",
                "invalid_configuration_field": "UNKNOWN_FIELD",
                "configuration_contract": {
                    "provider_module": "app.core.config",
                    "provider_symbol": "get_settings",
                    "allowed_fields": ["RESOURCE_ID"],
                    "access_mode": "lazy",
                },
                "authentication_runtime_contract": {
                    "discovery": "ambient",
                    "requires_configuration_field": False,
                    "requires_static_credential_file": False,
                    "requires_embedded_secret": False,
                },
            }
        ],
    )
    normalized_prompt = " ".join(prompt.split())

    assert "UNKNOWN_FIELD" in prompt
    assert "RESOURCE_ID" in prompt
    assert "AUTHENTICATION RUNTIME CONTRACT" in prompt
    assert "requires_configuration_field=false" in normalized_prompt
    assert "requires_static_credential_file=false" in normalized_prompt
    assert "invalid field" in prompt.lower()


def test_prompt_does_not_include_irrelevant_related_contracts():
    prompt = build_prompt_file_contract(
        spec=_spec(),
        file_contract=_endpoint_contract(),
        related_contracts=[_integration_contract()],
        descripcion_global="Upload files to Google Drive",
        contexto_normalizado=None,
    )

    related_section = prompt.split(
        "CONTRATOS RELACIONADOS (RESUMEN INTER-ARCHIVO; SOLO CONTEXTO)",
        1,
    )[1].split("SPEC (RESUMEN; REFERENCIA SECUNDARIA)", 1)[0]

    assert "app/integrations/google_drive.py" in related_section
    assert "app/api/endpoints/health.py" not in related_section


def test_summarize_related_contract_keeps_only_interface_fields():
    summary = _summarize_related_contract(_integration_contract())

    assert summary["path"] == "app/integrations/google_drive.py"
    assert summary["kind"] == "integration"
    assert "provided_interfaces" in summary
    assert "required_internal_calls" in summary
    assert "configuration" in summary
    assert "source" not in summary
    assert "evidence" not in summary
    assert "notes" not in summary


def test_prompt_is_compact_and_does_not_duplicate_current_contract():
    related_contracts = []
    for idx in range(8):
        contract = _integration_contract().copy()
        contract["path"] = f"app/integrations/service_{idx}.py"
        contract["required_symbols"] = [f"build_client_{idx}"]
        related_contracts.append(contract)

    prompt = build_prompt_file_contract(
        spec=_spec(),
        file_contract=_integration_contract(),
        related_contracts=related_contracts,
        descripcion_global="Upload files to external storage",
        contexto_normalizado={"objetivo_tecnico": "Integración externa"},
    )

    assert len(prompt) < 25000
    assert '"path": "app/integrations/google_drive.py"' in prompt
    assert "CONTRATOS RELACIONADOS (RESUMEN INTER-ARCHIVO; SOLO CONTEXTO)" in prompt

    related_section = prompt.split(
        "CONTRATOS RELACIONADOS (RESUMEN INTER-ARCHIVO; SOLO CONTEXTO)",
        1,
    )[1].split("SPEC (RESUMEN; REFERENCIA SECUNDARIA)", 1)[0]

    assert '"source"' not in related_section
    assert '"evidence"' not in related_section
