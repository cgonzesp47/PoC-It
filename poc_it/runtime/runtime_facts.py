from __future__ import annotations
"""
Artefacto intermedio "RuntimeFacts" para alinear generación de tests con el código real.

Motivación
----------
El SPEC describe la intención, pero el codegen puede elegir distintos patrones (DI con Depends(clase)
vs Depends(provider), orden de parámetros en services, etc.). Si los tests se generan solo desde el
SPEC, tienden a "inventar" un patrón distinto y fallan.

Solución
--------
- Extraer facts deterministas del código materializado (AST), incluyendo:
  - Endpoints reales (path/method/módulo/depends)
  - Dependencias inyectadas (callables/atributos)
  - Llamadas sobre dependencias (call signatures observadas), especialmente servicios (svc.*).
- Persistir estos facts como JSON dentro del proyecto generado (y opcionalmente en output/_debug),
  para que el generador de tests se alinee 1:1 con el wiring real.

Este módulo es deliberadamente pequeño y agnóstico de FastAPI/SQLAlchemy a nivel semántico:
solo parsea AST y serializa información observable.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional


RUNTIME_FACTS_PATH = ".poc_it/runtime_facts.json"


@dataclass
class ObservedCall:
    """
    Representa una llamada observada en el cuerpo de un endpoint sobre un objeto inyectado.

    Ejemplo: `await svc.create_product(db, product)`
      receiver_param="svc"
      method_name="create_product"
      arg_names=["db", "product"]
      awaited=True
    """

    receiver_param: str
    method_name: str
    arg_names: List[str]
    awaited: bool


@dataclass
class EndpointRuntimeFacts:
    path: str
    method: str
    module_path: str
    func_name: str
    status_code: Optional[int]
    depends: List[str]
    depends_imports: List[str]
    injected_params: List[str]
    uses_injected: bool
    observed_calls: List[ObservedCall]

    # Facts de contrato de REQUEST (body) para alinear tests negativos sin alucinaciones.
    # - request_body_param: nombre del parámetro que recibe el body (si existe)
    # - request_model: nombre del tipo/anotación (p.ej. "ProductCreate"). No es import string necesariamente.
    # - request_required_fields / request_optional_fields: campos requeridos/opcionales del modelo (si se pudo inferir).
    #   Si están vacíos/None, el generador de tests NO debe asumir 422 por "missing required" salvo evidencia del SPEC.
    request_body_param: Optional[str] = None
    request_model: Optional[str] = None
    request_required_fields: List[str] = None  # type: ignore[assignment]
    request_optional_fields: List[str] = None  # type: ignore[assignment]

    # Facts de contrato de respuesta (para evitar ResponseValidationError y “alucinaciones” en mocks)
    response_model: Optional[str] = None  # import string, p.ej. "app.schemas.ProductResponse"
    response_model_required_fields: List[str] = None  # type: ignore[assignment]
    response_model_optional_fields: List[str] = None  # type: ignore[assignment]

    # Facts para robustecer generación de stubs/mocks (evitar 404 inesperados y modelos vacíos)
    # - statefulness_recommended: indica si el stub debe ser una instancia compartida (p.ej. CRUD: POST->GET/PUT/DELETE)
    # - returns_none_on_not_found: si True, un stub debe devolver None en not-found (NO dict vacío / modelo vacío)
    # - not_found_http_status: normalmente 404 si el handler lo expresa; útil para relajar asserts cuando no hay stub
    statefulness_recommended: Optional[str] = None  # "per_request" | "per_client_fixture" | "stateless"
    returns_none_on_not_found: Optional[bool] = None
    not_found_http_status: Optional[int] = None


@dataclass
class RuntimeFacts:
    """
    Facts agregados del proyecto.

    Nota: `tests_style` es la fuente de verdad para decidir si los tests deben ser sync (TestClient)
    o async (httpx.AsyncClient + pytest.mark.asyncio + fixtures async). Se infiere del CÓDIGO real.

    Campos añadidos para tests herméticos (modo PARCIAL)
    ----------------------------------------------------
    Estos campos NO intentan "resolver" integraciones. Solo describen riesgos de runtime observables
    para que el LLM genere una suite hermética sin tocar infraestructura real.

    - external_symbols_used:
      Símbolos globales referenciados en el código que podrían causar NameError si falta un import
      o si el SDK no está instalado (ej: `google`, `boto3`, `redis`, etc.).
      Uso principal: permitir a tests/conftest inyectar stubs mínimos ANTES de que se ejecuten providers.

    - unsafe_import_modules:
      Módulos que, al importarse, probablemente intenten inicializar IO externo o dependan de SDKs
      opcionales (heurística). Sirve para que el LLM evite importarlos en tests salvo necesidad.

    - allowed_dependency_overrides:
      Lista de FQNs de dependencias de FastAPI que son seguras de overridear (fuente: runtime_contracts).
      Se replica aquí para que el generador de tests no tenga que leer dos artefactos.
    """

    endpoints: List[EndpointRuntimeFacts]
    env_vars_explicit: List[str]
    imports: List[str]

    external_symbols_used: List[str] = None  # type: ignore[assignment]
    unsafe_import_modules: List[str] = None  # type: ignore[assignment]
    allowed_dependency_overrides: List[str] = None  # type: ignore[assignment]

    # "sync"  -> TestClient + fixtures/tests sync (recomendado por defecto)
    # "async" -> AsyncClient + pytest.mark.asyncio + fixtures/tests async (solo si el proyecto lo requiere)
    tests_style: str = "sync"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _json_dumps(data: Any) -> str:
    # No ordenar claves: queremos estabilidad y evitar perder el "shape" esperado
    # por consumidores; además reduce diffs ruidosos.
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def persist_runtime_facts(
    *,
    project_structure: Dict[str, str],
    runtime_facts: RuntimeFacts,
) -> Dict[str, str]:
    """
    Devuelve un patch (path->content) que añade el artefacto al proyecto generado.

    Nota: No escribe a disco directamente; respeta el modelo de materialización del proyecto.
    """
    patched: Dict[str, str] = dict(project_structure)
    patched[RUNTIME_FACTS_PATH] = _json_dumps(runtime_facts.to_dict())
    # aseguramos directorio como paquete de artefactos (no python package)
    # no añadimos __init__.py para no interferir
    return patched


def runtime_facts_in_structure(structure: Dict[str, str]) -> bool:
    return isinstance(structure, dict) and RUNTIME_FACTS_PATH in structure


def load_runtime_facts_from_structure(structure: Dict[str, str]) -> Optional[RuntimeFacts]:
    raw = structure.get(RUNTIME_FACTS_PATH)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None

    try:
        endpoints: List[EndpointRuntimeFacts] = []
        for ep in data.get("endpoints", []) or []:
            calls: List[ObservedCall] = []
            for c in ep.get("observed_calls", []) or []:
                calls.append(
                    ObservedCall(
                        receiver_param=str(c.get("receiver_param")),
                        method_name=str(c.get("method_name")),
                        arg_names=list(c.get("arg_names") or []),
                        awaited=bool(c.get("awaited")),
                    )
                )
            endpoints.append(
                    EndpointRuntimeFacts(
                        path=str(ep.get("path")),
                        method=str(ep.get("method")),
                        module_path=str(ep.get("module_path")),
                        func_name=str(ep.get("func_name")),
                        status_code=ep.get("status_code"),
                        depends=list(ep.get("depends") or []),
                        depends_imports=list(ep.get("depends_imports") or []),
                        injected_params=list(ep.get("injected_params") or []),
                        uses_injected=bool(ep.get("uses_injected")),
                        observed_calls=calls,
                        request_body_param=ep.get("request_body_param"),
                        request_model=ep.get("request_model"),
                        request_required_fields=list(ep.get("request_required_fields") or []),
                        request_optional_fields=list(ep.get("request_optional_fields") or []),
                        response_model=ep.get("response_model"),
                        response_model_required_fields=list(ep.get("response_model_required_fields") or []),
                        response_model_optional_fields=list(ep.get("response_model_optional_fields") or []),
                        statefulness_recommended=ep.get("statefulness_recommended"),
                        returns_none_on_not_found=ep.get("returns_none_on_not_found"),
                        not_found_http_status=ep.get("not_found_http_status"),
                    )
            )
        return RuntimeFacts(
            endpoints=endpoints,
            env_vars_explicit=list(data.get("env_vars_explicit") or []),
            imports=list(data.get("imports") or []),
            external_symbols_used=list(data.get("external_symbols_used") or []),
            unsafe_import_modules=list(data.get("unsafe_import_modules") or []),
            allowed_dependency_overrides=list(data.get("allowed_dependency_overrides") or []),
            tests_style=str(data.get("tests_style") or "sync"),
        )
    except Exception:
        return None


def normalize_posix(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/")))  # noqa: PTH201
