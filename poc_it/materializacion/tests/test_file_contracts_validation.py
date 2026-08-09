from __future__ import annotations

from poc_it.generador.file_contracts_validation import (
    validate_generated_python_against_file_contracts,
    validate_internal_data_contracts,
)
from poc_it.materializacion.codegen.structural_validation import (
    validate_generated_files_against_file_contracts,
)


def test_validation_detects_missing_required_endpoint_function() -> None:
    file_contracts = [
        {
            "path": "app/api/endpoints/productos.py",
            "kind": "endpoint",
            "required_symbols": ["router", "list_productos"],
        }
    ]
    files_generados = [
        {
            "path": "app/api/endpoints/productos.py",
            "content": "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n# missing list_productos\n",
        }
    ]

    violations = validate_generated_files_against_file_contracts(
        files_generados, file_contracts
    )
    codes = {v.code for v in violations}
    assert "CODEGEN_MISSING_REQUIRED_SYMBOL_DEF" in codes


def test_validation_detects_config_missing_get_settings() -> None:
    file_contracts = [
        {
            "path": "app/core/config.py",
            "kind": "config",
            "required_symbols": ["Settings", "get_settings"],
        }
    ]
    files_generados = [
        {
            "path": "app/core/config.py",
            "content": "class Settings:\n    pass\n\n# missing get_settings\n",
        }
    ]

    violations = validate_generated_files_against_file_contracts(
        files_generados, file_contracts
    )
    codes = {v.code for v in violations}
    assert "CODEGEN_CONFIG_MISSING_GET_SETTINGS" in codes


