from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from poc_it.testing.domain.enums import TestStrategy

TEST_PLAN_PATH = ".poc_it/test_plan.json"
TEST_PLAN_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class TestFileArtifact:
    path: str
    strategy: str


@dataclass
class TestGenerationResult:
    __test__ = False

    ok: bool
    strategy: str
    structure_patch: Dict[str, str]
    test_plan: Optional[Any] = None
    validation_report: Optional[Dict[str, Any]] = None
    file_artifacts: List[TestFileArtifact] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def strategy_by_file(self) -> Dict[str, str]:
        return {artifact.path: artifact.strategy for artifact in self.file_artifacts}


@dataclass(frozen=True)
class RuntimeContractsBundle:
    payload: Dict[str, Any]


@dataclass(frozen=True)
class RuntimeFactsBundle:
    payload: Dict[str, Any]


@dataclass(frozen=True)
class EndpointCapabilities:
    startup_available: bool
    openapi_available: bool
    request_schema_complete: bool
    valid_request_generatable: bool
    invalid_request_generatable: bool
    dependencies_discovered: bool
    dependencies_overrideable: bool
    dependency_protocol_known: bool
    response_contract_known: bool
    stateful_candidate: bool


@dataclass(frozen=True)
class FileSpec:
    filename: str
    content: Any
    content_type: str


@dataclass(frozen=True)
class RequestSpec:
    method: str
    path_template: str
    path_params: Dict[str, Any] = field(default_factory=dict)
    query_params: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    json_body: Any = None
    form_data: Dict[str, Any] = field(default_factory=dict)
    files: Dict[str, FileSpec] = field(default_factory=dict)
    expected_status: Optional[int] = None
    allowed_statuses: List[int] = field(default_factory=list)
    response_media_type: Optional[str] = None


@dataclass(frozen=True)
class DependencyBehavior:
    dependency_fqn: str
    method_name: str
    action: str
    value: Any = None
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    exception_status_code: Optional[int] = None

    @property
    def dependency(self) -> str:
        return self.dependency_fqn


@dataclass(frozen=True)
class AssertionSpec:
    kind: str
    target: Optional[str] = None
    expected: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TestCasePlan:
    __test__ = False

    case_id: str
    level: str
    category: str
    request: Optional[RequestSpec]
    dependency_setup: List[DependencyBehavior] = field(default_factory=list)
    assertions: List[AssertionSpec] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioStep:
    operation_id: str
    request: RequestSpec
    capture: Dict[str, str] = field(default_factory=dict)
    assertions: List[AssertionSpec] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioTestPlan:
    scenario_id: str
    name: str
    level: str
    operations: List[ScenarioStep] = field(default_factory=list)
    shared_dependencies: List[str] = field(default_factory=list)
    assertions: List[AssertionSpec] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass(frozen=True)
class EndpointTestPlan:
    operation_id: str
    method: str
    path: str
    capabilities: EndpointCapabilities
    cases: List[TestCasePlan] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class TestPlanEndpoint:
    __test__ = False

    path: str
    method: str
    level: str
    reason: str
    expected_status: Optional[int] = None
    allowed_statuses: List[int] = field(default_factory=list)
    sample_request: Optional[dict] = field(default_factory=dict)
    required_response_keys: List[str] = field(default_factory=list)
    response_media_type: Optional[str] = None
    allow_semantic_asserts: bool = False
    hermetic: bool = True
    deprecated: bool = True


@dataclass(frozen=True)
class TestPlan:
    __test__ = False

    mode: str
    strategy: str = "contract-first"
    schema_version: int = TEST_PLAN_SCHEMA_VERSION
    endpoint_plans: List[EndpointTestPlan] = field(default_factory=list)
    scenario_plans: List[ScenarioTestPlan] = field(default_factory=list)
    endpoints: List[TestPlanEndpoint] = field(default_factory=list)
    totals: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "strategy": self.strategy,
            "totals": self.totals,
            "endpoint_plans": [asdict(ep) for ep in self.endpoint_plans],
            "scenario_plans": [asdict(scenario) for scenario in self.scenario_plans],
            "endpoints": [asdict(ep) for ep in self.endpoints],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"


def build_file_artifacts(paths: List[str], strategy: TestStrategy) -> List[TestFileArtifact]:
    return [TestFileArtifact(path=path, strategy=str(strategy.value)) for path in sorted(paths)]
