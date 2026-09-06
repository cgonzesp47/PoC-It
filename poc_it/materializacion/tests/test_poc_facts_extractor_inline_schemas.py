from __future__ import annotations

from poc_it.materializacion.poc_facts_extractor import extract_poc_facts_from_structure


def test_inline_basemodel_defined_in_endpoint_file_is_detected_as_request_model():
    """Regresión real: `UploadRequest(BaseModel)` definido en el propio fichero del endpoint
    (no en app/schemas.py) no se reconocía como request model, dejando
    request_model/request_body_param en None y degradando la generación de tests a
    OPENAPI_CONTRACT por "falta de evidencia" de un schema que en realidad sí existía."""
    estructura = {
        "app/api/endpoints/upload.py": (
            "from fastapi import APIRouter\n"
            "from pydantic import BaseModel\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "\n"
            "class UploadRequest(BaseModel):\n"
            "    filename: str\n"
            "\n"
            "\n"
            "@router.post('/upload')\n"
            "async def create_upload(request: UploadRequest) -> dict:\n"
            "    return {'status': 'ok'}\n"
        ),
    }

    facts = extract_poc_facts_from_structure(estructura)
    endpoint = next(ep for ep in facts.endpoints if ep.path == "/upload")

    assert endpoint.request_model == "UploadRequest"
    assert endpoint.request_body_param == "request"
    assert endpoint.request_required_fields == ["filename"]


def test_basemodel_in_dedicated_schemas_file_still_detected():
    """No regresión: el caso ya soportado (modelo en app/schemas.py) sigue funcionando."""
    estructura = {
        "app/schemas.py": (
            "from pydantic import BaseModel\n"
            "\n"
            "\n"
            "class ProductCreate(BaseModel):\n"
            "    name: str\n"
        ),
        "app/api/endpoints/products.py": (
            "from fastapi import APIRouter\n"
            "from app.schemas import ProductCreate\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "\n"
            "@router.post('/products')\n"
            "async def create_product(product: ProductCreate) -> dict:\n"
            "    return {'status': 'ok'}\n"
        ),
    }

    facts = extract_poc_facts_from_structure(estructura)
    endpoint = next(ep for ep in facts.endpoints if ep.path == "/products")

    assert endpoint.request_model == "ProductCreate"
    assert endpoint.request_body_param == "product"


def test_non_basemodel_class_in_endpoint_file_is_not_indexed():
    """No debe indexarse cualquier clase del fichero: solo las que heredan de BaseModel
    (directa o indirectamente)."""
    estructura = {
        "app/api/endpoints/upload.py": (
            "from fastapi import APIRouter\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "\n"
            "class NotAModel:\n"
            "    filename: str\n"
            "\n"
            "\n"
            "@router.post('/upload')\n"
            "async def create_upload(request: NotAModel) -> dict:\n"
            "    return {'status': 'ok'}\n"
        ),
    }

    facts = extract_poc_facts_from_structure(estructura)
    endpoint = next(ep for ep in facts.endpoints if ep.path == "/upload")

    assert endpoint.request_model is None
    assert endpoint.request_body_param is None
