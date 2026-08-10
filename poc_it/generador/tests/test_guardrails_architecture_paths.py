from __future__ import annotations

from pathlib import Path

from poc_it.generador.guardrails import guardrails_por_spec, seleccionar_error_bloqueante
from poc_it.generador.restriction_models import (
    RestrictionEnforcement,
    migrate_legacy_enforcement,
    parse_restriction_enforcement,
)
from poc_it.generador.restrictions import sanitizar_restrictions
from poc_it.materializacion.generador_artefactos import generar_proyecto_desde_spec


def test_guardrails_do_not_validate_request_transport_semantics() -> None:
    spec = {
        "endpoints": [
            {"file": "app/api/endpoints/productos.py", "method": "GET", "path": "/productos", "request": {"type": "json"}}
        ],
        "restrictions": [],
    }
    files = [
        {
            "path": "app/api/endpoints/productos.py",
            "content": """
from fastapi import APIRouter, UploadFile, File
router = APIRouter()

@router.get("/productos")
def listar(file: UploadFile = File(...)):
    return []
""".strip(),
        }
    ]

    res = guardrails_por_spec(spec, files)
    assert res.ok is True
    assert all("multipart" not in e.lower() for e in res.errors)


def test_main_importing_app_api_router_is_ok() -> None:
    spec = {
        "endpoints": [
            {"file": "app/api/endpoints/productos.py", "method": "GET", "path": "/productos", "request": {"type": "json"}}
        ],
        "restrictions": [],
    }
    files = [
        {"path": "app/main.py", "content": "from fastapi import FastAPI\nfrom app.api.router import api_router\napp = FastAPI()\napp.include_router(api_router)\n"},
        {"path": "app/api/router.py", "content": "from fastapi import APIRouter\nfrom app.api.endpoints import productos\napi_router = APIRouter()\napi_router.include_router(productos.router)\n"},
        {"path": "app/api/endpoints/productos.py", "content": "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/productos')\ndef listar():\n    return []\n"},
    ]

    res = guardrails_por_spec(spec, files)
    assert all("Endpoint extra" not in e for e in res.errors)


def test_router_importing_app_api_endpoints_is_ok() -> None:
    spec = {"endpoints": [{"file": "app/api/endpoints/productos.py", "method": "GET", "path": "/productos"}], "restrictions": []}
    files = [
        {"path": "app/api/router.py", "content": "from app.api.endpoints.productos import router\n"},
        {"path": "app/api/endpoints/productos.py", "content": "from fastapi import APIRouter\nrouter = APIRouter()\n"},
    ]
    res = guardrails_por_spec(spec, files)
    assert res.ok is True


def test_seleccionar_error_bloqueante_uses_file_contract_kind_priority() -> None:
    errors = [
        "app/api/endpoints/productos.py: Falta logging obligatorio",
        "app/main.py: X",
        "app/core/config.py: Y",
        "src/custom/location/helper.py: Z",
    ]
    file_contracts = [
        {"path": "app/api/endpoints/productos.py", "kind": "endpoint"},
        {"path": "app/core/config.py", "kind": "config"},
        {"path": "src/custom/location/helper.py", "kind": "service"},
    ]
    picked = seleccionar_error_bloqueante(errors, file_contracts=file_contracts)
    assert picked is not None
    assert picked[0] == "app/core/config.py"


def test_text_restriction_applies_only_with_text_enforcement() -> None:
    spec = {
        "restrictions": [
            {
                "id": "doc-only",
                "enforcement": "external_precondition",
                "must_not_contain": ["FORBIDDEN_MARKER"],
            },
            {
                "id": "text-only",
                "enforcement": "text",
                "must_not_contain": ["FORBIDDEN_MARKER"],
            },
        ]
    }
    files = [
        {"path": "app/example.py", "content": "FORBIDDEN_MARKER\n"}
    ]

    res = guardrails_por_spec(spec, files)
    assert any("text-only" in error for error in res.errors)
    assert all("doc-only" not in error for error in res.errors)


def test_credentials_word_is_allowed() -> None:
    spec = {"restrictions": []}
    files = [
        {
            "path": "app/integrations/client.py",
            "content": """
def build_client(runtime_credentials):
    return create_client(credentials=runtime_credentials)
""".strip(),
        }
    ]

    res = guardrails_por_spec(spec, files)
    assert res.ok is True
    assert res.errors == []


def test_literal_secret_detected_from_contractual_secret_name() -> None:
    spec = {
        "file_contracts": [
            {
                "path": "app/core/config.py",
                "config_keys": [
                    {"name": "API_SECRET", "secret": True},
                ],
            }
        ],
        "restrictions": [],
    }
    files = [
        {
            "path": "app/core/config.py",
            "content": 'API_SECRET = "hard-coded-secret"\n',
        }
    ]

    res = guardrails_por_spec(spec, files)
    assert res.ok is False
    assert any("HARDCODED_SECRET_LITERAL" in error for error in res.errors)


def test_private_key_literal_detected_as_high_confidence_secret() -> None:
    spec = {"restrictions": []}
    files = [
        {
            "path": "app/core/keys.py",
            "content": 'PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----"""\n',
        }
    ]

    res = guardrails_por_spec(spec, files)
    assert res.ok is False
    assert any("HARDCODED_SECRET_LITERAL" in error for error in res.errors)


def test_parse_restriction_enforcement_rejects_legacy_code() -> None:
    assert parse_restriction_enforcement("code") is None


def test_migrate_legacy_code_without_text_matchers_defaults_to_semantic() -> None:
    assert migrate_legacy_enforcement({"enforcement": "code"}) is RestrictionEnforcement.SEMANTIC


