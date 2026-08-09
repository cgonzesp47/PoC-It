from __future__ import annotations

from typing import Any, Dict, List, Set

from .file_generator import FileContractGenerator, _max_tokens_for_kind
from .models import CodegenIssue, GeneratedFile, ProjectGenerationResult


def sort_file_contracts_for_generation(file_contracts: List[dict]) -> List[dict]:
    kind_order = {
        "package_init": 0,
        "requirements": 1,
        "config": 2,
        "schema": 3,
        "repository": 4,
        "service": 5,
        "integration": 6,
        "endpoint": 7,
        "test": 8,
        "router": 9,
        "main": 10,
        "docs": 11,
    }

    def key(fc: dict) -> tuple[int, str]:
        k = str(fc.get("kind") or "").strip()
        p = str(fc.get("path") or "").strip()
        return (kind_order.get(k, 50), p)

    return sorted(
        [fc for fc in file_contracts if isinstance(fc, dict)],
        key=key,
    )


def related_contracts_for(
    *,
    contract: dict,
    file_contracts: List[dict],
    spec: dict,
    max_related: int = 6,
) -> List[dict]:
    related: List[dict] = []
    path = str(contract.get("path") or "").replace("\\", "/")
    kind = str(contract.get("kind") or "").strip()

    by_path = {
        str(fc.get("path") or "").replace("\\", "/"): fc
        for fc in file_contracts
        if isinstance(fc, dict)
    }
    by_kind: Dict[str, List[dict]] = {}
    for fc in file_contracts:
        if isinstance(fc, dict):
            by_kind.setdefault(str(fc.get("kind") or "").strip(), []).append(fc)

    implementation_files = spec.get("implementation_files") or []
    integration_path_by_ref: Dict[str, str] = {}
    for item in implementation_files:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "").strip() != "integration":
            continue
        ref = str(item.get("integration_ref") or "").strip()
        item_path = str(item.get("path") or "").replace("\\", "/").strip()
        if ref and item_path:
            integration_path_by_ref[ref] = item_path

    if kind == "endpoint":
        for ref in contract.get("integration_refs") or []:
            ref_str = str(ref or "").strip()
            integration_path = integration_path_by_ref.get(ref_str)
            if integration_path and integration_path in by_path and integration_path != path:
                related.append(by_path[integration_path])
    elif kind == "integration":
        related.extend(by_kind.get("config", []))
        related.extend(by_kind.get("requirements", []))
        integration_refs = {
            str(ref or "").strip()
            for ref in (contract.get("integration_refs") or [])
            if str(ref or "").strip()
        }
        for fc in by_kind.get("endpoint", []):
            refs = {
                str(ref or "").strip()
                for ref in (fc.get("integration_refs") or [])
                if str(ref or "").strip()
            }
            if refs.intersection(integration_refs):
                related.append(fc)
    elif kind == "router":
        related.extend(by_kind.get("endpoint", []))
    elif kind == "main":
        related.extend(by_kind.get("router", []))

    out: List[dict] = []
    seen: Set[str] = set()
    for fc in related:
        rp = str(fc.get("path") or "").replace("\\", "/")
        if not rp or rp == path or rp in seen:
            continue
        seen.add(rp)
        out.append(fc)
        if len(out) >= max_related:
            break
    return out


def max_tokens_for_kind(kind: str) -> int:
    return _max_tokens_for_kind(kind)


class ContractFirstCodegen:
    def __init__(
        self,
        *,
        file_generator: FileContractGenerator,
    ) -> None:
        self._file_generator = file_generator

    def generate_project(
        self,
        *,
        spec: Dict[str, Any],
        file_contracts: List[Dict[str, Any]],
        descripcion_global: str,
        contexto_normalizado: Dict[str, Any],
    ) -> ProjectGenerationResult:
        allowed_paths = [
            str(path).replace("\\", "/")
            for path in (spec.get("files") or [])
            if isinstance(path, str)
        ]
        sorted_contracts = sort_file_contracts_for_generation(file_contracts)
        generated_by_path: Dict[str, GeneratedFile] = {}
        issues: List[CodegenIssue] = []

        for contract in sorted_contracts:
            path = str(contract.get("path") or "").replace("\\", "/")
            if not path or path not in allowed_paths:
                continue

            if str(contract.get("kind") or "").strip() == "package_init":
                generated_by_path[path] = GeneratedFile(path=path, content="")
                continue

            result = self._file_generator.generate(
                spec=spec,
                file_contract=contract,
                related_contracts=related_contracts_for(
                    contract=contract,
                    file_contracts=sorted_contracts,
                    spec=spec,
                ),
                descripcion_global=descripcion_global,
                contexto_normalizado=contexto_normalizado,
            )
            issues.extend(result.issues)
            if not result.ok:
                return ProjectGenerationResult(
                    files=_files_in_spec_order(
                        allowed_paths=allowed_paths,
                        generated_by_path=generated_by_path,
                    ),
                    issues=issues,
                    status="invalid",
                )
            assert result.file is not None
            generated_by_path[path] = result.file

        missing = [path for path in allowed_paths if path not in generated_by_path]
        for path in missing:
            issues.append(
                CodegenIssue(
                    code="CODEGEN_REQUIRED_FILE_MISSING",
                    path=path,
                    message="Falta un archivo requerido del SPEC.",
                )
            )

        status = "valid" if not issues and not missing else "invalid"
        return ProjectGenerationResult(
            files=_files_in_spec_order(
                allowed_paths=allowed_paths,
                generated_by_path=generated_by_path,
            ),
            issues=issues,
            status=status,
        )


def _files_in_spec_order(
    *,
    allowed_paths: List[str],
    generated_by_path: Dict[str, GeneratedFile],
) -> List[GeneratedFile]:
    return [generated_by_path[path] for path in allowed_paths if path in generated_by_path]
