from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class CharacterizationFixture:
    name: str
    max_level: str
    project_structure: Dict[str, str]
    runtime_contracts: Optional[dict]
    runtime_facts: Optional[dict]
    expected_pytest_result: str


def all_characterization_fixtures() -> List[CharacterizationFixture]:
    return [
        CharacterizationFixture(
            name="api_sin_dependencias",
            max_level="OPENAPI_CONTRACT",
            project_structure={
                "app/main.py": """
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {
                    "openapi": "3.1.0",
                    "paths": {"/health": {"get": {"responses": {"200": {"description": "OK"}}}}},
                },
                "allowed_dependency_overrides": [],
                "endpoints": [{"path": "/health", "method": "GET"}],
            },
            runtime_facts={"app_module": "app.main"},
            expected_pytest_result="pass",
        ),
        CharacterizationFixture(
            name="crud_dependencia_repositorio",
            max_level="HERMETIC_ENDPOINT_CONTRACT",
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI

app = FastAPI()

class Repo:
    def list_items(self):
        return [{"id": 1, "name": "a"}]

def get_repo():
    return Repo()

@app.get("/items")
def list_items(repo: Repo = Depends(get_repo)):
    return repo.list_items()
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {"openapi": "3.1.0", "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}}},
                "allowed_dependency_overrides": ["app.main.get_repo"],
                "endpoints": [{"path": "/items", "method": "GET"}],
            },
            runtime_facts={"app_module": "app.main"},
            expected_pytest_result="pass",
        ),
        CharacterizationFixture(
            name="endpoint_con_get_db",
            max_level="HERMETIC_ENDPOINT_CONTRACT",
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI

app = FastAPI()

def get_db():
    return {"kind": "db"}

@app.get("/db-check")
def db_check(db = Depends(get_db)):
    return {"db": db["kind"]}
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {"openapi": "3.1.0", "paths": {"/db-check": {"get": {"responses": {"200": {"description": "OK"}}}}}},
                "allowed_dependency_overrides": ["app.main.get_db"],
                "endpoints": [{"path": "/db-check", "method": "GET"}],
            },
            runtime_facts={"app_module": "app.main"},
            expected_pytest_result="pass",
        ),
        CharacterizationFixture(
            name="cliente_http_externo",
            max_level="HERMETIC_ENDPOINT_CONTRACT",
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI

app = FastAPI()

class ExternalClient:
    def ping(self):
        return {"up": True}

def get_client():
    return ExternalClient()

@app.get("/external")
def external(client: ExternalClient = Depends(get_client)):
    return client.ping()
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {"openapi": "3.1.0", "paths": {"/external": {"get": {"responses": {"200": {"description": "OK"}}}}}},
                "allowed_dependency_overrides": ["app.main.get_client"],
                "endpoints": [{"path": "/external", "method": "GET"}],
            },
            runtime_facts={"app_module": "app.main"},
            expected_pytest_result="pass",
        ),
        CharacterizationFixture(
            name="endpoint_async",
            max_level="HERMETIC_ENDPOINT_CONTRACT",
            project_structure={
                "app/main.py": """
from fastapi import FastAPI

app = FastAPI()

@app.get("/async-health")
async def async_health():
    return {"status": "ok"}
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {"openapi": "3.1.0", "paths": {"/async-health": {"get": {"responses": {"200": {"description": "OK"}}}}}},
                "allowed_dependency_overrides": [],
                "endpoints": [{"path": "/async-health", "method": "GET"}],
            },
            runtime_facts={"app_module": "app.main"},
            expected_pytest_result="pass",
        ),
        CharacterizationFixture(
            name="endpoint_autenticacion",
            max_level="HERMETIC_ENDPOINT_CONTRACT",
            project_structure={
                "app/main.py": """
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

app = FastAPI()
security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    if token != "ok":
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"sub": "demo"}

@app.get("/me")
def me(user = Depends(get_current_user)):
    return user
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {"openapi": "3.1.0", "paths": {"/me": {"get": {"responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}}}}}},
                "allowed_dependency_overrides": ["app.main.get_current_user"],
                "endpoints": [{"path": "/me", "method": "GET"}],
            },
            runtime_facts={"app_module": "app.main"},
            expected_pytest_result="pass",
        ),
        CharacterizationFixture(
            name="path_y_query_params",
            max_level="OPENAPI_CONTRACT",
            project_structure={
                "app/main.py": """
from fastapi import FastAPI

app = FastAPI()

@app.get("/items/{item_id}")
def get_item(item_id: int, q: str | None = None):
    return {"item_id": item_id, "q": q}
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {"openapi": "3.1.0", "paths": {"/items/{item_id}": {"get": {"responses": {"200": {"description": "OK"}}}}}},
                "allowed_dependency_overrides": [],
                "endpoints": [{"path": "/items/{item_id}", "method": "GET"}],
            },
            runtime_facts={"app_module": "app.main"},
            expected_pytest_result="pass",
        ),
        CharacterizationFixture(
            name="body_anidado",
            max_level="OPENAPI_CONTRACT",
            project_structure={
                "app/main.py": """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class Address(BaseModel):
    city: str
    zip_code: str

class UserCreate(BaseModel):
    name: str
    address: Address

@app.post("/users", status_code=201)
def create_user(body: UserCreate):
    return body.model_dump()
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {"openapi": "3.1.0", "paths": {"/users": {"post": {"responses": {"201": {"description": "Created"}}}}}},
                "allowed_dependency_overrides": [],
                "endpoints": [{"path": "/users", "method": "POST"}],
            },
            runtime_facts={"app_module": "app.main"},
            expected_pytest_result="pass",
        ),
        CharacterizationFixture(
            name="multipart_upload",
            max_level="OPENAPI_CONTRACT",
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
                "openapi": {"openapi": "3.1.0", "paths": {"/upload": {"post": {"responses": {"200": {"description": "OK"}}}}}},
                "allowed_dependency_overrides": [],
                "endpoints": [{"path": "/upload", "method": "POST"}],
            },
            runtime_facts={"app_module": "app.main"},
            expected_pytest_result="pass",
        ),
        CharacterizationFixture(
            name="api_con_lifespan",
            max_level="SMOKE_ONLY",
            project_structure={
                "app/main.py": """
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ready = True
    yield
    app.state.ready = False

app = FastAPI(lifespan=lifespan)

@app.get("/ready")
def ready():
    return {"ready": True}
""".strip()
                + "\n",
            },
            runtime_contracts={
                "openapi": {"openapi": "3.1.0", "paths": {"/ready": {"get": {"responses": {"200": {"description": "OK"}}}}}},
                "allowed_dependency_overrides": [],
                "endpoints": [{"path": "/ready", "method": "GET"}],
            },
            runtime_facts={"app_module": "app.main"},
            expected_pytest_result="pass",
        ),
    ]