def test_migrate_legacy_code_with_text_matchers_becomes_text() -> None:
    assert migrate_legacy_enforcement(
        {
            "enforcement": "code",
            "must_contain_any": ["EXPECTED_MARKER"],
        }
    ) is RestrictionEnforcement.TEXT


def test_modern_enforcement_is_preserved() -> None:
    assert migrate_legacy_enforcement(
        {"enforcement": "external_precondition"}
    ) is RestrictionEnforcement.EXTERNAL_PRECONDITION


def test_unknown_enforcement_without_text_matchers_falls_back_to_semantic() -> None:
    assert migrate_legacy_enforcement(
        {"enforcement": "garbage"}
    ) is RestrictionEnforcement.SEMANTIC


def test_unknown_enforcement_with_text_matchers_falls_back_to_text() -> None:
    assert migrate_legacy_enforcement(
        {
            "enforcement": "garbage",
            "must_not_contain": ["BAD"],
        }
    ) is RestrictionEnforcement.TEXT


def test_sanitizar_restrictions_never_emits_code_enforcement() -> None:
    result = sanitizar_restrictions(
        [
            {"enforcement": "code"},
            {"enforcement": "garbage", "must_not_contain": ["BAD"]},
            {"enforcement": "runtime"},
            {"enforcement": "documentation"},
            {"enforcement": "external_precondition"},
            {"enforcement": "semantic"},
            {"enforcement": "text"},
        ]
    )

    allowed = {
        "text",
        "semantic",
        "runtime",
        "external_precondition",
        "documentation",
    }
    assert all(restriction["enforcement"] in allowed for restriction in result)
    assert all(restriction["enforcement"] != "code" for restriction in result)


def test_sanitizar_restrictions_migrates_legacy_metadata_structurally() -> None:
    result = sanitizar_restrictions(
        [
            {"id": "legacy-text", "enforcement": "code", "must_not_contain": ["BAD"]},
            {"id": "legacy-semantic", "enforcement": "code"},
            {"id": "unknown-semantic", "enforcement": "garbage"},
            {"id": "unknown-text", "enforcement": "garbage", "must_contain_any": ["OK"]},
        ]
    )

    by_id = {item["id"]: item for item in result}
    assert by_id["legacy-text"]["enforcement"] == "text"
    assert by_id["legacy-semantic"]["enforcement"] == "semantic"
    assert by_id["unknown-semantic"]["enforcement"] == "semantic"
    assert by_id["unknown-text"]["enforcement"] == "text"
    assert by_id["legacy-text"]["migration_applied"] is True
    assert by_id["legacy-semantic"]["migration_applied"] is True


def test_guardrails_do_not_crash_for_legacy_code_enforcement() -> None:
    spec = {
        "restrictions": [
            {
                "id": "legacy-rule",
                "enforcement": "code",
            }
        ]
    }
    files = [{"path": "app/example.py", "content": "print('ok')\n"}]

    res = guardrails_por_spec(spec, files)
    assert res.ok is True
    assert any("RESTRICTION_ENFORCEMENT_UNKNOWN" in warning for warning in res.warnings)


def test_guardrails_unknown_enforcement_emits_diagnostic_and_uses_structural_fallback() -> None:
    spec = {
        "restrictions": [
            {
                "id": "legacy-text",
                "enforcement": "garbage",
                "must_not_contain": ["BAD"],
            }
        ]
    }
    files = [{"path": "app/example.py", "content": "BAD\n"}]

    res = guardrails_por_spec(spec, files)
    assert res.ok is False
    assert any("legacy-text" in error for error in res.errors)
    assert any("RESTRICTION_ENFORCEMENT_UNKNOWN" in warning for warning in res.warnings)


def test_guardrails_semantic_enforcement_does_not_execute_text_matchers() -> None:
    spec = {
        "restrictions": [
            {
                "id": "semantic-rule",
                "enforcement": "semantic",
                "must_not_contain": ["credentials"],
            }
        ]
    }
    files = [{"path": "app/example.py", "content": "credentials\n"}]

    res = guardrails_por_spec(spec, files)
    assert res.ok is True
    assert all("semantic-rule" not in error for error in res.errors)


def test_guardrails_text_enforcement_executes_text_matchers() -> None:
    spec = {
        "restrictions": [
            {
                "id": "text-rule",
                "enforcement": "text",
                "must_not_contain": ["FORBIDDEN_MARKER"],
            }
        ]
    }
    files = [{"path": "app/example.py", "content": "FORBIDDEN_MARKER\n"}]

    res = guardrails_por_spec(spec, files)
    assert res.ok is False
    assert any("text-rule" in error for error in res.errors)


def test_generar_proyecto_desde_spec_persists_compiled_restrictions_debug_snapshot() -> None:
    for path in (
        Path("output/_debug/compiled_restrictions.json"),
        Path("output/_debug/spec_with_compiled_restrictions.json"),
    ):
        if path.exists():
            path.unlink()

    spec = {
        "files": ["README.md"],
        "restrictions": [
            {
                "id": "legacy-rule",
                "enforcement": "code",
            }
        ],
    }

    result = generar_proyecto_desde_spec(
        spec=spec,
        descripcion_global="debug restrictions",
        files_iniciales=[{"path": "README.md", "content": "# ok\n"}],
    )

    assert isinstance(result, dict)
    assert Path("output/_debug/compiled_restrictions.json").exists()
    assert Path("output/_debug/spec_with_compiled_restrictions.json").exists()
