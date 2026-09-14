from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class E2EMatrixFixture:
    name: str
    description: str
    expected_levels: tuple[int, ...]
    expected_generated_files: tuple[str, ...]
    expected_missing_files: tuple[str, ...]
    expected_limitations_substrings: tuple[str, ...]
    project_structure: Dict[str, str]
    runtime_contracts: Optional[dict]
    runtime_facts: Optional[dict]
    stateful_scenarios: tuple[dict, ...] = ()
    dependency_behaviors: tuple[dict, ...] = ()
    expected_pytest_result: str = "pass"


def all_e2e_matrix_fixtures() -> List[E2EMatrixFixture]:
    return [
        E2EMatrixFixture(
            name="fastapi_sin_dependencias",
            description="FastAPI sin dependencias, endpoint de transformación simple.",
            expected_levels=(0, 1, 2),
            expected_generated_files=(
                "tests/test_smoke_import.py",
                "tests/test_startup.py",
                "tests/test_openapi_contract.py",
            ),
            expected_missing_files=("tests/conftest.py",),
            expected_limitations_substrings=(),
            project_structure={
                "app/main.py": """
from fastapi import FastAPI

app = FastAPI()

@app.post("/transform")
def transform(payload: dict):
    text = str(payload.get("text", ""))
    return {"result": text.upper()}
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/transform": {
                            "post": {
                                "operationId": "post:/transform",
                                "responses": {"200": {"description": "OK"}},
                            }
                        }
                    },
                },
                "allowed_dependency_overrides": [],
                "endpoints": [
                    {
                        "path": "/transform",
                        "method": "POST",
                        "operation_id": "post:/transform",
                        "status_code": 200,
                        "sample_request": {"text": "hello"},
                        "response_json_required_keys": ["result"],
                    }
                ],
            },
            runtime_facts={"app_module": "app.main"},
        ),
        E2EMatrixFixture(
            name="crud_repositorio_inyectado",
            description="CRUD con repositorio inyectado para permitir escenario stateful.",
            expected_levels=(0, 1, 2, 3),
            expected_generated_files=(
                "tests/test_smoke_import.py",
                "tests/test_startup.py",
                "tests/test_openapi_contract.py",
                "tests/conftest.py",
            ),
            expected_missing_files=(),
            expected_limitations_substrings=(),
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class ItemIn(BaseModel):
    name: str

class Repo:
    def __init__(self):
        self._items = {}
        self._next_id = 1

    def create(self, payload: dict):
        item = {"id": self._next_id, **payload}
        self._items[self._next_id] = item
        self._next_id += 1
        return item

    def get(self, item_id: int):
        return self._items.get(item_id)

    def delete(self, item_id: int):
        return self._items.pop(item_id, None) is not None

repo_singleton = Repo()

def get_repository():
    return repo_singleton

@app.post("/items", status_code=201)
def create_item(body: ItemIn, repo: Repo = Depends(get_repository)):
    return repo.create(body.model_dump())

@app.get("/items/{item_id}")
def get_item(item_id: int, repo: Repo = Depends(get_repository)):
    item = repo.get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return item

@app.delete("/items/{item_id}")
def delete_item(item_id: int, repo: Repo = Depends(get_repository)):
    deleted = repo.delete(item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="not found")
    return {"deleted": True}
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/items": {
                            "post": {
                                "operationId": "post:/items",
                                "responses": {"201": {"description": "Created"}},
                            }
                        },
                        "/items/{item_id}": {
                            "get": {
                                "operationId": "get:/items/{item_id}",
                                "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                            },
                            "delete": {
                                "operationId": "delete:/items/{item_id}",
                                "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                            },
                        },
                    },
                },
                "allowed_dependency_overrides": ["app.main.get_repository"],
                "endpoints": [
                    {
                        "path": "/items",
                        "method": "POST",
                        "operation_id": "post:/items",
                        "handler_name": "create_item",
                        "depends_imports": ["app.main.get_repository"],
                        "observed_methods": ["create"],
                        "statefulness_recommended": "per_client_fixture",
                        "status_code": 201,
                        "sample_request": {"name": "demo"},
                        "response_json_required_keys": ["id", "name"],
                    },
                    {
                        "path": "/items/{item_id}",
                        "method": "GET",
                        "operation_id": "get:/items/{item_id}",
                        "handler_name": "get_item",
                        "depends_imports": ["app.main.get_repository"],
                        "observed_methods": ["get"],
                        "status_code": 200,
                        "sample_request": None,
                        "response_json_required_keys": ["id", "name"],
                    },
                    {
                        "path": "/items/{item_id}",
                        "method": "DELETE",
                        "operation_id": "delete:/items/{item_id}",
                        "handler_name": "delete_item",
                        "depends_imports": ["app.main.get_repository"],
                        "observed_methods": ["delete"],
                        "status_code": 200,
                        "sample_request": None,
                        "response_json_required_keys": ["deleted"],
                    },
                ],
            },
            runtime_facts={"app_module": "app.main"},
            stateful_scenarios=(
                {
                    "scenario_id": "repository_crud_lifecycle",
                    "name": "Create, retrieve and delete entity",
                    "operation_ids": (
                        "post:/items",
                        "get:/items/{item_id}",
                        "delete:/items/{item_id}",
                    ),
                    "shared_dependencies": (
                        "app.main.get_repository",
                    ),
                    "confidence": 0.95,
                    "evidence": ["fixture_stateful_crud"],
                },
            ),
            dependency_behaviors=(),
        ),
        E2EMatrixFixture(
            name="repositorio_estilo_sqlalchemy",
            description="Repositorio que encapsula acceso tipo SQLAlchemy sin sesión directa en handler.",
            expected_levels=(0, 1, 2),
            expected_generated_files=(
                "tests/test_smoke_import.py",
                "tests/test_startup.py",
                "tests/test_openapi_contract.py",
                "tests/conftest.py",
            ),
            expected_missing_files=(),
            expected_limitations_substrings=(),
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI

app = FastAPI()

class ProductRepository:
    def list_products(self):
        return [{"id": 1, "name": "keyboard"}]

def get_product_repository():
    return ProductRepository()

@app.get("/products")
def list_products(repo: ProductRepository = Depends(get_product_repository)):
    return repo.list_products()
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/products": {
                            "get": {
                                "operationId": "get:/products",
                                "responses": {"200": {"description": "OK"}},
                            }
                        }
                    },
                },
                "allowed_dependency_overrides": ["app.main.get_product_repository"],
                "endpoints": [
                    {
                        "path": "/products",
                        "method": "GET",
                        "operation_id": "get:/products",
                        "handler_name": "list_products",
                        "depends_imports": ["app.main.get_product_repository"],
                        "observed_methods": ["list_products"],
                        "status_code": 200,
                        "response_json_required_keys": [],
                    }
                ],
            },
            runtime_facts={"app_module": "app.main"},
        ),
        E2EMatrixFixture(
            name="sesion_directa_limitada",
            description="Sesión directa inyectada en handler, con degradación explícita.",
            expected_levels=(0, 1),
            expected_generated_files=(
                "tests/test_smoke_import.py",
                "tests/test_startup.py",
                "tests/test_openapi_contract.py",
            ),
            expected_missing_files=(),
            expected_limitations_substrings=("degraded", "OPENAPI_ONLY"),
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI

app = FastAPI()

class Session:
    def execute(self, query: str):
        return [{"id": 1, "name": "limited"}]

def get_session():
    return Session()

@app.get("/direct-session")
def direct_session(session: Session = Depends(get_session)):
    return session.execute("select * from items")
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/direct-session": {
                            "get": {
                                "operationId": "get:/direct-session",
                                "responses": {"200": {"description": "OK"}},
                            }
                        }
                    },
                },
                "allowed_dependency_overrides": [],
                "endpoints": [
                    {
                        "path": "/direct-session",
                        "method": "GET",
                        "operation_id": "get:/direct-session",
                        "handler_name": "direct_session",
                        "depends_imports": ["app.main.get_session"],
                        "observed_methods": ["execute"],
                        "status_code": 200,
                        "response_json_required_keys": [],
                        "level": "OPENAPI_ONLY",
                        "reason": "direct session dependency degraded to OPENAPI_ONLY",
                    }
                ],
            },
            runtime_facts={"app_module": "app.main"},
        ),
        E2EMatrixFixture(
            name="google_drive_gateway",
            description="Gateway local de Google Drive simulado sin SDK ni red.",
            expected_levels=(0, 1, 2),
            expected_generated_files=(
                "tests/test_smoke_import.py",
                "tests/test_startup.py",
                "tests/test_openapi_contract.py",
                "tests/conftest.py",
            ),
            expected_missing_files=(),
            expected_limitations_substrings=(),
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI
from pydantic import BaseModel

app = FastAPI()

class UploadIn(BaseModel):
    filename: str

class DriveGateway:
    def upload_file(self, filename: str):
        return {"id": "file-123", "filename": filename}

def get_drive_gateway():
    return DriveGateway()

@app.post("/drive/upload")
def upload(body: UploadIn, drive = Depends(get_drive_gateway)):
    return drive.upload_file(body.filename)
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/drive/upload": {
                            "post": {
                                "operationId": "post:/drive/upload",
                                "responses": {"200": {"description": "OK"}},
                            }
                        }
                    },
                },
                "allowed_dependency_overrides": ["app.main.get_drive_gateway"],
                "endpoints": [
                    {
                        "path": "/drive/upload",
                        "method": "POST",
                        "operation_id": "post:/drive/upload",
                        "handler_name": "upload",
                        "depends_imports": ["app.main.get_drive_gateway"],
                        "observed_methods": ["upload_file"],
                        "status_code": 200,
                        "sample_request": {"filename": "demo.txt"},
                        "response_json_required_keys": ["id", "filename"],
                    }
                ],
            },
            runtime_facts={"app_module": "app.main", "external_integrations": ["google-drive"]},
        ),
        E2EMatrixFixture(
            name="sdk_encadenado_fake",
            description="SDK encadenado local service.files().create().execute() sin red.",
            expected_levels=(0, 1, 2),
            expected_generated_files=(
                "tests/test_smoke_import.py",
                "tests/test_startup.py",
                "tests/test_openapi_contract.py",
                "tests/conftest.py",
            ),
            expected_missing_files=(),
            expected_limitations_substrings=(),
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI

app = FastAPI()

class _CreateRequest:
    def execute(self):
        return {"id": "file-123"}

class _FilesResource:
    def create(self, **kwargs):
        return _CreateRequest()

class FakeDriveService:
    def files(self):
        return _FilesResource()

def get_drive_service():
    return FakeDriveService()

@app.post("/sdk/upload")
def sdk_upload(service = Depends(get_drive_service)):
    return service.files().create(body={"name": "demo.txt"}).execute()
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/sdk/upload": {
                            "post": {
                                "operationId": "post:/sdk/upload",
                                "responses": {"200": {"description": "OK"}},
                            }
                        }
                    },
                },
                "allowed_dependency_overrides": ["app.main.get_drive_service"],
                "endpoints": [
                    {
                        "path": "/sdk/upload",
                        "method": "POST",
                        "operation_id": "post:/sdk/upload",
                        "handler_name": "sdk_upload",
                        "depends_imports": ["app.main.get_drive_service"],
                        "observed_methods": ["files", "create", "execute"],
                        "status_code": 200,
                        "response_json_required_keys": ["id"],
                    }
                ],
            },
            runtime_facts={"app_module": "app.main"},
        ),
        E2EMatrixFixture(
            name="cliente_http_async_inyectado",
            description="Cliente HTTP async inyectado con fake local.",
            expected_levels=(0, 1, 2),
            expected_generated_files=(
                "tests/test_smoke_import.py",
                "tests/test_startup.py",
                "tests/test_openapi_contract.py",
                "tests/conftest.py",
            ),
            expected_missing_files=(),
            expected_limitations_substrings=(),
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI

app = FastAPI()

class AsyncClient:
    async def get(self, path: str):
        return {"path": path, "ok": True}

def get_http_client():
    return AsyncClient()

@app.get("/proxy")
async def proxy(client: AsyncClient = Depends(get_http_client)):
    return await client.get("/upstream")
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/proxy": {
                            "get": {
                                "operationId": "get:/proxy",
                                "responses": {"200": {"description": "OK"}},
                            }
                        }
                    },
                },
                "allowed_dependency_overrides": ["app.main.get_http_client"],
                "endpoints": [
                    {
                        "path": "/proxy",
                        "method": "GET",
                        "operation_id": "get:/proxy",
                        "handler_name": "proxy",
                        "depends_imports": ["app.main.get_http_client"],
                        "observed_methods": ["get"],
                        "status_code": 200,
                        "response_json_required_keys": ["path", "ok"],
                    }
                ],
            },
            runtime_facts={"app_module": "app.main"},
        ),
        E2EMatrixFixture(
            name="autenticacion_depends",
            description="Autenticación basada en Depends con comportamiento local.",
            expected_levels=(0, 1, 2),
            expected_generated_files=(
                "tests/test_smoke_import.py",
                "tests/test_startup.py",
                "tests/test_openapi_contract.py",
                "tests/conftest.py",
            ),
            expected_missing_files=(),
            expected_limitations_substrings=(),
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI, Header, HTTPException

app = FastAPI()

def get_current_user(authorization: str | None = Header(default=None)):
    if authorization != "Bearer ok":
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"sub": "demo"}

@app.get("/me")
def me(user = Depends(get_current_user)):
    return user
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/me": {
                            "get": {
                                "operationId": "get:/me",
                                "responses": {
                                    "200": {"description": "OK"},
                                    "401": {"description": "Unauthorized"},
                                },
                            }
                        }
                    },
                },
                "allowed_dependency_overrides": ["app.main.get_current_user"],
                "endpoints": [
                    {
                        "path": "/me",
                        "method": "GET",
                        "operation_id": "get:/me",
                        "handler_name": "me",
                        "depends_imports": ["app.main.get_current_user"],
                        "dependency_behaviors": [
                            {
                                "dependency_fqn": "app.main.get_current_user",
                                "method_name": "",
                                "action": "provide",
                                "value": {
                                    "sub": "demo",
                                },
                            }
                        ],
                        "status_code": 200,
                        "response_json_required_keys": ["sub"],
                    }
                ],
            },
            runtime_facts={"app_module": "app.main"},
        ),
        E2EMatrixFixture(
            name="multipart_archivos",
            description="Multipart con UploadFile local.",
            expected_levels=(0, 1, 2),
            expected_generated_files=(
                "tests/test_smoke_import.py",
                "tests/test_startup.py",
                "tests/test_openapi_contract.py",
            ),
            expected_missing_files=("tests/conftest.py",),
            expected_limitations_substrings=(),
            project_structure={
                "app/main.py": """
from fastapi import FastAPI, File, UploadFile

app = FastAPI()

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    return {"filename": file.filename}
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/upload": {
                            "post": {
                                "operationId": "post:/upload",
                                "responses": {"200": {"description": "OK"}},
                            }
                        }
                    },
                },
                "allowed_dependency_overrides": [],
                "endpoints": [
                    {
                        "path": "/upload",
                        "method": "POST",
                        "operation_id": "post:/upload",
                        "handler_name": "upload",
                        "status_code": 200,
                        "response_json_required_keys": ["filename"],
                        "file_params_required": ["file"],
                    }
                ],
            },
            runtime_facts={"app_module": "app.main"},
        ),
        E2EMatrixFixture(
            name="workflow_create_read_delete",
            description="Workflow create/read/delete con intención stateful explícita.",
            expected_levels=(0, 1, 2, 3),
            expected_generated_files=(
                "tests/test_smoke_import.py",
                "tests/test_startup.py",
                "tests/test_openapi_contract.py",
                "tests/conftest.py",
            ),
            expected_missing_files=(),
            expected_limitations_substrings=(),
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class DocIn(BaseModel):
    title: str

class DocumentRepo:
    def __init__(self):
        self._items = {}
        self._next_id = 1

    def create(self, payload: dict):
        item = {"id": self._next_id, **payload}
        self._items[self._next_id] = item
        self._next_id += 1
        return item

    def get(self, item_id: int):
        return self._items.get(item_id)

    def delete(self, item_id: int):
        return self._items.pop(item_id, None) is not None

repo_singleton = DocumentRepo()

def get_document_repo():
    return repo_singleton

@app.post("/documents", status_code=201)
def create_document(body: DocIn, repo: DocumentRepo = Depends(get_document_repo)):
    return repo.create(body.model_dump())

@app.get("/documents/{item_id}")
def get_document(item_id: int, repo: DocumentRepo = Depends(get_document_repo)):
    item = repo.get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return item

@app.delete("/documents/{item_id}")
def delete_document(item_id: int, repo: DocumentRepo = Depends(get_document_repo)):
    if not repo.delete(item_id):
        raise HTTPException(status_code=404, detail="not found")
    return {"deleted": True}
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {
                        "/documents": {
                            "post": {
                                "operationId": "post:/documents",
                                "responses": {"201": {"description": "Created"}},
                            }
                        },
                        "/documents/{item_id}": {
                            "get": {
                                "operationId": "get:/documents/{item_id}",
                                "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                            },
                            "delete": {
                                "operationId": "delete:/documents/{item_id}",
                                "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                            },
                        },
                    },
                },
                "allowed_dependency_overrides": ["app.main.get_document_repo"],
                "endpoints": [
                    {
                        "path": "/documents",
                        "method": "POST",
                        "operation_id": "post:/documents",
                        "handler_name": "create_document",
                        "depends_imports": ["app.main.get_document_repo"],
                        "observed_methods": ["create"],
                        "statefulness_recommended": "per_client_fixture",
                        "status_code": 201,
                        "sample_request": {"title": "doc-1"},
                        "response_json_required_keys": ["id", "title"],
                    },
                    {
                        "path": "/documents/{item_id}",
                        "method": "GET",
                        "operation_id": "get:/documents/{item_id}",
                        "handler_name": "get_document",
                        "depends_imports": ["app.main.get_document_repo"],
                        "observed_methods": ["get"],
                        "status_code": 200,
                        "response_json_required_keys": ["id", "title"],
                    },
                    {
                        "path": "/documents/{item_id}",
                        "method": "DELETE",
                        "operation_id": "delete:/documents/{item_id}",
                        "handler_name": "delete_document",
                        "depends_imports": ["app.main.get_document_repo"],
                        "observed_methods": ["delete"],
                        "status_code": 200,
                        "response_json_required_keys": ["deleted"],
                    },
                ],
            },
            runtime_facts={"app_module": "app.main"},
            stateful_scenarios=(
                {
                    "scenario_id": "document_lifecycle",
                    "name": "Create, retrieve and delete document",
                    "operation_ids": (
                        "post:/documents",
                        "get:/documents/{item_id}",
                        "delete:/documents/{item_id}",
                    ),
                    "shared_dependencies": (
                        "app.main.get_document_repo",
                    ),
                    "confidence": 0.95,
                    "evidence": ["fixture_stateful_workflow"],
                },
            ),
            dependency_behaviors=(),
        ),
    ]
