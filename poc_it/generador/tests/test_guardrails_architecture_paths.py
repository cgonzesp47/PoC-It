from __future__ import annotations
import pytest

from poc_it.generador.guardrails import guardrails_por_spec, seleccionar_error_bloqueante


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
