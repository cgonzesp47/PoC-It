from __future__ import annotations

"""poc_it.runtime_contracts

Artefacto intermedio "RuntimeContracts" (antes RuntimeFacts).

Objetivo
--------
Generar un contrato determinista post-materialización para:
- Alinear la generación de tests con el wiring real (FastAPI + Depends)
- Garantizar tests 100% herméticos (sin integraciones externas)
- Evitar alucinaciones en overrides/imports

Este contrato está pensado para ser consumido por:
- orquestacion/generacion_tests (suite hermética)
- orquestacion/tests_sanitizer (validación determinista)

Notas
-----
- Se genera DESPUÉS de materializar el proyecto.
- Se persiste dentro del proyecto en `.poc_it/runtime_contracts.json`.
- No ejecuta imports del proyecto; es un artefacto de datos.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional


RUNTIME_CONTRACTS_PATH = ".poc_it/runtime_contracts.json"


@dataclass
class ObservedCall:
    receiver_param: str
    method_name: str
    arg_names: List[str]
    awaited: bool


@dataclass
class EndpointRuntimeContract:
    path: str
    method: str
    module_path: str
    func_name: str
    status_code: Optional[int]

    # DI / wiring
    depends: List[str]
    depends_imports: List[str]
    injected_params: List[str]
    uses_injected: bool
    observed_calls: List[ObservedCall]

    # Request/Response contracts (best-effort)
    request_body_param: Optional[str] = None
    request_model: Optional[str] = None
    request_required_fields: List[str] = None  # type: ignore[assignment]
    request_optional_fields: List[str] = None  # type: ignore[assignment]

    # Request params contracts (best-effort)
    path_params: List[str] = None  # type: ignore[assignment]
    query_params_required: List[str] = None  # type: ignore[assignment]
    query_params_optional: List[str] = None  # type: ignore[assignment]
    header_params_required: List[str] = None  # type: ignore[assignment]
    header_params_optional: List[str] = None  # type: ignore[assignment]
    cookie_params_required: List[str] = None  # type: ignore[assignment]
    cookie_params_optional: List[str] = None  # type: ignore[assignment]
    form_params_required: List[str] = None  # type: ignore[assignment]
    form_params_optional: List[str] = None  # type: ignore[assignment]
    file_params_required: List[str] = None  # type: ignore[assignment]
    file_params_optional: List[str] = None  # type: ignore[assignment]

    response_model: Optional[str] = None
    response_model_required_fields: List[str] = None  # type: ignore[assignment]
    response_model_optional_fields: List[str] = None  # type: ignore[assignment]

    # Response contracts (best-effort) - hints to generate correct asserts
    response_media_type: Optional[str] = None  # e.g. application/json
    response_json_shape: Optional[str] = None  # object|array|string|number|boolean|null|unknown
    response_json_required_keys: List[str] = None  # type: ignore[assignment]

    # Hermetic test hints
    statefulness_recommended: Optional[str] = None  # per_client_fixture|stateless
    returns_none_on_not_found: Optional[bool] = None
    not_found_http_status: Optional[int] = None


@dataclass
class RuntimeContracts:
    endpoints: List[EndpointRuntimeContract]

    # Observables del proyecto
    env_vars_explicit: List[str]
    imports: List[str]

    # Test suite style: por defecto sync (TestClient)
    tests_style: str = "sync"

    # Contrato de hermeticidad
    hermetic: bool = True

    # Modo de generación (best-effort). Útil para políticas.
    generation_mode: Optional[str] = None

    # Lista plana permitida para dependency_overrides (FQNs)
    allowed_dependency_overrides: List[str] = None  # type: ignore[assignment]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not d.get("allowed_dependency_overrides"):
            allowed = set()
            for ep in self.endpoints or []:
                for fqn in ep.depends_imports or []:
                    if isinstance(fqn, str) and fqn.strip():
                        allowed.add(fqn.strip())
            d["allowed_dependency_overrides"] = sorted(allowed)
        return d


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def persist_runtime_contracts(*, project_structure: Dict[str, str], runtime_contracts: RuntimeContracts) -> Dict[str, str]:
    patched: Dict[str, str] = dict(project_structure)
    patched[RUNTIME_CONTRACTS_PATH] = _json_dumps(runtime_contracts.to_dict())
    return patched


def runtime_contracts_in_structure(structure: Dict[str, str]) -> bool:
    return isinstance(structure, dict) and RUNTIME_CONTRACTS_PATH in structure


def load_runtime_contracts_from_structure(structure: Dict[str, str]) -> Optional[RuntimeContracts]:
    raw = structure.get(RUNTIME_CONTRACTS_PATH)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None

    try:
        endpoints: List[EndpointRuntimeContract] = []
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
                EndpointRuntimeContract(
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
                    path_params=list(ep.get("path_params") or []),
                    query_params_required=list(ep.get("query_params_required") or []),
                    query_params_optional=list(ep.get("query_params_optional") or []),
                    header_params_required=list(ep.get("header_params_required") or []),
                    header_params_optional=list(ep.get("header_params_optional") or []),
                    cookie_params_required=list(ep.get("cookie_params_required") or []),
                    cookie_params_optional=list(ep.get("cookie_params_optional") or []),
                    form_params_required=list(ep.get("form_params_required") or []),
                    form_params_optional=list(ep.get("form_params_optional") or []),
                    file_params_required=list(ep.get("file_params_required") or []),
                    file_params_optional=list(ep.get("file_params_optional") or []),
                    response_model=ep.get("response_model"),
                    response_model_required_fields=list(ep.get("response_model_required_fields") or []),
                    response_model_optional_fields=list(ep.get("response_model_optional_fields") or []),
                    response_media_type=ep.get("response_media_type"),
                    response_json_shape=ep.get("response_json_shape"),
                    response_json_required_keys=list(ep.get("response_json_required_keys") or []),
                    statefulness_recommended=ep.get("statefulness_recommended"),
                    returns_none_on_not_found=ep.get("returns_none_on_not_found"),
                    not_found_http_status=ep.get("not_found_http_status"),
                )
            )

        allowed = data.get("allowed_dependency_overrides")
        if not isinstance(allowed, list):
            allowed = []

        return RuntimeContracts(
            endpoints=endpoints,
            env_vars_explicit=list(data.get("env_vars_explicit") or []),
            imports=list(data.get("imports") or []),
            tests_style=str(data.get("tests_style") or "sync"),
            hermetic=bool(data.get("hermetic") if data.get("hermetic") is not None else True),
            generation_mode=(str(data.get("generation_mode")) if data.get("generation_mode") else None),
            allowed_dependency_overrides=[str(x) for x in allowed if str(x).strip()],
        )
    except Exception:
        return None


def normalize_posix(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/")))
