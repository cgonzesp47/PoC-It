from __future__ import annotations

from poc_it.generador.file_contracts import build_file_contracts_from_spec


def test_router_allowed_imports_are_modules_not_py_paths() -> None:
    spec = {
        "schema_version": "pocit.spec.v1",
        "status": "draft",
        "files": [
            "app/__init__.py",
            "app/main.py",
            "app/api/__init__.py",
            "app/api/router.py",
            "app/api/endpoints/__init__.py",
            "app/api/endpoints/productos.py",
            "app/core/__init__.py",
            "app/core/config.py",
            "requirements.txt",
            "README.md",
        ],
        "dependencies": ["fastapi", "uvicorn"],
        "dev_dependencies": ["pytest"],
        "env": [],
        "persistence": {"required": False},
        "test_strategy": {},
        "source": {},
        "endpoints": [
            {
                "method": "GET",
                "path": "/productos",
                "file": "app/api/endpoints/productos.py",
                "func": "list_productos",
                "request": {"type": "none"},
                "response": {"json_example": []},
                "errors": [],
                "bundle_files": [],
                "source": {"type": "explicit", "evidence": "x"},
            }
        ],
        "contracts": [],
    }

    contracts = build_file_contracts_from_spec(spec)
    router = next(c for c in contracts if c.path == "app/api/router.py")

    assert "app.api.endpoints.productos" in router.allowed_imports
    assert "app/api/endpoints/productos.py" not in router.allowed_imports
