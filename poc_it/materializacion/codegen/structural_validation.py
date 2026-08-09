from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from typing import Any, List

from .models import CodegenIssue, GeneratedFile


def validate_generated_file_structure(
    *,
    generated_file: GeneratedFile,
    file_contract: dict[str, Any],
) -> List[CodegenIssue]:
    issues: List[CodegenIssue] = []
    expected_path = str(file_contract.get("path") or "").replace("\\", "/")
    kind = str(file_contract.get("kind") or "").strip()

    path = generated_file.path.replace("\\", "/")
    content = generated_file.content

    if path != expected_path:
        issues.append(
            CodegenIssue(
                code="CODEGEN_PATH_MISMATCH",
                path=expected_path,
                message=f"Path generado distinto al esperado: {path}",
            )
        )
        return issues

    if kind != "package_init" and not content.strip():
        issues.append(
            CodegenIssue(
                code="CODEGEN_EMPTY_CONTENT",
                path=path,
                message="El contenido generado está vacío.",
            )
        )
        return issues

    if expected_path.endswith(".py") and content.strip():
        try:
            ast.parse(content)
        except SyntaxError as exc:
            issues.append(
                CodegenIssue(
                    code="CODEGEN_SYNTAX_ERROR",
                    path=path,
                    message=f"Error de sintaxis Python: {exc}",
                )
            )

    required_symbols = file_contract.get("required_symbols") or []
    if not isinstance(required_symbols, list):
        required_symbols = []

    if kind == "main":
        if "app" in required_symbols and "app" not in content:
            issues.append(
                CodegenIssue(
                    code="CODEGEN_MAIN_MISSING_APP",
                    path=path,
                    message="main debe definir el símbolo app.",
                )
            )
    elif kind == "router":
        if "api_router" in required_symbols and "api_router" not in content:
            issues.append(
                CodegenIssue(
                    code="CODEGEN_ROUTER_MISSING_API_ROUTER",
                    path=path,
                    message="router debe definir api_router.",
                )
            )
    elif kind == "endpoint":
        if "router = APIRouter()" not in content:
            issues.append(
                CodegenIssue(
                    code="CODEGEN_ENDPOINT_MISSING_ROUTER",
                    path=path,
                    message="endpoint debe definir router = APIRouter().",
                )
            )
        for sym in required_symbols:
            if not isinstance(sym, str) or not sym.strip() or sym == "router":
                continue
            if not sym.isidentifier():
                continue
            pattern = r"(?m)^\s*(async\s+def|def)\s+" + re.escape(sym) + r"\s*\("
            if not re.search(pattern, content):
                issues.append(
                    CodegenIssue(
                        code="CODEGEN_MISSING_REQUIRED_SYMBOL_DEF",
                        path=path,
                        message=f"Falta definición de símbolo requerida: {sym}",
                        symbol=sym,
                    )
                )
    elif kind == "config":
        if "class Settings" not in content:
            issues.append(
                CodegenIssue(
                    code="CODEGEN_CONFIG_MISSING_SETTINGS",
                    path=path,
                    message="config debe definir class Settings.",
                )
            )
        if "def get_settings" not in content:
            issues.append(
                CodegenIssue(
                    code="CODEGEN_CONFIG_MISSING_GET_SETTINGS",
                    path=path,
                    message="config debe definir def get_settings.",
                )
            )

    return issues


def validate_generated_files_against_file_contracts(
    files_generados: list[Mapping[str, Any]],
    file_contracts: list[Mapping[str, Any]],
) -> List[CodegenIssue]:
    files_by_path: dict[str, GeneratedFile] = {}
    for item in files_generados or []:
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path") or "").replace("\\", "/")
        if not path:
            continue
        files_by_path[path] = GeneratedFile(
            path=path,
            content=str(item.get("content") or ""),
        )

    issues: List[CodegenIssue] = []
    for contract in file_contracts or []:
        if not isinstance(contract, Mapping):
            continue
        path = str(contract.get("path") or "").replace("\\", "/")
        if not path:
            continue
        generated_file = files_by_path.get(path)
        if generated_file is None:
            issues.append(
                CodegenIssue(
                    code="missing_file",
                    path=path,
                    message="Archivo no generado",
                )
            )
            continue
        issues.extend(
            validate_generated_file_structure(
                generated_file=generated_file,
                file_contract=dict(contract),
            )
        )
    return issues
