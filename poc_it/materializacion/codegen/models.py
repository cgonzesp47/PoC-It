from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


CodegenStatus = Literal[
    "valid_local",
    "valid_integration_skeleton",
    "invalid",
]


@dataclass(frozen=True)
class GeneratedFile:
    path: str
    content: str


@dataclass(frozen=True)
class CodegenIssue:
    code: str
    path: str
    message: str
    severity: str = "error"
    action_ref: Optional[str] = None
    symbol: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
            "action_ref": self.action_ref,
            "symbol": self.symbol,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class EndpointTestContract:
    method: str
    path: str
    endpoint_file: str
    success_case_required: bool
    internal_calls: List[Dict[str, Any]]
    declared_errors: List[Dict[str, Any]]
    request: Dict[str, Any]
    response: Dict[str, Any]
    external_calls_forbidden: bool = True
    test_kinds: List[Dict[str, Any]] = field(default_factory=list)

    def to_debug_dict(self) -> Dict[str, Any]:
        return {
            "endpoint": f"{self.method} {self.path}",
            "endpoint_file": self.endpoint_file,
            "success_case_required": self.success_case_required,
            "external_calls_forbidden": self.external_calls_forbidden,
            "test_kinds": list(self.test_kinds),
        }


@dataclass
class FileGenerationResult:
    file: Optional[GeneratedFile]
    issues: List[CodegenIssue] = field(default_factory=list)
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.file is not None and not any(
            issue.severity == "error" for issue in self.issues
        )


@dataclass
class ProjectGenerationResult:
    files: List[GeneratedFile] = field(default_factory=list)
    issues: List[CodegenIssue] = field(default_factory=list)
    status: str = "invalid"
    external_connectivity_verified: bool = False
