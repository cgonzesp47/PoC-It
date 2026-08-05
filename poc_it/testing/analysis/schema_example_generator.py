from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from poc_it.testing.analysis.fastapi_inspector import SchemaNode


@dataclass(frozen=True)
class GeneratedExample:
    value: Any
    valid: bool
    reason: str


def generate_valid_example(schema: SchemaNode, *, max_depth: int = 6) -> Any:
    return _generate_valid(schema, depth=0, max_depth=max_depth)


def generate_invalid_example(schema: SchemaNode, *, max_depth: int = 6) -> GeneratedExample:
    invalid = _generate_invalid(schema, depth=0, max_depth=max_depth)
    if invalid is not None:
        return invalid
    return GeneratedExample(value=None, valid=False, reason="fallback_invalid_null")


def _generate_valid(schema: SchemaNode, *, depth: int, max_depth: int) -> Any:
    if depth >= max_depth:
        return _generic_value_for_type(schema)

    raw = schema.raw if isinstance(schema.raw, dict) else {}

    if "example" in raw:
        return raw["example"]
    examples = raw.get("examples")
    explicit_example = _pick_explicit_example(examples)
    if explicit_example is not None:
        return explicit_example
    if "default" in raw:
        return raw["default"]
    if "const" in raw:
        return raw["const"]
    if schema.enum:
        return schema.enum[0]

    if schema.one_of:
        return _generate_valid(schema.one_of[0], depth=depth + 1, max_depth=max_depth)
    if schema.any_of:
        return _generate_valid(schema.any_of[0], depth=depth + 1, max_depth=max_depth)
    if schema.all_of:
        merged: Dict[str, Any] = {}
        primitive: Any = None
        for child in schema.all_of:
            child_value = _generate_valid(child, depth=depth + 1, max_depth=max_depth)
            if isinstance(child_value, dict):
                merged.update(child_value)
            elif primitive is None:
                primitive = child_value
        return merged if merged else primitive

    t = _schema_type(schema)
    if t == "string":
        return _valid_string(schema)
    if t == "integer":
        return _valid_integer(schema)
    if t == "number":
        return _valid_number(schema)
    if t == "boolean":
        return True
    if t == "array":
        item = _generate_valid(schema.items or SchemaNode(type="string"), depth=depth + 1, max_depth=max_depth)
        min_items = _int_from_raw(raw.get("minItems"))
        max_items = _int_from_raw(raw.get("maxItems"))
        count = max(1, min_items or 1)
        if max_items is not None:
            count = min(count, max_items)
        return [item for _ in range(count)]
    if t == "object" or schema.properties or schema.required:
        return _valid_object(schema, depth=depth + 1, max_depth=max_depth)

    if schema.nullable:
        return None
    return _generic_value_for_type(schema)


def _generate_invalid(schema: SchemaNode, *, depth: int, max_depth: int) -> Optional[GeneratedExample]:
    if depth >= max_depth:
        return GeneratedExample(value=None, valid=False, reason="depth_limit_invalid")

    t = _schema_type(schema)

    if t == "object" or schema.properties or schema.required or schema.all_of:
        valid = _generate_valid(schema, depth=depth, max_depth=max_depth)
        if isinstance(valid, dict):
            required_keys = _required_keys(schema)
            if required_keys:
                broken = dict(valid)
                broken.pop(required_keys[0], None)
                return GeneratedExample(value=broken, valid=False, reason=f"missing_required:{required_keys[0]}")
            for key, prop in schema.properties.items():
                wrong = dict(valid)
                wrong[key] = _wrong_type_value(prop)
                return GeneratedExample(value=wrong, valid=False, reason=f"wrong_type:{key}")
        return GeneratedExample(value="not-an-object", valid=False, reason="wrong_type:object")

    if t == "array":
        valid_item = _generate_valid(schema.items or SchemaNode(type="string"), depth=depth + 1, max_depth=max_depth)
        raw = schema.raw if isinstance(schema.raw, dict) else {}
        min_items = _int_from_raw(raw.get("minItems"))
        if min_items and min_items > 0:
            return GeneratedExample(value=[], valid=False, reason="minItems")
        return GeneratedExample(value=[_wrong_type_value(schema.items or SchemaNode(type="string"))], valid=False, reason="wrong_type:array_item")

    if t == "string":
        raw = schema.raw if isinstance(schema.raw, dict) else {}
        min_length = _int_from_raw(raw.get("minLength"))
        pattern = raw.get("pattern")
        enum = schema.enum
        const = raw.get("const")
        if const is not None:
            return GeneratedExample(value=f"{const}_invalid", valid=False, reason="const")
        if enum:
            return GeneratedExample(value="invalid-enum-value", valid=False, reason="enum")
        if min_length and min_length > 0:
            return GeneratedExample(value="", valid=False, reason="minLength")
        if isinstance(pattern, str) and pattern:
            return GeneratedExample(value="pattern-mismatch", valid=False, reason="pattern")
        return GeneratedExample(value=123, valid=False, reason="wrong_type:string")

    if t == "integer":
        raw = schema.raw if isinstance(schema.raw, dict) else {}
        minimum = _number_from_raw(raw.get("minimum"))
        maximum = _number_from_raw(raw.get("maximum"))
        if minimum is not None:
            return GeneratedExample(value=int(minimum) - 1, valid=False, reason="minimum")
        if maximum is not None:
            return GeneratedExample(value=int(maximum) + 1, valid=False, reason="maximum")
        return GeneratedExample(value="invalid-integer", valid=False, reason="wrong_type:integer")

    if t == "number":
        raw = schema.raw if isinstance(schema.raw, dict) else {}
        minimum = _number_from_raw(raw.get("minimum"))
        maximum = _number_from_raw(raw.get("maximum"))
        if minimum is not None:
            return GeneratedExample(value=float(minimum) - 1.0, valid=False, reason="minimum")
        if maximum is not None:
            return GeneratedExample(value=float(maximum) + 1.0, valid=False, reason="maximum")
        return GeneratedExample(value="invalid-number", valid=False, reason="wrong_type:number")

    if t == "boolean":
        return GeneratedExample(value="not-a-boolean", valid=False, reason="wrong_type:boolean")

    if schema.one_of:
        valid = _generate_valid(schema.one_of[0], depth=depth + 1, max_depth=max_depth)
        if isinstance(valid, dict):
            valid["__invalid__"] = True
            return GeneratedExample(value=valid, valid=False, reason="oneOf")
        return GeneratedExample(value=None, valid=False, reason="oneOf")

    if schema.any_of:
        return _generate_invalid(schema.any_of[0], depth=depth + 1, max_depth=max_depth)

    return GeneratedExample(value=None, valid=False, reason="generic_invalid")


