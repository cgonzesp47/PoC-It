from __future__ import annotations

from poc_it.orquestacion.reparacion_runtime import _resolve_require_probe_ok


def test_require_probe_ok_defaults_to_true(monkeypatch):
    """Regresión: antes el probe request-time era opt-in explícito y se ignoraba en PARCIAL por
    defecto, dejando pasar a tests código que ya reventaba con una petición real sintética."""
    monkeypatch.delenv("POC_IT_SAFE_LLM_REPAIR_REQUIRE_PROBE_OK", raising=False)

    assert _resolve_require_probe_ok() is True


def test_require_probe_ok_can_be_opted_out_for_rollback(monkeypatch):
    """Vía de reversión: quien quiera el comportamiento anterior (best-effort, no bloquea) puede
    desactivarlo explícitamente."""
    monkeypatch.setenv("POC_IT_SAFE_LLM_REPAIR_REQUIRE_PROBE_OK", "0")

    assert _resolve_require_probe_ok() is False


def test_require_probe_ok_explicit_true_still_works():
    import os

    os.environ["POC_IT_SAFE_LLM_REPAIR_REQUIRE_PROBE_OK"] = "1"
    try:
        assert _resolve_require_probe_ok() is True
    finally:
        del os.environ["POC_IT_SAFE_LLM_REPAIR_REQUIRE_PROBE_OK"]
