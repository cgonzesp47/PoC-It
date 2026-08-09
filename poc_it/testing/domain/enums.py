from __future__ import annotations

from enum import Enum


class TestLevel(str, Enum):
    SMOKE_ONLY = "SMOKE_ONLY"
    OPENAPI_CONTRACT = "OPENAPI_CONTRACT"
    HERMETIC_ENDPOINT_CONTRACT = "HERMETIC_ENDPOINT_CONTRACT"
    SEMANTIC_STATEFUL = "SEMANTIC_STATEFUL"


class TestStrategy(str, Enum):
    __test__ = False

    CONTRACT_FIRST = "contract-first"
    MINIMAL_FALLBACK = "minimal-fallback"


class TestsStyle(str, Enum):
    SYNC = "sync"
    ASYNC = "async"
