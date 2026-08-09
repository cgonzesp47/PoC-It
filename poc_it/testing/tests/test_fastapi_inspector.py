from poc_it.testing.analysis.fastapi_inspector import (
    OpenAPIInspectorError,
    inspect_fastapi_openapi,
)


def test_each_openapi_operation_produces_endpoint_contract_and_supports_include_router_shape():
    openapi = {
        "openapi": "3.1.0",
        "paths": {
            "/items/{item_id}": {
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer", "format": "int64"},
                    }
                ],
                "get": {
                    "operationId": "getItem",
                    "tags": ["items"],
                    "parameters": [
                        {
                            "name": "expand",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "boolean"},
                        },
                        {
                            "name": "x-trace-id",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "session",
                            "in": "cookie",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"}
                                }
                            },
                        }
                    },
                    "security": [{"bearerAuth": []}],
                }
            },
            "/router-created": {
                "post": {
                    "operationId": "createViaRouter",
                    "deprecated": True,
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ItemCreate"}
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"}
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "Item": {
                    "type": "object",
                    "required": ["id", "name"],
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": ["A", "B"]},
                    },
                },
                "ItemCreate": {
                    "allOf": [
                        {"$ref": "#/components/schemas/BaseItem"},
                        {
                            "type": "object",
                            "required": ["name"],
                            "properties": {
                                "name": {"type": "string"},
                                "metadata": {
                                    "type": "object",
                                    "nullable": True,
                                    "additionalProperties": {"type": "string"},
                                },
                            },
                        },
                    ]
                },
                "BaseItem": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "format": "uri"},
                    },
                },
            }
        },
    }

    report = inspect_fastapi_openapi(
        openapi=openapi,
        runtime_contracts={
            "endpoints": [
                {"method": "GET", "path": "/items/{item_id}", "security_schemes": ["runtimeScheme"]}
            ]
        },
    )

    assert report.errors == []
    assert len(report.endpoints) == 2

    get_item = next(ep for ep in report.endpoints if ep.operation_id == "getItem")
    assert get_item.method == "GET"
    assert get_item.path == "/items/{item_id}"
    assert [p.name for p in get_item.path_params] == ["item_id"]
    assert [p.name for p in get_item.query_params] == ["expand"]
    assert [p.name for p in get_item.headers] == ["x-trace-id"]
    assert [p.name for p in get_item.cookies] == ["session"]
    assert get_item.status_codes == ["200"]
    assert get_item.security_schemes == ["bearerAuth", "runtimeScheme"]
    assert get_item.tags == ["items"]
    assert "#/components/schemas/Item" in get_item.component_refs
    assert get_item.response_schemas["200"].content["application/json"].properties["kind"].enum == ["A", "B"]

    created = next(ep for ep in report.endpoints if ep.operation_id == "createViaRouter")
    assert created.deprecated is True
    assert created.content_types == ["application/json"]
    assert created.request_body is not None
    schema = created.request_body.content["application/json"]
    assert len(schema.all_of) == 2
    assert schema.all_of[1].properties["metadata"].nullable is True
    assert schema.all_of[1].properties["metadata"].additional_properties.type == "string"


def test_ref_resolution_honors_recursion_limit_and_reports_errors():
    openapi = {
        "openapi": "3.1.0",
        "paths": {
            "/recursive": {
                "get": {
                    "operationId": "recursiveExample",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/A"}
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "A": {"$ref": "#/components/schemas/B"},
                "B": {"$ref": "#/components/schemas/A"},
            }
        },
    }

    report = inspect_fastapi_openapi(openapi=openapi, max_ref_depth=3)

    assert report.endpoints == []
    assert len(report.errors) == 1
    assert "Cyclic $ref detected" in report.errors[0]


def test_invalid_openapi_is_reported_not_silenced():
    openapi = {
        "openapi": "3.1.0",
        "paths": {
            "/broken": {
                "get": {
                    "operationId": "brokenOperation",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": "invalid"
                                }
                            },
                        }
                    },
                }
            }
        },
    }

    report = inspect_fastapi_openapi(openapi=openapi)

    assert report.endpoints == []
    assert len(report.errors) == 1
    assert "must define schema" in report.errors[0]


def test_non_dict_openapi_raises_explicit_error():
    try:
        inspect_fastapi_openapi(openapi=None)  # type: ignore[arg-type]
    except OpenAPIInspectorError as exc:
        assert "must be a dict" in str(exc)
    else:
        raise AssertionError("Expected OpenAPIInspectorError")
