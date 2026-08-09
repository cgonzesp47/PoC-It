from poc_it.analisis.normalizador_contexto import (
    _build_technology_signal_index,
    _collect_external_mentions,
    _technology_identity,
    _validate_integration_technology_refs,
)
from poc_it.modulos.models import PlantillaUsuario


def _plantilla(tecnologias: str = "") -> PlantillaUsuario:
    return PlantillaUsuario(
        nombre="PoC",
        problema="Problema",
        usuarios="Usuario",
        funcionalidades="Funcionalidad",
        limites="Límite",
        tecnologias=tecnologias,
    )


def test_technology_identity():
    assert _technology_identity("ADC") == "adc"
    assert _technology_identity("adc (application-default-credentials)") == "adc"
    assert _technology_identity("  foo  ") == "foo"


def test_technology_alias_parentheses_does_not_duplicate():
    technology_signals = [
        {
            "name": "adc",
            "category": "auth",
            "role": "Default credentials",
            "evidence": "adc (application-default-credentials)",
            "confidence": "explicit",
        }
    ]
    mentions = _collect_external_mentions(
        {"technology_signals": technology_signals},
        _plantilla("adc (application-default-credentials)"),
    )

    assert not any(
        mention["id"] == "adc_application_default_credentials"
        for mention in mentions
    )
    assert all(
        mention.get("technology_ref") != "adc (application-default-credentials)"
        for mention in mentions
    )


def test_build_technology_signal_index_uses_canonical_identity():
    technology_signals = [
        {
            "name": "adc",
            "category": "auth",
            "role": "Default credentials",
            "evidence": "adc (application-default-credentials)",
            "confidence": "explicit",
        }
    ]

    index = _build_technology_signal_index(technology_signals)

    assert index["adc"]["name"] == "adc"


def test_framework_does_not_become_integration():
    mentions = _collect_external_mentions(
        {
            "technology_signals": [
                {
                    "name": "fastapi",
                    "category": "framework",
                    "role": "Web framework",
                    "evidence": "fastapi",
                    "confidence": "explicit",
                }
            ]
        },
        _plantilla("fastapi"),
    )

    assert not any(mention["id"] == "fastapi" for mention in mentions)


def test_runtime_does_not_become_integration():
    mentions = _collect_external_mentions(
        {
            "technology_signals": [
                {
                    "name": "uvicorn",
                    "category": "runtime",
                    "role": "ASGI server",
                    "evidence": "uvicorn",
                    "confidence": "explicit",
                }
            ]
        },
        _plantilla("uvicorn"),
    )

    assert not any(mention["id"] == "uvicorn" for mention in mentions)


def test_auth_does_not_become_integration():
    mentions = _collect_external_mentions(
        {
            "technology_signals": [
                {
                    "name": "default_credentials",
                    "category": "auth",
                    "role": "Auth mechanism",
                    "evidence": "default_credentials",
                    "confidence": "explicit",
                }
            ]
        },
        _plantilla("default_credentials"),
    )

    assert not any(
        mention["id"] == "default_credentials"
        for mention in mentions
    )


def test_functional_external_system_can_remain_integration():
    technology_signals = [
        {
            "name": "document_api",
            "category": "external_api",
            "role": "External document service",
            "evidence": "document_api",
            "confidence": "explicit",
        }
    ]

    mentions = _collect_external_mentions(
        {"technology_signals": technology_signals},
        _plantilla("document_api"),
    )

    assert any(mention["id"] == "document_api" for mention in mentions)
    assert any(mention.get("technology_ref") == "document_api" for mention in mentions)
    assert _validate_integration_technology_refs(
        technology_signals,
        [
            {
                "id": "document_api",
                "technology_refs": ["document_api"],
            }
        ],
    ) == []


def test_combined_stack_does_not_create_integration_per_technology():
    technology_signals = [
        {
            "name": "framework_x",
            "category": "framework",
            "role": "Framework",
            "evidence": "framework_x",
            "confidence": "explicit",
        },
        {
            "name": "runtime_x",
            "category": "runtime",
            "role": "Runtime",
            "evidence": "runtime_x",
            "confidence": "explicit",
        },
        {
            "name": "client_library_x",
            "category": "library",
            "role": "Client library",
            "evidence": "client_library_x",
            "confidence": "explicit",
        },
        {
            "name": "auth_library_x",
            "category": "library",
            "role": "Auth library",
            "evidence": "auth_library_x",
            "confidence": "explicit",
        },
        {
            "name": "deployment_x",
            "category": "unknown",
            "role": "Deployment platform",
            "evidence": "deployment_x",
            "confidence": "explicit",
        },
        {
            "name": "auth_mechanism_x",
            "category": "auth",
            "role": "Default mechanism",
            "evidence": "auth_mechanism_x (default mechanism)",
            "confidence": "explicit",
        },
        {
            "name": "external_api_x",
            "category": "external_api",
            "role": "Functional external system",
            "evidence": "external_api_x",
            "confidence": "explicit",
        },
    ]

    mentions = _collect_external_mentions(
        {"technology_signals": technology_signals},
        _plantilla(
            "framework_x, runtime_x, client_library_x, auth_library_x, "
            "deployment_x, auth_mechanism_x (default mechanism), external_api_x"
        ),
    )

    ids = {mention["id"] for mention in mentions}
    assert "external_api_x" in ids
    assert "auth_mechanism_x_default_mechanism" not in ids
    assert "framework_x" not in ids
    assert "runtime_x" not in ids
    assert len(mentions) == 1

    assert _validate_integration_technology_refs(
        technology_signals,
        [
            {
                "id": "external_api_x",
                "technology_refs": ["external_api_x"],
            }
        ],
    ) == []


def test_adc_regression_uses_canonical_signal_name():
    technology_signals = [
        {
            "name": "adc",
            "category": "auth",
            "role": "Application Default Credentials para autenticación",
            "evidence": "adc (application-default-credentials)",
            "confidence": "explicit",
        }
    ]

    mentions = _collect_external_mentions(
        {"technology_signals": technology_signals},
        _plantilla("adc, adc (application-default-credentials)"),
    )

    assert any(signal["name"] == "adc" for signal in technology_signals)
    assert not any(
        mention["id"] == "adc_application_default_credentials"
        for mention in mentions
    )
    assert not any(
        mention.get("technology_ref") == "adc (application-default-credentials)"
        for mention in mentions
    )
