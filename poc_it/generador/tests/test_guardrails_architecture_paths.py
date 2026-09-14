from __future__ import annotations
import pytest

from poc_it.generador.guardrails import (
    _contains_sensitive_literal_assignment,
    _missing_exception_logging,
    guardrails_por_spec,
    seleccionar_error_bloqueante,
)


def test_guardrails_recognizes_api_endpoints_path_as_endpoint_file() -> None:
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
def listar():
    return []
""".strip(),
        }
    ]

    # SPEC declara json; el endpoint usa UploadFile/File => debe detectarse y reportar error
    res = guardrails_por_spec(spec, files)
    assert res.ok is False
    assert any("multipart" in e.lower() for e in res.errors)
    assert "app/api/endpoints/productos.py" in set(res.repair_paths)


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
    # no debería marcar error por imports de endpoints extra (solo chequea from app.*endpoints import router)
    assert all("Endpoint extra" not in e for e in res.errors)


def test_router_importing_app_api_endpoints_is_ok() -> None:
    spec = {"endpoints": [{"file": "app/api/endpoints/productos.py", "method": "GET", "path": "/productos"}], "restrictions": []}
    files = [
        {"path": "app/api/router.py", "content": "from app.api.endpoints.productos import router\n"},
        {"path": "app/api/endpoints/productos.py", "content": "from fastapi import APIRouter\nrouter = APIRouter()\n"},
    ]
    res = guardrails_por_spec(spec, files)
    assert res.ok is True


def test_seleccionar_error_bloqueante_prioritizes_new_architecture() -> None:
    errors = [
        "app/api/endpoints/productos.py: Falta logging obligatorio",
        "app/main.py: X",
        "app/core/config.py: Y",
        "app/endpoints/legacy.py: Z",
    ]
    picked = seleccionar_error_bloqueante(errors)
    assert picked is not None
    assert picked[0] == "app/core/config.py"


def test_multipart_check_ignores_unrelated_file_call() -> None:
    """'File(' es un token genérico: no debe dispararse por una llamada no relacionada con
    fastapi (p.ej. una clase propia también llamada File, o una función 'save_file(...)')."""
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
from fastapi import APIRouter
from app.services.storage import File

router = APIRouter()


@router.get("/productos")
def listar():
    return File(name="x")
""".strip(),
        }
    ]

    res = guardrails_por_spec(spec, files)
    assert all("multipart" not in e.lower() for e in res.errors)


# ---------------------------------------------------------------------------
# HARDCODED_CREDENTIALS_LITERAL: values-not-keys (evita falso positivo por nombre de key)
# ---------------------------------------------------------------------------


def test_credential_named_key_sourced_from_env_is_not_hardcoded() -> None:
    src = """
import os

service_account_info = {
    "type": "service_account",
    "private_key": os.environ["GOOGLE_PRIVATE_KEY"],
    "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
}
""".strip()
    assert _contains_sensitive_literal_assignment(src) is False


def test_credential_named_key_with_literal_value_is_hardcoded() -> None:
    src = """
service_account_info = {
    "type": "service_account",
    "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIExampleFake\\n-----END PRIVATE KEY-----",
}
""".strip()
    assert _contains_sensitive_literal_assignment(src) is True


def test_embedded_private_key_value_is_hardcoded_regardless_of_key_name() -> None:
    src = """
raw_blob = {"payload": "-----BEGIN PRIVATE KEY-----\\nMIIExampleFake\\n-----END PRIVATE KEY-----"}
""".strip()
    assert _contains_sensitive_literal_assignment(src) is True


# ---------------------------------------------------------------------------
# Logging guardrail: AST-based, por except-handler, cualquier nombre de logger
# ---------------------------------------------------------------------------


def test_logging_guardrail_accepts_non_default_logger_name() -> None:
    src = """
log = get_logger(__name__)

def handler():
    try:
        do_something()
    except Exception:
        log.exception("failed")
        raise HTTPException(status_code=500)
""".strip()
    assert _missing_exception_logging(src) is False


def test_logging_guardrail_accepts_error_with_exc_info_true() -> None:
    src = """
logger = get_logger(__name__)

def handler():
    try:
        do_something()
    except Exception as exc:
        logger.error("failed", exc_info=True)
        raise HTTPException(status_code=500)
""".strip()
    assert _missing_exception_logging(src) is False


def test_logging_guardrail_accepts_bare_reraise() -> None:
    src = """
def handler():
    try:
        do_something()
    except Exception:
        raise
""".strip()
    assert _missing_exception_logging(src) is False


def test_logging_guardrail_still_flags_swallowed_exception() -> None:
    src = """
def handler():
    try:
        do_something()
    except Exception:
        return {"status": "error"}
""".strip()
    assert _missing_exception_logging(src) is True


def test_logging_guardrail_does_not_leak_across_unrelated_except_blocks() -> None:
    """Un `logger.exception(...)` en OTRO except block no debe "tapar" un except Exception
    distinto que sí traga el error silenciosamente."""
    src = """
def a():
    try:
        risky()
    except ValueError:
        logger.exception("value error")


def b():
    try:
        risky()
    except Exception:
        return None
""".strip()
    assert _missing_exception_logging(src) is True


# ---------------------------------------------------------------------------
# json_example key coherence: acepta uso como identificador (Pydantic response_model)
# ---------------------------------------------------------------------------


def test_json_example_key_coherence_accepts_pydantic_response_model() -> None:
    spec = {
        "endpoints": [
            {
                "file": "app/api/endpoints/health.py",
                "method": "GET",
                "path": "/health",
                "request": {"type": "none"},
                "response": {"json_example": {"status": "ok"}},
            }
        ],
        "restrictions": [],
    }
    files = [
        {
            "path": "app/api/endpoints/health.py",
            "content": """
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str


@router.get("/health")
def health() -> HealthResponse:
    return HealthResponse(status="ok")
""".strip(),
        }
    ]

    res = guardrails_por_spec(spec, files)
    assert all("no parece incluir ninguna de las claves" not in e for e in res.errors)
