from __future__ import annotations

import json
from unittest.mock import patch

from poc_it.orquestacion.runtime_probe import (
    RuntimeProbeResult,
    _select_probe_endpoints,
    run_runtime_probe,
)
from poc_it.runtime.runtime_contracts import RUNTIME_CONTRACTS_PATH


def test_zero_dependency_endpoints_are_included():
    """Regresión: un endpoint sin dependencias (p.ej. GET /health) se saltaba por completo en
    el probe, aunque sea el caso más seguro de probar (nada externo que mockear)."""
    endpoints = [{"path": "/health", "method": "GET", "depends_imports": []}]

    selected = _select_probe_endpoints(endpoints, allow=None, max_endpoints=25)

    assert selected == endpoints


def test_endpoint_with_non_overrideable_dependency_is_still_excluded():
    """Un endpoint con una dependencia que NO está en la allowlist sigue excluido: probarlo de
    verdad podría llamar a un servicio externo real."""
    endpoints = [
        {
            "path": "/upload",
            "method": "POST",
            "depends_imports": ["app.integrations.google_drive.build_client"],
        }
    ]

    selected = _select_probe_endpoints(endpoints, allow=[], max_endpoints=25)

    assert selected == []


def test_endpoint_with_overrideable_dependency_is_included():
    dep = "app.integrations.google_drive.build_client"
    endpoints = [{"path": "/upload", "method": "POST", "depends_imports": [dep]}]

    selected = _select_probe_endpoints(endpoints, allow=[dep], max_endpoints=25)

    assert selected == endpoints


def test_max_endpoints_cap_still_respected():
    endpoints = [{"path": f"/e{i}", "method": "GET", "depends_imports": []} for i in range(5)]

    selected = _select_probe_endpoints(endpoints, allow=None, max_endpoints=2)

    assert len(selected) == 2


def test_default_max_endpoints_covers_typical_poc_size():
    """Regresión: el límite por defecto (antes 3) hacía que un proyecto con más de 3 endpoints
    dejara los últimos sin probar nunca. Una PoC típica tiene bastantes menos de 25."""
    import inspect

    sig = inspect.signature(run_runtime_probe)
    assert sig.parameters["max_endpoints"].default >= 10


def test_probe_reports_not_ok_when_an_endpoint_fails():
    """Regresión: `ok` solo reflejaba si el propio script del probe se caía (returncode != 0);
    un endpoint que respondía >=500 o lanzaba una excepción se registraba en `errors` pero nunca
    se propagaba a `ok`, así que ningún fallo real de endpoint llegaba a activar reparación."""
    fake_completed = type(
        "FakeCompleted",
        (),
        {
            "returncode": 0,
            "stdout": (
                "ENDPOINT_FACTS_JSON: {}\n"
                "STUB_SIGNATURES_JSON: {\"deps\": {}}\n"
                "ENDPOINT_ERRORS_JSON: "
                + json.dumps(
                    {
                        "errors": [
                            {
                                "method": "POST",
                                "path": "/upload",
                                "status_code": 500,
                                "detail": "Internal Server Error",
                            }
                        ]
                    }
                )
            ),
            "stderr": "",
        },
    )()

    with patch("poc_it.orquestacion.runtime_probe.subprocess.run", return_value=fake_completed):
        result = run_runtime_probe(
            project_dir=".",
            estructura={},
            python_executable="python",
        )

    assert isinstance(result, RuntimeProbeResult)
    assert result.ok is False
    assert "/upload" in result.detail


