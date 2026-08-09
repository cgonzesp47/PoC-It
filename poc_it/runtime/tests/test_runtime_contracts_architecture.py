from __future__ import annotations

from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH
import poc_it.testing.domain.models as testing_models


def test_runtime_contracts_path_comes_from_runtime_package() -> None:
    assert RUNTIME_CONTRACTS_PATH == ".poc_it/runtime_contracts.json"


def test_testing_models_do_not_own_runtime_contracts_path() -> None:
    assert not hasattr(
        testing_models,
        "RUNTIME_CONTRACTS_PATH",
    )
