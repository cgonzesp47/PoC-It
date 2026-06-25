from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set


@dataclass(frozen=True)
class FileContract:
    path: str
    kind: str  # main | router | endpoint | config | schema | service | repository | test | docs | requirements | package_init | unknown
    responsibilities: List[str] = field(default_factory=list)
    required_symbols: List[str] = field(default_factory=list)
    allowed_imports: List[str] = field(default_factory=list)
    forbidden_imports: List[str] = field(default_factory=list)
    endpoints: List[Dict[str, Any]] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    env: List[Any] = field(default_factory=list)
    persistence: Dict[str, Any] = field(default_factory=dict)
    test_strategy: Dict[str, Any] = field(default_factory=dict)
    source: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


def file_contracts_to_dict(contracts: List[FileContract]) -> List[dict]:
    return [
        {
            "path": c.path,
            "kind": c.kind,
            "responsibilities": list(c.responsibilities),
            "required_symbols": list(c.required_symbols),
            "allowed_imports": list(c.allowed_imports),
            "forbidden_imports": list(c.forbidden_imports),
            "endpoints": list(c.endpoints),
            "dependencies": list(c.dependencies),
            "env": list(c.env),
            "persistence": dict(c.persistence),
            "test_strategy": dict(c.test_strategy),
            "source": dict(c.source),
            "notes": list(c.notes),
        }
        for c in contracts
    ]


def build_file_contracts_from_spec(spec: dict) -> List[FileContract]:
    if not isinstance(spec, dict):
        raise TypeError("spec must be a dict")

    files = spec.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("spec.files must be a non-empty list")

    # Normalize paths to posix
    spec_files = [str(p).replace("\\", "/") for p in files if p]
    if not spec_files:
        raise ValueError("spec.files has no usable paths")

    endpoints = spec.get("endpoints", [])
    if endpoints is None:
        endpoints = []
    if not isinstance(endpoints, list):
        raise ValueError("spec.endpoints must be a list")

    deps = spec.get("dependencies", []) or []
    env = spec.get("env", []) or []
    persistence = spec.get("persistence", {}) or {}
    test_strategy = spec.get("test_strategy", {}) or {}
    source = spec.get("source", {}) or {}

    # Map endpoints by file
    endpoints_by_file: Dict[str, List[Dict[str, Any]]] = {}
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        f = str(ep.get("file") or "").replace("\\", "/")
        if not f:
            continue
        endpoints_by_file.setdefault(f, []).append(ep)

    contracts: List[FileContract] = []
    seen: Set[str] = set()

    endpoint_files = sorted([f for f in endpoints_by_file.keys() if f])
    endpoint_files_in_spec = [f for f in endpoint_files if f in spec_files]

    for path in spec_files:
        if path in seen:
            raise ValueError(f"duplicate spec.files path: {path}")
        seen.add(path)

        kind = _infer_kind(path)

        # Kind overrides for canonical paths
        if path == "app/main.py":
            kind = "main"
        elif path == "app/api/router.py":
            kind = "router"
        elif path == "app/core/config.py":
            kind = "config"
        elif path.endswith("/__init__.py") or path == "app/__init__.py":
            kind = "package_init"
        elif path == "requirements.txt":
            kind = "requirements"
        elif path.lower().endswith("readme.md") or path == "README.md":
            kind = "docs"
        elif path.startswith("app/api/endpoints/") and path.endswith(".py"):
            kind = "endpoint"

        responsibilities: List[str] = []
        required_symbols: List[str] = []
        allowed_imports: List[str] = []
        forbidden_imports: List[str] = []
        eps_for_file = endpoints_by_file.get(path, [])

        notes: List[str] = []

        if kind == "main":
            required_symbols = ["app"]
            responsibilities = [
                "crear instancia FastAPI",
                "incluir router principal",
                "no contener lógica de negocio",
            ]
            allowed_imports = ["fastapi", "app.api.router"]
        elif kind == "router":
            required_symbols = ["api_router"]
            responsibilities = [
                "crear APIRouter principal",
                "incluir routers de endpoint files",
                "no contener lógica de negocio",
            ]
            # Permitimos fastapi y los módulos endpoint conocidos
            allowed_imports = ["fastapi"] + endpoint_files_in_spec
            notes.append("Debe incluir routers de todos los endpoint files del SPEC")
        elif kind == "endpoint":
            responsibilities = [
                "declarar APIRouter local",
                "implementar handlers para method/path del SPEC",
                "respetar request/response/errors/source",
                "no decidir arquitectura global",
                "no crear conexiones reales en import-time",
            ]
            required_symbols = ["router"]
            for ep in eps_for_file:
                fn = ep.get("func") or ep.get("function") or ep.get("handler")
                if isinstance(fn, str) and fn.strip():
                    required_symbols.append(fn.strip())
            # Dedup preserving order
            required_symbols = list(dict.fromkeys(required_symbols).keys())

            allowed_imports = [
                "fastapi",
                "typing",
                "pydantic",
            ]
            if bool((persistence or {}).get("required")):
                notes.append(
                    "Si persistence.required=true, no abrir DB en endpoint; depender de servicios/repositorios/ports o fakes"
                )
        elif kind == "config":
            required_symbols = ["Settings", "get_settings"]
            responsibilities = [
                "configuración lazy",
                "variables de entorno",
                "no validar credenciales en import-time",
            ]
            allowed_imports = ["pydantic_settings", "functools"]
            if bool((persistence or {}).get("required")):
                notes.append(
                    "Preparar settings de persistencia sin hardcodear vendor si SPEC no lo exige"
                )
        elif kind == "requirements":
            responsibilities = ["declarar dependencias runtime"]
        elif kind == "docs":
            responsibilities = ["documentación del proyecto"]
        elif kind == "package_init":
            responsibilities = ["marcar paquete python"]
        else:
            responsibilities = ["módulo del proyecto"]
            notes.append("Contrato derivado del SPEC sin reglas específicas")

        contracts.append(
            FileContract(
                path=path,
                kind=kind,
                responsibilities=responsibilities,
                required_symbols=required_symbols,
                allowed_imports=allowed_imports,
                forbidden_imports=forbidden_imports,
                endpoints=list(eps_for_file),
                dependencies=list(deps) if kind == "requirements" else [],
                env=list(env) if kind in ("main", "config") else [],
                persistence=dict(persistence) if kind in ("endpoint", "config") else {},
                test_strategy=dict(test_strategy) if kind in ("endpoint", "config", "main") else {},
                source=dict(source) if kind in ("endpoint", "main", "router") else {},
                notes=notes,
            )
        )

    _validate_contracts_against_spec(spec_files=spec_files, endpoints=endpoints, contracts=contracts)

    return contracts


