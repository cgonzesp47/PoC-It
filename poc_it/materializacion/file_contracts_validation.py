from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence


@dataclass(frozen=True)
class FileContractViolation:
    path: str
    message: str
    code: str = "file_contract_violation"


def validate_generated_files_against_file_contracts(
    files_generados: Sequence[Mapping[str, Any]],
    file_contracts: Sequence[Mapping[str, Any]],
) -> List[FileContractViolation]:
    """Validación estructural mínima post-generación.

    Objetivo:
    - Detectar roturas obvias contra FileContracts (símbolos requeridos ausentes, routers faltantes, etc.).
    - NO valida semántica profunda.

    Contrato:
    - `files_generados`: lista de {path, content}
    - `file_contracts`: lista de dicts (output de file_contracts_to_dict)
    """
    files_by_path: Dict[str, str] = {}
    for f in files_generados or []:
        if not isinstance(f, Mapping):
            continue
        p = str(f.get("path") or "").replace("\\", "/")
        c = str(f.get("content") or "")
        if p:
            files_by_path[p] = c

    violations: List[FileContractViolation] = []

    for fc in file_contracts or []:
        if not isinstance(fc, Mapping):
            continue
        path = str(fc.get("path") or "").replace("\\", "/")
        if not path:
            continue

        if path not in files_by_path:
            violations.append(
                FileContractViolation(path=path, code="missing_file", message="Archivo no generado")
            )
            continue

        content = files_by_path[path]
        kind = str(fc.get("kind") or "").strip()
        required_symbols = fc.get("required_symbols")
        if not isinstance(required_symbols, list):
            required_symbols = []

        violations.extend(_validate_content_for_kind(path=path, kind=kind, content=content))

        if kind == "endpoint":
            # required_symbols: exigir defs para cada función pública requerida (router ya se valida por kind)
            for sym in required_symbols:
                if not isinstance(sym, str) or not sym.strip() or sym.strip() == "router":
                    continue
                if not _has_function_def(content, sym.strip()):
                    violations.append(
                        FileContractViolation(
                            path=path,
                            code="missing_required_symbol",
                            message=f"Falta definición de función requerida: {sym}",
                        )
                    )

    return violations


def _validate_content_for_kind(*, path: str, kind: str, content: str) -> List[FileContractViolation]:
    v: List[FileContractViolation] = []

    if kind == "main":
        if not re.search(r"\bapp\s*[:=]", content):
            v.append(FileContractViolation(path=path, code="main_missing_app", message="main debe definir `app`"))
        if "from app.api.router import api_router" not in content and "import api_router" not in content:
            v.append(
                FileContractViolation(
                    path=path,
                    code="main_missing_api_router_import",
                    message="main debe importar api_router desde app.api.router",
                )
            )
        if "include_router" not in content or "api_router" not in content:
            v.append(
                FileContractViolation(
                    path=path,
                    code="main_missing_include_router",
                    message="main debe incluir api_router con app.include_router(api_router)",
                )
            )

    if kind == "router":
        if "api_router" not in content:
            v.append(
                FileContractViolation(
                    path=path, code="router_missing_api_router", message="router debe definir api_router"
                )
            )

    if kind == "endpoint":
        if "router = APIRouter" not in content and "router=APIRouter" not in content:
            v.append(
                FileContractViolation(
                    path=path,
                    code="endpoint_missing_router",
                    message="endpoint debe definir `router = APIRouter()`",
                )
            )

    if kind == "config":
        if "class Settings" not in content:
            v.append(
                FileContractViolation(
                    path=path,
                    code="config_missing_settings",
                    message="config debe definir class Settings",
                )
            )
        if not re.search(r"\bdef\s+get_settings\b", content):
            v.append(
                FileContractViolation(
                    path=path,
                    code="config_missing_get_settings",
                    message="config debe definir def get_settings",
                )
            )

    return v


def _has_function_def(content: str, name: str) -> bool:
    # tolerante a async/sync
    pat = rf"^\s*(async\s+def|def)\s+{re.escape(name)}\s*\("
    return bool(re.search(pat, content, flags=re.MULTILINE))
