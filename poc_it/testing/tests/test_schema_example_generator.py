from poc_it.testing.analysis.fastapi_inspector import SchemaNode
from poc_it.testing.analysis.schema_example_generator import (
    generate_invalid_example,
    generate_valid_example,
)


def test_valid_example_respects_priority_order_and_string_formats():
    email_schema = SchemaNode(type="string", format="email", raw={})
    uuid_schema = SchemaNode(type="string", format="uuid", raw={})
    date_schema = SchemaNode(type="string", format="date", raw={})
    datetime_schema = SchemaNode(type="string", format="date-time", raw={})

    assert generate_valid_example(email_schema) == "test@example.com"
    assert generate_valid_example(uuid_schema) == "123e4567-e89b-12d3-a456-426614174000"
    assert generate_valid_example(date_schema) == "2024-01-01"
    assert generate_valid_example(datetime_schema) == "2024-01-01T12:00:00Z"

    explicit = SchemaNode(type="string", raw={"example": "from-example", "default": "from-default"})
    by_default = SchemaNode(type="string", raw={"default": "from-default"})
    by_enum = SchemaNode(type="string", enum=["first", "second"], raw={})
    by_const = SchemaNode(type="string", raw={"const": "fixed"})
    by_examples = SchemaNode(type="string", raw={"examples": ["first-example", "second-example"]})

    assert generate_valid_example(explicit) == "from-example"
    assert generate_valid_example(by_default) == "from-default"
    assert generate_valid_example(by_enum) == "first"
    assert generate_valid_example(by_const) == "fixed"
    assert generate_valid_example(by_examples) == "first-example"


def test_valid_example_respects_numeric_and_collection_constraints():
    integer_schema = SchemaNode(type="integer", raw={"minimum": 3, "maximum": 8})
    number_schema = SchemaNode(type="number", raw={"minimum": 2.5, "maximum": 8.5})
    string_schema = SchemaNode(type="string", raw={"minLength": 8, "maxLength": 8, "pattern": "^[A-Z]+$"})
    array_schema = SchemaNode(
        type="array",
        items=SchemaNode(type="integer", raw={"minimum": 5}),
        raw={"minItems": 2, "maxItems": 4},
    )

    assert generate_valid_example(integer_schema) == 3
    assert generate_valid_example(number_schema) == 2.5
    assert generate_valid_example(string_schema) == "ABCxxxxx"
    assert generate_valid_example(array_schema) == [5, 5]


def test_valid_example_builds_required_object_fields_recursively_and_deterministically():
    schema = SchemaNode(
        type="object",
        required=["name", "age", "flags"],
        properties={
            "name": SchemaNode(type="string", raw={"minLength": 7}),
            "age": SchemaNode(type="integer", raw={"minimum": 18}),
            "flags": SchemaNode(
                type="array",
                items=SchemaNode(type="boolean", raw={}),
                raw={"minItems": 1},
            ),
            "ignored_optional": SchemaNode(type="string", raw={}),
        },
        raw={},
    )

    example = generate_valid_example(schema)

    assert example == {
        "name": "example",
        "age": 18,
        "flags": [True],
    }
    assert generate_valid_example(schema) == example


def test_valid_example_supports_allof_and_depth_limit():
    recursive_child = SchemaNode(
        type="object",
        required=["child"],
        properties={},
        raw={},
    )
    recursive_child.properties["child"] = recursive_child  # type: ignore[misc]

    schema = SchemaNode(
        all_of=[
            SchemaNode(
                type="object",
                required=["id"],
                properties={"id": SchemaNode(type="integer", raw={"minimum": 1})},
                raw={},
            ),
            SchemaNode(
                type="object",
                required=["label"],
                properties={"label": SchemaNode(type="string", raw={})},
                raw={},
            ),
        ],
        raw={},
    )

    assert generate_valid_example(schema) == {"id": 1, "label": "example"}
    assert generate_valid_example(recursive_child, max_depth=2) == {"child": {}}


def test_invalid_example_removes_required_or_breaks_type_or_constraint():
    object_schema = SchemaNode(
        type="object",
        required=["name", "age"],
        properties={
            "name": SchemaNode(type="string", raw={}),
            "age": SchemaNode(type="integer", raw={"minimum": 18}),
        },
        raw={},
    )
    string_schema = SchemaNode(type="string", raw={"minLength": 3})
    number_schema = SchemaNode(type="number", raw={"maximum": 2.0})
    boolean_schema = SchemaNode(type="boolean", raw={})

    invalid_object = generate_invalid_example(object_schema)
    invalid_string = generate_invalid_example(string_schema)
    invalid_number = generate_invalid_example(number_schema)
    invalid_boolean = generate_invalid_example(boolean_schema)

    assert invalid_object.reason == "missing_required:name"
    assert invalid_object.value == {"age": 18}

    assert invalid_string.reason == "minLength"
    assert invalid_string.value == ""

    assert invalid_number.reason == "maximum"
    assert invalid_number.value == 3.0

    assert invalid_boolean.reason == "wrong_type:boolean"
    assert invalid_boolean.value == "not-a-boolean"


def test_invalid_example_breaks_array_and_enum_constraints_before_business_logic():
    array_schema = SchemaNode(
        type="array",
        items=SchemaNode(type="string", raw={}),
        raw={"minItems": 1},
    )
    enum_schema = SchemaNode(type="string", enum=["draft", "published"], raw={})
    const_schema = SchemaNode(type="string", raw={"const": "fixed"})

    invalid_array = generate_invalid_example(array_schema)
    invalid_enum = generate_invalid_example(enum_schema)
    invalid_const = generate_invalid_example(const_schema)

    assert invalid_array.reason == "minItems"
    assert invalid_array.value == []

    assert invalid_enum.reason == "enum"
    assert invalid_enum.value == "invalid-enum-value"

    assert invalid_const.reason == "const"
    assert invalid_const.value == "fixed_invalid"
