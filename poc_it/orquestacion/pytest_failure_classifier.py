from __future__ import annotations
"""
Clasificador de fallos de pytest en "clases" estables.

Objetivo
--------
Evitar una lista infinita de fixers deterministas y mejorar convergencia del loop LLM:

- Clasificar el fallo en un número ACOTADO de categorías (import/collection, harness, async/sync,
  request-shape, response-shape, assertion/status, runtime(app)).
- Con la clase decidimos qué "sub-loop" ejecutar (C1 harness vs C2 asserts) y qué reglas/gates aplicar.

Este módulo NO arregla nada. Solo clasifica.
"""

from dataclasses import dataclass
import re
from typing import Optional

# -------------------------
# Patterns por clase
# -------------------------

# collection/import/deps
_MOD_NOT_FOUND_RE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'", re.IGNORECASE)
_IMPORT_ERROR_RE = re.compile(r"(ImportError:|cannot import name|No module named)", re.IGNORECASE)

# harness/fixtures/overrides
_FIXTURE_CALLED_DIRECTLY_RE = re.compile(r'Fixture "([^"]+)" called directly', re.IGNORECASE)
_DEP_OVERRIDES_RE = re.compile(r"dependency_overrides", re.IGNORECASE)
_PYTEST_FIXTURE_ERROR_RE = re.compile(r"(fixture|fixtures)", re.IGNORECASE)

# async/sync mismatches (TestClient + async)
_ASYNC_FIXTURE_RE = re.compile(r"(requested an async fixture|async fixture|pytest-asyncio)", re.IGNORECASE)
_AWAIT_RESPONSE_RE = re.compile(r"object Response can't be used in 'await' expression", re.IGNORECASE)

# request/response shapes
_STATUS_422_RE = re.compile(r"status_code == 422|422 Unprocessable Entity|assert\\s+422\\s*==", re.IGNORECASE)
_RESPONSE_VALIDATION_RE = re.compile(r"(ResponseValidationError|value is not a valid dict|field required)", re.IGNORECASE)

# assertions/status mismatches
_ASSERTION_ERROR_RE = re.compile(r"AssertionError", re.IGNORECASE)
_STATUS_MISMATCH_RE = re.compile(r"assert\\s+response\\.status_code\\s*==|assert\\s+\\d+\\s*==\\s*\\d+", re.IGNORECASE)
_DIFF_DICT_RE = re.compile(r"(Left contains|Right contains|Omitting \\d+ identical items)", re.IGNORECASE)

# runtime/app tracebacks
_APP_TRACEBACK_RE = re.compile(r"Traceback \\(most recent call last\\):[\\s\\S]+?\\napp[\\\\/]", re.IGNORECASE)


@dataclass(frozen=True)
class PytestFailureClass:
    kind: str
    detail: str
    missing_module: Optional[str] = None
    called_fixture: Optional[str] = None


def classify_pytest_failure(pytest_output: str) -> PytestFailureClass:
    out = pytest_output or ""

    m = _MOD_NOT_FOUND_RE.search(out)
    if m:
        return PytestFailureClass(kind="COLLECTION_IMPORT", detail="ModuleNotFoundError", missing_module=m.group(1))

    if _FIXTURE_CALLED_DIRECTLY_RE.search(out):
        fx = _FIXTURE_CALLED_DIRECTLY_RE.search(out)
        return PytestFailureClass(
            kind="HARNESS_FIXTURES",
            detail='Fixture called directly',
            called_fixture=(fx.group(1).strip() if fx else None),
        )

    if _ASYNC_FIXTURE_RE.search(out) or _AWAIT_RESPONSE_RE.search(out):
        return PytestFailureClass(kind="ASYNC_SYNC", detail="async fixture/test used with sync TestClient")

    if _STATUS_422_RE.search(out):
        return PytestFailureClass(kind="REQUEST_SHAPE", detail="422 request shape mismatch")

    if _RESPONSE_VALIDATION_RE.search(out):
        return PytestFailureClass(kind="RESPONSE_SHAPE", detail="ResponseValidationError / schema mismatch")

    if _APP_TRACEBACK_RE.search(out):
        return PytestFailureClass(kind="RUNTIME_APP", detail="traceback in app/** (likely code bug)")

    if _ASSERTION_ERROR_RE.search(out) or _STATUS_MISMATCH_RE.search(out) or _DIFF_DICT_RE.search(out):
        return PytestFailureClass(kind="ASSERTIONS", detail="assert/status mismatch")

    if _IMPORT_ERROR_RE.search(out):
        return PytestFailureClass(kind="COLLECTION_IMPORT", detail="import/collection error")

    # fallback conservador
    return PytestFailureClass(kind="UNKNOWN", detail="unclassified pytest failure")
