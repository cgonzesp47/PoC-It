from __future__ import annotations

from enum import Enum
from typing import Any


class RestrictionEnforcement(str, Enum):
    TEXT = "text"
    SEMANTIC = "semantic"
    RUNTIME = "runtime"
    EXTERNAL_PRECONDITION = "external_precondition"
    DOCUMENTATION = "documentation"


def parse_restriction_enforcement(
    value: Any,
) -> RestrictionEnforcement | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    try:
        return RestrictionEnforcement(normalized)
    except ValueError:
        return None


def _infer_enforcement_from_structure(
    restriction: dict,
) -> RestrictionEnforcement:
    has_text_matchers = bool(
        restriction.get("must_not_contain")
        or restriction.get("must_contain_any")
    )
    if has_text_matchers:
        return RestrictionEnforcement.TEXT
    return RestrictionEnforcement.SEMANTIC


def migrate_legacy_enforcement(
    restriction: dict,
) -> RestrictionEnforcement:
    raw = str(restriction.get("enforcement") or "").strip().lower()
    parsed = parse_restriction_enforcement(raw)
    if parsed is not None:
        return parsed
    if raw == "code":
        return _infer_enforcement_from_structure(restriction)
    return _infer_enforcement_from_structure(restriction)


__all__ = [
    "RestrictionEnforcement",
    "parse_restriction_enforcement",
    "migrate_legacy_enforcement",
]