def test_internal_import_symbol_missing_detected_before_runtime() -> None:
    file_contracts = [
        {
            "path": "app/core/config.py",
            "kind": "config",
            "configuration": [{"key": "RESOURCE_ID"}],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "configuration_access": {
                "module": "app.core.config",
                "provider_symbol": "get_settings",
                "fields": ["RESOURCE_ID"],
            },
        },
    ]
    files_by_path = {
        "app/core/config.py": (
            "class Settings:\n"
            "    pass\n\n"
            "def get_settings():\n"
            "    return Settings()\n"
        ),
        "app/api/endpoints/resource.py": "from app.core.config import settings\n",
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert {v.code for v in violations} == {"INTERNAL_IMPORT_SYMBOL_MISSING"}


def test_valid_internal_import_symbol_passes() -> None:
    file_contracts = [
        {
            "path": "app/core/config.py",
            "kind": "config",
            "configuration": [{"key": "RESOURCE_ID"}],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "configuration_access": {
                "module": "app.core.config",
                "provider_symbol": "get_settings",
                "fields": ["RESOURCE_ID"],
            },
        },
    ]
    files_by_path = {
        "app/core/config.py": (
            "class Settings:\n"
            "    pass\n\n"
            "def get_settings():\n"
            "    return Settings()\n"
        ),
        "app/api/endpoints/resource.py": "from app.core.config import get_settings\n",
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert violations == []


def test_required_symbol_mismatch_detected() -> None:
    file_contracts = [
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.storage",
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                }
            ],
        },
    ]
    files_by_path = {
        "app/integrations/storage.py": "def save_resource(name, content):\n    return None\n",
        "app/api/endpoints/resource.py": (
            "from app.integrations.storage import store_resource\n\n"
            "def create_resource(name, content):\n"
            "    return store_resource(name, content)\n"
        ),
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert {v.code for v in violations} == {"FILE_CONTRACT_SYMBOL_MISSING"}


def test_required_call_signature_mismatch_detected() -> None:
    file_contracts = [
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.storage",
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                }
            ],
        },
    ]
    files_by_path = {
        "app/integrations/storage.py": "def store_resource(name, content):\n    return None\n",
        "app/api/endpoints/resource.py": (
            "from app.integrations.storage import store_resource\n\n"
            "def create_resource(content):\n"
            "    return store_resource(content)\n"
        ),
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert {v.code for v in violations} == {"FILE_CONTRACT_CALL_SIGNATURE_MISMATCH"}


def test_required_call_signature_match_passes() -> None:
    file_contracts = [
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.storage",
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                }
            ],
        },
    ]
    files_by_path = {
        "app/integrations/storage.py": "def store_resource(name, content):\n    return None\n",
        "app/api/endpoints/resource.py": (
            "from app.integrations.storage import store_resource\n\n"
            "def create_resource(name, content):\n"
            "    return store_resource(name, content)\n"
        ),
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert violations == []


def test_required_call_data_flow_supports_positional_arguments() -> None:
    file_contracts = [
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.storage",
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                    "parameters": [
                        {"name": "name", "required": True},
                        {"name": "content", "required": True},
                    ],
                }
            ],
        },
    ]
    files_by_path = {
        "app/integrations/storage.py": "def store_resource(name, content):\n    return None\n",
        "app/api/endpoints/resource.py": (
            "from app.integrations.storage import store_resource\n\n"
            "def create_resource(name, content):\n"
            "    return store_resource(name, content)\n"
        ),
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert violations == []


def test_required_call_data_flow_detects_missing_positional_argument() -> None:
    file_contracts = [
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.storage",
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                    "parameters": [
                        {"name": "name", "required": True},
                        {"name": "content", "required": True},
                    ],
                }
            ],
        },
    ]
    files_by_path = {
        "app/integrations/storage.py": "def store_resource(name, content):\n    return None\n",
        "app/api/endpoints/resource.py": (
            "from app.integrations.storage import store_resource\n\n"
            "def create_resource(name):\n"
            "    return store_resource(name)\n"
        ),
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert {v.code for v in violations} == {
        "FILE_CONTRACT_CALL_SIGNATURE_MISMATCH",
        "FILE_CONTRACT_DATA_FLOW_MISMATCH",
    }


def test_required_call_data_flow_supports_mixed_arguments() -> None:
    file_contracts = [
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.storage",
                    "symbol": "store_resource",
                    "interface_ref": "app.integrations.storage:store_resource",
                    "parameters": [
                        {"name": "name", "required": True},
                        {"name": "content", "required": True},
                    ],
                }
            ],
        },
    ]
    files_by_path = {
        "app/integrations/storage.py": "def store_resource(name, content):\n    return None\n",
        "app/api/endpoints/resource.py": (
            "from app.integrations.storage import store_resource\n\n"
            "def create_resource(name, content):\n"
            "    return store_resource(name, content=content)\n"
        ),
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert violations == []


def test_runtime_credentials_are_not_treated_as_embedded_secret() -> None:
    file_contracts = [
        {
            "path": "app/integrations/client.py",
            "kind": "integration",
            "configuration": [{"key": "API_SECRET", "secret": True}],
        }
    ]
    files_by_path = {
        "app/integrations/client.py": (
            "def build(runtime_credentials):\n"
            "    return some_client(credentials=runtime_credentials)\n"
        )
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert "EMBEDDED_SECRET_FORBIDDEN" not in {v.code for v in violations}


def test_literal_secret_assignment_is_detected_from_contract_metadata() -> None:
    file_contracts = [
        {
            "path": "app/integrations/client.py",
            "kind": "integration",
            "configuration": [{"key": "API_SECRET", "secret": True}],
        }
    ]
    files_by_path = {
        "app/integrations/client.py": 'API_SECRET = "literal-secret"\n'
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert {v.code for v in violations} == {"EMBEDDED_SECRET_FORBIDDEN"}


def test_object_to_string_type_mismatch_is_detected() -> None:
    file_contracts = [
        {
            "path": "app/integrations/producer.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "build_resource",
                    "interface_ref": "app.integrations.producer:build_resource",
                    "returns": {"kind": "object", "fields": {"name": "string"}},
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.producer",
                    "symbol": "build_resource",
                    "interface_ref": "app.integrations.producer:build_resource",
                },
                {
                    "module": "app.integrations.storage",
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                    "parameters": [{"name": "name", "type": "string", "required": True}],
                },
            ],
        },
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                }
            ],
        },
    ]
    files_by_path = {
        "app/integrations/producer.py": "def build_resource():\n    return {'name': 'x'}\n",
        "app/integrations/storage.py": "def consume_name(name):\n    return name\n",
        "app/api/endpoints/resource.py": (
            "from app.integrations.producer import build_resource\n"
            "from app.integrations.storage import consume_name\n\n"
            "def create_resource():\n"
            "    resource = build_resource()\n"
            "    return consume_name(resource)\n"
        ),
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert "FILE_CONTRACT_DATA_FLOW_TYPE_MISMATCH" in {v.code for v in violations}


def test_object_field_type_compatibility_passes() -> None:
    file_contracts = [
        {
            "path": "app/integrations/producer.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "build_resource",
                    "interface_ref": "app.integrations.producer:build_resource",
                    "returns": {"kind": "object", "fields": {"name": "string"}},
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.producer",
                    "symbol": "build_resource",
                    "interface_ref": "app.integrations.producer:build_resource",
                },
                {
                    "module": "app.integrations.storage",
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                    "parameters": [{"name": "name", "type": "string", "required": True}],
                },
            ],
        },
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                }
            ],
        },
    ]
    files_by_path = {
        "app/integrations/producer.py": "def build_resource():\n    return {'name': 'x'}\n",
        "app/integrations/storage.py": "def consume_name(name):\n    return name\n",
        "app/api/endpoints/resource.py": (
            "from app.integrations.producer import build_resource\n"
            "from app.integrations.storage import consume_name\n\n"
            "def create_resource():\n"
            "    resource = build_resource()\n"
            "    return consume_name(resource['name'])\n"
        ),
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert "FILE_CONTRACT_DATA_FLOW_TYPE_MISMATCH" not in {v.code for v in violations}


def test_unknown_data_flow_does_not_raise_type_mismatch() -> None:
    file_contracts = [
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.storage",
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                    "parameters": [{"name": "name", "type": "string", "required": True}],
                },
            ],
        },
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                }
            ],
        },
    ]
    files_by_path = {
        "app/integrations/storage.py": "def consume_name(name):\n    return name\n",
        "app/api/endpoints/resource.py": (
            "from app.integrations.storage import consume_name\n\n"
            "def get_dynamic_value():\n"
            "    return object()\n\n"
            "def create_resource():\n"
            "    return consume_name(get_dynamic_value())\n"
        ),
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert "FILE_CONTRACT_DATA_FLOW_TYPE_MISMATCH" not in {v.code for v in violations}


def test_unresolved_python_symbol_is_detected() -> None:
    file_contracts = [
        {
            "path": "app/integrations/client.py",
            "kind": "integration",
        }
    ]
    files_by_path = {
        "app/integrations/client.py": (
            "def create():\n"
            "    return MissingClient()\n"
        )
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert "UNRESOLVED_PYTHON_SYMBOL" in {v.code for v in violations}


def test_imported_local_and_builtin_symbols_are_not_reported_as_unresolved() -> None:
    file_contracts = [
        {
            "path": "app/integrations/client.py",
            "kind": "integration",
        }
    ]
    files_by_path = {
        "app/integrations/client.py": (
            "from package.module import Client\n\n"
            "def helper(values):\n"
            "    return len(values)\n\n"
            "def create(values):\n"
            "    local_client = Client()\n"
            "    return helper(values), local_client\n"
        )
    }

    violations = validate_generated_python_against_file_contracts(
        files_by_path=files_by_path,
        file_contracts=file_contracts,
    )

    assert "UNRESOLVED_PYTHON_SYMBOL" not in {v.code for v in violations}


def test_pre_codegen_type_validation_is_noop_without_explicit_data_flows() -> None:
    file_contracts = [
        {
            "path": "app/integrations/producer.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "build_resource",
                    "interface_ref": "app.integrations.producer:build_resource",
                    "returns": {"kind": "object", "fields": {"name": "string"}},
                }
            ],
        },
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                    "parameters": [{"name": "name", "type": "string", "required": True}],
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "required_internal_calls": [
                {
                    "module": "app.integrations.producer",
                    "symbol": "build_resource",
                    "interface_ref": "app.integrations.producer:build_resource",
                },
                {
                    "module": "app.integrations.storage",
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                    "parameters": [{"name": "name", "type": "string", "required": True}],
                },
            ],
        },
    ]

    violations = validate_internal_data_contracts(file_contracts=file_contracts)

    assert {v.code for v in violations} == set()


