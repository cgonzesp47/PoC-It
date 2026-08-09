from __future__ import annotations

import ast
from pathlib import Path


def test_testing_package_does_not_import_orchestration() -> None:
    testing_root = Path(__file__).resolve().parents[1]
    violations: list[str] = []

    for file_path in testing_root.rglob("*.py"):
        tree = ast.parse(file_path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("poc_it.orquestacion"):
                        violations.append(f"{file_path}: import {alias.name}")

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("poc_it.orquestacion"):
                    violations.append(f"{file_path}: from {module} import ...")

    assert not violations, "\n".join(violations)
