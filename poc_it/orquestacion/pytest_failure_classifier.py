from __future__ import annotations
"""
Clasificador determinista de fallos de pytest.

Objetivo
--------
Distinguir el origen del fallo para enrutar la reparación correcta sin relajar
assertions de negocio ni convertir casos felices en checks laxos.
"""

from dataclasses import dataclass
import re
from typing import Optional


@dataclass(frozen=True)
class PytestFailureClass:
    category: str
    archivo: Optional[str]
    test: Optional[str]
    endpoint_or_scenario: Optional[str]
    nivel: str
    causa: str
    accion_permitida: str
    reparable_automaticamente: bool
    missing_module: Optional[str] = None
    called_fixture: Optional[str] = None

    @property
    def kind(self) -> str:
        return self.category

    @property
    def detail(self) -> str:
        return self.causa


_FILE_RE = re.compile(r"_{3,}\s+([^\s]+\.py)")
_TEST_RE = re.compile(r"::([A-Za-z0-9_]+)")
_TEST_HEADER_RE = re.compile(r"_{3,}\s+(test_[A-Za-z0-9_]+)\s+_{3,}")
_ENDPOINT_RE = re.compile(r"(/[A-Za-z0-9_\-/{}/]+)")
_SCENARIO_RE = re.compile(r"(test_scenario_[A-Za-z0-9_]+)")
_MOD_NOT_FOUND_RE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'", re.IGNORECASE)
_IMPORT_ERROR_RE = re.compile(r"(ImportError:|cannot import name|No module named)", re.IGNORECASE)
_FIXTURE_DIRECT_RE = re.compile(r'Fixture "([^"]+)" called directly', re.IGNORECASE)
_FIXTURE_ERROR_RE = re.compile(r"(fixture|fixtures|conftest\.py)", re.IGNORECASE)
_LIFESPAN_RE = re.compile(r"(lifespan|startup event|shutdown event|while handling lifespan)", re.IGNORECASE)
_OPENAPI_RE = re.compile(r"(openapi\.json|test_openapi_document_can_be_generated|test_openapi_contract)", re.IGNORECASE)
_REQUEST_DATA_RE = re.compile(
    r"(422 Unprocessable Entity|status_code == 422|assert\s+422\s*==|field required|Input should be|value is not a valid)",
    re.IGNORECASE,
)
_DEPENDENCY_DOUBLE_RE = re.compile(
    r"(StrictDoubleError|StatefulProtocolError|Unexpected method|Unhandled stateful method|dependency_overrides)",
    re.IGNORECASE,
)
_EXTERNAL_IO_RE = re.compile(
    r"(ConnectionError|ConnectError|ReadTimeout|TimeoutError|Name or service not known|Temporary failure in name resolution|requests\.)",
    re.IGNORECASE,
)
_PLAN_VALIDATION_RE = re.compile(
    r"(SpecValidationError|PLAN_VALIDATION_FAILURE|Unsupported assertion kind|unknown operation|invalid plan|scenario_plans)",
    re.IGNORECASE,
)
_RENDERING_RE = re.compile(
    r"(render_|rendering|IndentationError|SyntaxError|E999|f-string:|unexpected indent)",
    re.IGNORECASE,
)
_APPLICATION_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):[\s\S]+?\n.*app[\\/]", re.IGNORECASE)
_STATUS_ASSERT_RE = re.compile(r"assert\s+response\.status_code\s*==|assert\s+\d+\s*==\s*\d+", re.IGNORECASE)
_RESPONSE_ASSERT_RE = re.compile(r"(JSON_HAS_KEYS|JSON_FIELD_EQUALS|Left contains|Right contains|AssertionError)", re.IGNORECASE)