def test_pre_codegen_type_validation_detects_explicit_incompatible_flow() -> None:
    file_contracts = [
        {
            "path": "app/integrations/producer.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "build_resource",
                    "interface_ref": "app.integrations.producer:build_resource",
                    "returns": {"kind": "object", "fields": {"name": "string"}},
                }
            ],
        },
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                    "parameters": [{"name": "name", "type": "string", "required": True}],
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "data_flows": [
                {
                    "source": {
                        "interface_ref": "app.integrations.producer:build_resource",
                    },
                    "target": {
                        "interface_ref": "app.integrations.storage:consume_name",
                        "parameter": "name",
                    },
                }
            ],
        },
    ]

    violations = validate_internal_data_contracts(file_contracts=file_contracts)

    assert {v.code for v in violations} == {"FILE_CONTRACT_PRE_CODEGEN_TYPE_MISMATCH"}


def test_pre_codegen_type_validation_accepts_explicit_compatible_flow() -> None:
    file_contracts = [
        {
            "path": "app/integrations/producer.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "build_name",
                    "interface_ref": "app.integrations.producer:build_name",
                    "returns": {"kind": "string"},
                }
            ],
        },
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                    "parameters": [{"name": "name", "type": "string", "required": True}],
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "data_flows": [
                {
                    "source": {
                        "interface_ref": "app.integrations.producer:build_name",
                    },
                    "target": {
                        "interface_ref": "app.integrations.storage:consume_name",
                        "parameter": "name",
                    },
                }
            ],
        },
    ]

    violations = validate_internal_data_contracts(file_contracts=file_contracts)

    assert violations == []


def test_pre_codegen_type_validation_does_not_fail_for_unknown_shape() -> None:
    file_contracts = [
        {
            "path": "app/integrations/producer.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "build_dynamic",
                    "interface_ref": "app.integrations.producer:build_dynamic",
                    "returns": {"kind": "unknown"},
                }
            ],
        },
        {
            "path": "app/integrations/storage.py",
            "kind": "integration",
            "provided_interfaces": [
                {
                    "symbol": "consume_name",
                    "interface_ref": "app.integrations.storage:consume_name",
                    "parameters": [{"name": "name", "type": "string", "required": True}],
                }
            ],
        },
        {
            "path": "app/api/endpoints/resource.py",
            "kind": "endpoint",
            "data_flows": [
                {
                    "source": {
                        "interface_ref": "app.integrations.producer:build_dynamic",
                    },
                    "target": {
                        "interface_ref": "app.integrations.storage:consume_name",
                        "parameter": "name",
                    },
                }
            ],
        },
    ]

    violations = validate_internal_data_contracts(file_contracts=file_contracts)

    assert violations == []
