import json

from poc_it.testing.planning.test_plan_builder import build_test_plan_for_generation
from poc_it.testing.rendering.suite_renderer import render_suite_from_plan
from poc_it.testing.tests.test_legacy_characterization_fixtures import all_characterization_fixtures


def _normalize_text(value: str) -> str:
    return value.replace("\\", "/").replace("\r\n", "\n")


def _normalize_json(data):
    raw = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return _normalize_text(raw)


def _snapshot_for_fixture(fixture):
    planning = build_test_plan_for_generation(
        project_structure=fixture.project_structure,
        runtime_contracts=fixture.runtime_contracts,
        runtime_facts=fixture.runtime_facts,
        spec={"mode": "PARCIAL"},
        mode="PARCIAL",
        project_name=fixture.name,
    )
    rendered = render_suite_from_plan(
        structure_with_plan=planning.structure_with_plan,
        runtime_contracts=fixture.runtime_contracts,
        runtime_facts=fixture.runtime_facts,
        plan=planning.plan,
        project_name=fixture.name,
    )
    return {
        "name": fixture.name,
        "max_level": fixture.max_level,
        "expected_pytest_result": fixture.expected_pytest_result,
        "runtime_contracts": _normalize_json(fixture.runtime_contracts),
        "openapi": _normalize_json((fixture.runtime_contracts or {}).get("openapi") or {}),
        "plan": _normalize_json(json.loads(planning.structure_with_plan[".poc_it/test_plan.json"])),
        "tests": {path: _normalize_text(content) for path, content in sorted(rendered.patch.items())},
        "validation_report": _normalize_json(rendered.validation_report),
    }


def test_characterization_catalog_contains_ten_fastapi_pocs():
    fixtures = all_characterization_fixtures()

    assert len(fixtures) == 10
    assert {fixture.name for fixture in fixtures} == {
        "api_sin_dependencias",
        "crud_dependencia_repositorio",
        "endpoint_con_get_db",
        "cliente_http_externo",
        "endpoint_async",
        "endpoint_autenticacion",
        "path_y_query_params",
        "body_anidado",
        "multipart_upload",
        "api_con_lifespan",
    }


def test_characterization_snapshots_are_hermetic_and_portable():
    snapshots = [_snapshot_for_fixture(fixture) for fixture in all_characterization_fixtures()]

    for snapshot in snapshots:
        assert snapshot["expected_pytest_result"] == "pass"
        assert snapshot["max_level"] in {
            "SMOKE_ONLY",
            "OPENAPI_CONTRACT",
            "HERMETIC_ENDPOINT_CONTRACT",
            "SEMANTIC_STATEFUL",
        }

        combined = "\n".join(
            [snapshot["runtime_contracts"], snapshot["openapi"], snapshot["plan"], snapshot["validation_report"]]
            + list(snapshot["tests"].values())
        )

        assert "http://" not in combined, snapshot["name"]
        assert "https://" not in combined, snapshot["name"]
        assert "C:\\" not in combined
        assert "/tmp/" not in combined
        assert "\\\\" not in combined


def test_characterization_snapshots_have_explicit_outputs():
    snapshots = {snapshot["name"]: snapshot for snapshot in [_snapshot_for_fixture(f) for f in all_characterization_fixtures()]}

    assert "tests/test_startup.py" in snapshots["api_sin_dependencias"]["tests"]
    assert "tests/test_openapi_contract.py" in snapshots["path_y_query_params"]["tests"]

    report = json.loads(snapshots["endpoint_con_get_db"]["validation_report"])
    assert report["overrides"]["has_get_db_override"] is True
