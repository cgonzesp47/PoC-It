from __future__ import annotations

from poc_it.orquestacion.tests_harness import (
    HarnessPlan as LegacyHarnessPlan,
    build_harness_plan as legacy_build_harness_plan,
    render_conftest_py as legacy_render_conftest_py,
)
from poc_it.testing.rendering.tests_harness import (
    HarnessPlan,
    build_harness_plan,
    render_conftest_py,
)


def test_legacy_harness_reexports_canonical_functions():
    assert LegacyHarnessPlan is HarnessPlan
    assert legacy_build_harness_plan is build_harness_plan
    assert legacy_render_conftest_py is render_conftest_py