def test_probe_seeds_dummy_values_for_required_env_vars(monkeypatch):
    """Regresión: una PoC con una variable de entorno legítimamente obligatoria (p.ej.
    GOOGLE_DRIVE_FOLDER_ID) fallaba SIEMPRE el probe con un ValidationError de
    pydantic-settings ('Field required'), aunque el código generado fuera correcto — un falso
    positivo, porque el probe no rellenaba ninguna variable de entorno (a diferencia del arnés
    de tests, que sí lo hace vía `_ensure_env`)."""
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    monkeypatch.setenv("ALREADY_SET_VAR", "real-value")

    fake_completed = type(
        "FakeCompleted",
        (),
        {
            "returncode": 0,
            "stdout": "ENDPOINT_FACTS_JSON: {}\nSTUB_SIGNATURES_JSON: {\"deps\": {}}\nENDPOINT_ERRORS_JSON: {\"errors\": []}",
            "stderr": "",
        },
    )()

    estructura = {
        RUNTIME_CONTRACTS_PATH: json.dumps(
            {
                "endpoints": [],
                "env_vars_explicit": ["GOOGLE_DRIVE_FOLDER_ID", "ALREADY_SET_VAR"],
            }
        )
    }

    with patch(
        "poc_it.orquestacion.runtime_probe.subprocess.run", return_value=fake_completed
    ) as mock_run:
        run_runtime_probe(
            project_dir=".",
            estructura=estructura,
            python_executable="python",
        )

    passed_env = mock_run.call_args.kwargs["env"]
    assert passed_env["GOOGLE_DRIVE_FOLDER_ID"] == "DUMMY"
    # Una variable que ya tiene un valor real en el entorno NO se sobrescribe.
    assert passed_env["ALREADY_SET_VAR"] == "real-value"


def test_probe_reports_ok_when_no_endpoint_errors():
    fake_completed = type(
        "FakeCompleted",
        (),
        {
            "returncode": 0,
            "stdout": (
                "ENDPOINT_FACTS_JSON: {}\n"
                "STUB_SIGNATURES_JSON: {\"deps\": {}}\n"
                "ENDPOINT_ERRORS_JSON: {\"errors\": []}"
            ),
            "stderr": "",
        },
    )()

    with patch("poc_it.orquestacion.runtime_probe.subprocess.run", return_value=fake_completed):
        result = run_runtime_probe(
            project_dir=".",
            estructura={},
            python_executable="python",
        )

    assert result.ok is True


def test_generated_probe_script_survives_apostrophes_in_endpoint_data():
    """Regresión: el JSON de endpoints se incrustaba en el script generado envolviéndolo en un
    string raw (`r'''...'''`) con un escapado manual de comillas simples/backslashes. Ese
    escapado NO es válido dentro de un string `r'''...'''` (el backslash antepuesto a la comilla
    se conserva literal en vez de "consumirse"), así que cualquier apóstrofo en cualquier campo
    de un endpoint (path, note, response fields...) rompía `json.loads` dentro del propio
    subproceso del probe con `JSONDecodeError: Invalid \\escape`, abortando el probe entero antes
    de generar ningún resultado útil. Ahora se usa `repr()`, que siempre produce un literal de
    Python válido para cualquier contenido."""
    endpoint_with_apostrophe = {
        "path": "/health",
        "method": "GET",
        "depends_imports": [],
        "note": "it's fine, \"really\" \\ ok",
    }

    captured_code = {}

    def fake_run(cmd, **kwargs):
        captured_code["code"] = cmd[2]
        return type(
            "FakeCompleted",
            (),
            {
                "returncode": 0,
                "stdout": 'ENDPOINT_FACTS_JSON: {}\nSTUB_SIGNATURES_JSON: {"deps": {}}\nENDPOINT_ERRORS_JSON: {"errors": []}',
                "stderr": "",
            },
        )()

    estructura = {
        RUNTIME_CONTRACTS_PATH: json.dumps({"endpoints": [endpoint_with_apostrophe]}),
    }

    with patch("poc_it.orquestacion.runtime_probe.subprocess.run", side_effect=fake_run):
        result = run_runtime_probe(
            project_dir=".",
            estructura=estructura,
            python_executable="python",
        )

    assert result.ok is True

    # El script generado debe ser Python válido...
    compile(captured_code["code"], "<probe>", "exec")

    # ...pero lo que realmente reventaba antes no era un SyntaxError, sino un JSONDecodeError EN
    # TIEMPO DE EJECUCIÓN dentro del propio subproceso (el patrón `r'''...\'...'''` compila bien,
    # solo produce un valor de string incorrecto). Ejecutamos de verdad la línea que hace
    # `json.loads(...)` para confirmar que reconstruye el JSON original, apóstrofo incluido.
    json_line = next(
        line.strip() for line in captured_code["code"].splitlines() if line.strip().startswith("selected = json.loads(")
    )
    ns = {"json": json}
    exec(json_line, ns)
    assert ns["selected"]["endpoints"][0]["note"] == endpoint_with_apostrophe["note"]
