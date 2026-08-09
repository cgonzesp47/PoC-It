from .models import (
    CodegenIssue,
    FileGenerationResult,
    GeneratedFile,
    ProjectGenerationResult,
)
from .parsing import parse_single_file_response
from .structural_validation import (
    validate_generated_file_structure,
    validate_generated_files_against_file_contracts,
)

__all__ = [
    "CodegenIssue",
    "ContractFirstCodegen",
    "FileGenerationResult",
    "GeneratedFile",
    "ProjectGenerationResult",
    "max_tokens_for_kind",
    "parse_single_file_response",
    "related_contracts_for",
    "sort_file_contracts_for_generation",
    "validate_generated_file_structure",
    "validate_generated_files_against_file_contracts",
]


def __getattr__(name: str):
    if name in {
        "ContractFirstCodegen",
        "max_tokens_for_kind",
        "related_contracts_for",
        "sort_file_contracts_for_generation",
    }:
        from .orchestrator import (
            ContractFirstCodegen,
            max_tokens_for_kind,
            related_contracts_for,
            sort_file_contracts_for_generation,
        )

        exports = {
            "ContractFirstCodegen": ContractFirstCodegen,
            "max_tokens_for_kind": max_tokens_for_kind,
            "related_contracts_for": related_contracts_for,
            "sort_file_contracts_for_generation": sort_file_contracts_for_generation,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
