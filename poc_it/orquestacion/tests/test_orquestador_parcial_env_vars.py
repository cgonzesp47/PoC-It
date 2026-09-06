"""
Regression tests para `_required_env_var_names_from_spec` / `_merge_env_var_names`.

Estas funciones garantizan que una variable de entorno obligatoria declarada en
`spec.env` (por ejemplo, un campo sin valor por defecto de `pydantic_settings.BaseSettings`)
reciba un valor de relleno durante los tests hermeticos, aunque el extractor AST
(`env_vars_explicit`, basado en `os.getenv`/`os.environ`) no la haya detectado.
"""

from poc_it.orquestacion.orquestador_parcial import (
    _merge_env_var_names,
    _required_env_var_names_from_spec,
)


def test_required_env_var_names_from_spec_filters_required_only():
    spec = {
        "env": [
            {"name": "GOOGLE_DRIVE_FOLDER_ID", "required": True},
            {"name": "OPTIONAL_FLAG", "required": False},
            {"name": "", "required": True},
        ]
    }

    assert _required_env_var_names_from_spec(spec) == ["GOOGLE_DRIVE_FOLDER_ID"]


def test_required_env_var_names_from_spec_handles_missing_or_malformed_input():
    assert _required_env_var_names_from_spec(None) == []
    assert _required_env_var_names_from_spec({}) == []
    assert _required_env_var_names_from_spec({"env": None}) == []
    assert _required_env_var_names_from_spec({"env": ["not-a-dict"]}) == []


def test_merge_env_var_names_deduplicates_and_preserves_order():
    merged = _merge_env_var_names(
        ["API_KEY", "SOME_URL"],
        ["GOOGLE_DRIVE_FOLDER_ID", "API_KEY"],
    )

    assert merged == ["API_KEY", "SOME_URL", "GOOGLE_DRIVE_FOLDER_ID"]


def test_merge_env_var_names_ignores_blank_entries():
    merged = _merge_env_var_names(["", "  ", "REAL_VAR"])

    assert merged == ["REAL_VAR"]
