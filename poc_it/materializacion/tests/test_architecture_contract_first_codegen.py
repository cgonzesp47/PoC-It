from __future__ import annotations

import ast
from pathlib import Path


def _collect_import_violations(root: Path, forbidden_modules: set[str]) -> list[str]:
    violations: list[str] = []

    for file_path in root.rglob("*.py"):
        tree = ast.parse(file_path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules:
                        violations.append(f"{file_path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in forbidden_modules:
                    violations.append(f"{file_path}: from {module} import ...")

    return violations


def test_generador_artefactos_uses_contract_first_codegen_imports() -> None:
    file_path = Path("poc_it/materializacion/generador_artefactos.py")
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    imported_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_modules.add(module)
            for alias in node.names:
                imported_names.add(alias.name)

    assert "poc_it.materializacion.codegen.file_generator" in imported_modules
    assert "poc_it.materializacion.codegen.orchestrator" in imported_modules
    assert "FileContractGenerator" in imported_names
    assert "ContractFirstCodegen" in imported_names


def test_codegen_has_no_legacy_batch_imports() -> None:
    forbidden_modules = {
        "poc_it.generador.prompts_lotes",
        "poc_it.materializacion.file_contracts_validation",
    }
    production_roots = [
        Path("poc_it/materializacion"),
        Path("poc_it/orquestacion"),
        Path("poc_it/testing"),
    ]

    violations: list[str] = []
    for root in production_roots:
        violations.extend(_collect_import_violations(root, forbidden_modules))

    assert not violations, "\n".join(violations)


def test_materializacion_codegen_package_does_not_import_prompts_lotes() -> None:
    codegen_root = Path("poc_it/materializacion/codegen")
    violations = _collect_import_violations(
        codegen_root,
        {"poc_it.generador.prompts_lotes"},
    )

    assert not violations, "\n".join(violations)