def classify_pytest_failure(pytest_output: str) -> PytestFailureClass:
    out = pytest_output or ""
    archivo = _extract_file(out)
    test = _extract_test(out)
    endpoint_or_scenario = _extract_endpoint_or_scenario(out)
    nivel = _infer_level(archivo=archivo, test=test)

    module_match = _MOD_NOT_FOUND_RE.search(out)
    if module_match:
        if nivel == "LEVEL_0_STARTUP":
            return _failure(
                category="STARTUP_IMPORT_FAILURE",
                archivo=archivo,
                test=test,
                endpoint_or_scenario=endpoint_or_scenario,
                nivel=nivel,
                causa="startup import failed",
                accion_permitida="repair_generated_import_or_module_reference",
                reparable_automaticamente=True,
                missing_module=module_match.group(1),
            )
        return _failure(
            category="TEST_INFRASTRUCTURE_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel=nivel,
            causa="test collection import failed",
            accion_permitida="repair_generated_import_or_fixture_reference",
            reparable_automaticamente=True,
            missing_module=module_match.group(1),
        )

    if nivel == "LEVEL_0_STARTUP" and _LIFESPAN_RE.search(out):
        return _failure(
            category="STARTUP_LIFESPAN_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel=nivel,
            causa="application lifespan failed during startup",
            accion_permitida="route_to_code_repair",
            reparable_automaticamente=False,
        )

    if _OPENAPI_RE.search(out) and (_APPLICATION_TRACEBACK_RE.search(out) or "openapi" in out.lower()):
        return _failure(
            category="OPENAPI_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel="LEVEL_1_OPENAPI",
            causa="openapi generation or contract exposure failed",
            accion_permitida="route_to_code_repair",
            reparable_automaticamente=False,
        )

    fixture_match = _FIXTURE_DIRECT_RE.search(out)
    if fixture_match:
        return _failure(
            category="TEST_INFRASTRUCTURE_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel=nivel,
            causa="fixture called directly",
            accion_permitida="regenerate_harness_or_fix_fixture_reference",
            reparable_automaticamente=True,
            called_fixture=fixture_match.group(1).strip(),
        )

    if _DEPENDENCY_DOUBLE_RE.search(out):
        return _failure(
            category="DEPENDENCY_DOUBLE_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel=nivel,
            causa="dependency double does not match observed protocol or override setup",
            accion_permitida="repair_dependency_double_or_harness_cleanup",
            reparable_automaticamente=True,
        )

    if _EXTERNAL_IO_RE.search(out):
        return _failure(
            category="EXTERNAL_IO_LEAK",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel=nivel,
            causa="test leaked external I/O instead of using isolated dependency",
            accion_permitida="regenerate_harness_or_override_dependency",
            reparable_automaticamente=True,
        )

    if _PLAN_VALIDATION_RE.search(out):
        return _failure(
            category="PLAN_VALIDATION_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel=nivel,
            causa="invalid or non-renderable test plan",
            accion_permitida="repair_plan_only",
            reparable_automaticamente=True,
        )

    if _RENDERING_RE.search(out) and archivo and "tests/" in archivo.replace("\\", "/"):
        return _failure(
            category="RENDERING_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel=nivel,
            causa="rendered test file is syntactically invalid or malformed",
            accion_permitida="repair_renderer_output",
            reparable_automaticamente=True,
        )

    if _REQUEST_DATA_RE.search(out):
        return _failure(
            category="REQUEST_DATA_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel="LEVEL_2_HTTP" if nivel == "UNKNOWN" else nivel,
            causa="generated request data does not satisfy schema or defaults",
            accion_permitida="repair_request_data_only",
            reparable_automaticamente=True,
        )

    if _APPLICATION_TRACEBACK_RE.search(out):
        return _failure(
            category="APPLICATION_BEHAVIOR_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel=nivel,
            causa="application traceback indicates code behavior failure",
            accion_permitida="route_to_code_repair",
            reparable_automaticamente=False,
        )

    if _STATUS_ASSERT_RE.search(out) or _RESPONSE_ASSERT_RE.search(out):
        return _failure(
            category="APPLICATION_BEHAVIOR_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel=nivel,
            causa="happy-path status/response/assertion mismatch",
            accion_permitida="route_to_code_repair",
            reparable_automaticamente=False,
        )

    if _IMPORT_ERROR_RE.search(out) or _FIXTURE_ERROR_RE.search(out):
        return _failure(
            category="TEST_INFRASTRUCTURE_FAILURE",
            archivo=archivo,
            test=test,
            endpoint_or_scenario=endpoint_or_scenario,
            nivel=nivel,
            causa="test harness or collection infrastructure failure",
            accion_permitida="regenerate_harness_or_fix_fixture_reference",
            reparable_automaticamente=True,
        )

    return _failure(
        category="TEST_INFRASTRUCTURE_FAILURE",
        archivo=archivo,
        test=test,
        endpoint_or_scenario=endpoint_or_scenario,
        nivel=nivel,
        causa="unclassified pytest failure",
        accion_permitida="inspect_test_infrastructure",
        reparable_automaticamente=False,
    )


def _failure(
    *,
    category: str,
    archivo: Optional[str],
    test: Optional[str],
    endpoint_or_scenario: Optional[str],
    nivel: str,
    causa: str,
    accion_permitida: str,
    reparable_automaticamente: bool,
    missing_module: Optional[str] = None,
    called_fixture: Optional[str] = None,
) -> PytestFailureClass:
    return PytestFailureClass(
        category=category,
        archivo=archivo,
        test=test,
        endpoint_or_scenario=endpoint_or_scenario,
        nivel=nivel,
        causa=causa,
        accion_permitida=accion_permitida,
        reparable_automaticamente=reparable_automaticamente,
        missing_module=missing_module,
        called_fixture=called_fixture,
    )


def _extract_file(output: str) -> Optional[str]:
    match = _FILE_RE.search(output)
    return match.group(1) if match else None


def _extract_test(output: str) -> Optional[str]:
    match = _TEST_RE.search(output)
    if match:
        return match.group(1)

    header_match = _TEST_HEADER_RE.search(output)
    if header_match:
        return header_match.group(1)

    return None


def _extract_endpoint_or_scenario(output: str) -> Optional[str]:
    scenario = _SCENARIO_RE.search(output)
    if scenario:
        return scenario.group(1)
    endpoint = _ENDPOINT_RE.search(output)
    return endpoint.group(1) if endpoint else None


def _infer_level(*, archivo: Optional[str], test: Optional[str]) -> str:
    joined = " ".join(part for part in [archivo or "", test or ""])
    lowered = joined.lower()
    if "startup" in lowered:
        return "LEVEL_0_STARTUP"
    if "openapi" in lowered or "request_validation" in lowered:
        return "LEVEL_1_OPENAPI"
    if "http_behavior" in lowered:
        return "LEVEL_2_HTTP"
    if "semantic" in lowered or "scenario" in lowered or "stateful" in lowered:
        return "LEVEL_3_STATEFUL"
    return "UNKNOWN"