def _infer_kind(path: str) -> str:
    p = (path or "").replace("\\", "/")
    if not p:
        return "unknown"
    if p.endswith("/__init__.py") or p == "app/__init__.py":
        return "package_init"
    if p == "requirements.txt":
        return "requirements"
    if p.lower().endswith("readme.md") or p == "README.md":
        return "docs"
    if p.startswith("tests/") or p.startswith("app/tests/"):
        return "test"
    if p.startswith("app/api/endpoints/") and p.endswith(".py"):
        return "endpoint"
    if p.startswith("app/api/") and p.endswith(".py"):
        return "router"
    if p.startswith("app/core/") and p.endswith(".py"):
        return "config"
    if p.startswith("app/") and p.endswith(".py"):
        return "unknown"
    return "unknown"


def _validate_contracts_against_spec(*, spec_files: List[str], endpoints: List[Any], contracts: List[FileContract]) -> None:
    # 1) Todo file en spec.files debe tener un FileContract
    c_paths = [c.path for c in contracts]
    if set(c_paths) != set(spec_files):
        missing = sorted(list(set(spec_files) - set(c_paths)))
        extra = sorted(list(set(c_paths) - set(spec_files)))
        raise ValueError(f"file contracts mismatch. missing={missing} extra={extra}")

    # 2) No duplicados por path
    if len(c_paths) != len(set(c_paths)):
        raise ValueError("duplicate file contracts by path")

    # 3) Todo endpoint debe aparecer exactamente en un FileContract de kind endpoint
    ep_seen = 0
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        ep_file = str(ep.get("file") or "").replace("\\", "/")
        if not ep_file:
            continue

        if ep_file not in spec_files:
            raise ValueError(f"endpoint.file not present in spec.files: {ep_file}")

        owners = [c for c in contracts if c.kind == "endpoint" and c.path == ep_file and ep in c.endpoints]
        if len(owners) != 1:
            raise ValueError(f"endpoint not owned by exactly 1 file contract: file={ep_file}")
        ep_seen += 1

        # endpoint.func debe aparecer en required_symbols del contrato de su archivo.
        fn = ep.get("func") or ep.get("function") or ep.get("handler")
        if isinstance(fn, str) and fn.strip():
            if fn.strip() not in owners[0].required_symbols:
                raise ValueError(f"endpoint func not in required_symbols: {fn} file={ep_file}")

    # 4) No endpoints huérfanos: ep_seen debe ser igual al número de endpoints dict válidos con file.
    ep_expected = len([ep for ep in endpoints if isinstance(ep, dict) and (ep.get("file") or "")])
    if ep_seen != ep_expected:
        raise ValueError("orphan endpoints detected")