def _valid_object(schema: SchemaNode, *, depth: int, max_depth: int) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    required_keys = _required_keys(schema)
    for key in required_keys:
        child = schema.properties.get(key)
        if child is None:
            child = _find_property_in_all_of(schema, key)
        if child is not None:
            output[key] = _generate_valid(child, depth=depth + 1, max_depth=max_depth)
    return output


def _required_keys(schema: SchemaNode) -> List[str]:
    keys: List[str] = []
    for key in schema.required:
        if key not in keys:
            keys.append(key)
    for child in schema.all_of:
        for key in _required_keys(child):
            if key not in keys:
                keys.append(key)
    return keys


def _find_property_in_all_of(schema: SchemaNode, key: str) -> Optional[SchemaNode]:
    if key in schema.properties:
        return schema.properties[key]
    for child in schema.all_of:
        found = _find_property_in_all_of(child, key)
        if found is not None:
            return found
    return None


def _schema_type(schema: SchemaNode) -> Optional[str]:
    if schema.type:
        return schema.type
    if schema.properties or schema.required or schema.additional_properties or schema.all_of:
        return "object"
    if schema.items:
        return "array"
    return None


def _pick_explicit_example(examples: Any) -> Any:
    if isinstance(examples, list) and examples:
        return examples[0]
    if isinstance(examples, dict):
        first = next(iter(examples.values()), None)
        if isinstance(first, dict) and "value" in first:
            return first["value"]
        if first is not None:
            return first
    return None


def _valid_string(schema: SchemaNode) -> str:
    raw = schema.raw if isinstance(schema.raw, dict) else {}
    fmt = (schema.format or "").lower().strip()

    if fmt == "email":
        value = "test@example.com"
    elif fmt == "uuid":
        value = "123e4567-e89b-12d3-a456-426614174000"
    elif fmt == "date":
        value = date(2024, 1, 1).isoformat()
    elif fmt in {"date-time", "datetime"}:
        value = datetime(2024, 1, 1, 12, 0, 0).isoformat() + "Z"
    else:
        value = "example"

    pattern = raw.get("pattern")
    if pattern == "^[A-Z]+$":
        value = "ABC"
    elif pattern == "^[0-9]+$":
        value = "123"

    min_length = _int_from_raw(raw.get("minLength"))
    max_length = _int_from_raw(raw.get("maxLength"))

    if min_length is not None and len(value) < min_length:
        value = value + ("x" * (min_length - len(value)))
    if max_length is not None and len(value) > max_length:
        value = value[:max_length] or "x"

    return value


def _valid_integer(schema: SchemaNode) -> int:
    raw = schema.raw if isinstance(schema.raw, dict) else {}
    minimum = _number_from_raw(raw.get("minimum"))
    maximum = _number_from_raw(raw.get("maximum"))
    value = int(minimum) if minimum is not None else 1
    if maximum is not None:
        value = min(value, int(maximum))
    return value


def _valid_number(schema: SchemaNode) -> float:
    raw = schema.raw if isinstance(schema.raw, dict) else {}
    minimum = _number_from_raw(raw.get("minimum"))
    maximum = _number_from_raw(raw.get("maximum"))
    value = float(minimum) if minimum is not None else 1.0
    if maximum is not None:
        value = min(value, float(maximum))
    return value


def _wrong_type_value(schema: SchemaNode) -> Any:
    t = _schema_type(schema)
    if t == "string":
        return 123
    if t == "integer":
        return "wrong-type"
    if t == "number":
        return "wrong-type"
    if t == "boolean":
        return "wrong-type"
    if t == "array":
        return "wrong-type"
    if t == "object":
        return "wrong-type"
    return None


def _generic_value_for_type(schema: SchemaNode) -> Any:
    t = _schema_type(schema)
    if t == "string":
        return "example"
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "array":
        return []
    if t == "object":
        return {}
    return None


def _int_from_raw(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _number_from_raw(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
