from __future__ import annotations

import json
from unittest.mock import patch

from poc_it.orquestacion.llm_safe_repair import (
    SafeRepairConfig,
    _render_diag_prompt,
    _render_patch_prompt,
    _render_previous_attempts_section,
    run_llm_safe_repair,
)


def test_render_previous_attempts_section_empty_when_none_given():
    assert _render_previous_attempts_section(None) == ""
    assert _render_previous_attempts_section([]) == ""


def test_render_previous_attempts_section_includes_patch_and_reason():
    section = _render_previous_attempts_section(
        [
            {
                "patch": {"app/api/endpoints/upload.py": "...contenido rechazado..."},
                "reason": "RUNTIME_REQUEST_PROBE_FAILED\nunexpected keyword argument 'file_path'",
            }
        ]
    )
    assert "INTENTOS ANTERIORES YA PROBADOS Y RECHAZADOS" in section
    assert "unexpected keyword argument 'file_path'" in section
    assert "...contenido rechazado..." in section


def test_diag_and_patch_prompts_include_previous_attempts_section():
    previous_attempts = [{"patch": {"app/x.py": "bad"}, "reason": "still fails"}]

    diag_prompt = _render_diag_prompt(
        spec=None,
        runtime_detail="TypeError: x",
        guardrails_warnings=None,
        allowlist=["app/x.py"],
        files_subset=[{"path": "app/x.py", "content": "y = 1"}],
        previous_attempts=previous_attempts,
    )
    assert "INTENTOS ANTERIORES YA PROBADOS Y RECHAZADOS" in diag_prompt
    assert "still fails" in diag_prompt

    patch_prompt = _render_patch_prompt(
        spec=None,
        runtime_detail="TypeError: x",
        allowlist=["app/x.py"],
        files_subset=[{"path": "app/x.py", "content": "y = 1"}],
        diag={"files_to_change": ["app/x.py"]},
        previous_attempts=previous_attempts,
    )
    assert "INTENTOS ANTERIORES YA PROBADOS Y RECHAZADOS" in patch_prompt
    assert "still fails" in patch_prompt


def test_diag_and_patch_prompts_include_shared_generic_python_rules():
    """Regresión: las reglas genéricas de corrección de Python (tempfile, imports de
    submódulos, no tratar un dict como escalar) ya se validaron y se usan en el prompt de
    codegen/reparación de contrato, pero el prompt de reparación runtime no las tenía, así que
    Safe LLM Repair repetía los mismos errores que esas reglas ya sabían evitar."""
    diag_prompt = _render_diag_prompt(
        spec=None,
        runtime_detail="FileNotFoundError: x",
        guardrails_warnings=None,
        allowlist=["app/x.py"],
        files_subset=[{"path": "app/x.py", "content": "y = 1"}],
    )
    patch_prompt = _render_patch_prompt(
        spec=None,
        runtime_detail="FileNotFoundError: x",
        allowlist=["app/x.py"],
        files_subset=[{"path": "app/x.py", "content": "y = 1"}],
        diag={"files_to_change": ["app/x.py"]},
    )
    for prompt in (diag_prompt, patch_prompt):
        assert "tempfile" in prompt
        assert "is not defined" in prompt
        assert "escalar" in prompt


def test_diag_and_patch_prompts_include_diagnostic_methodology_rule():
    """Regresión concreta: la Safe LLM Repair diagnosticaba 4/4 veces un
    FileNotFoundError como 'problema del fichero temporal' sin nunca rastrear que la causa
    real era pasar test_file['filename'] en vez de test_file['filepath'] a la llamada. Esta
    regla genérica (rastrear cada valor hasta su origen antes de culpar al recurso externo)
    debe estar presente en ambos prompts para evitar que se repita ese patrón de
    misdiagnóstico."""
    diag_prompt = _render_diag_prompt(
        spec=None,
        runtime_detail="FileNotFoundError: x",
        guardrails_warnings=None,
        allowlist=["app/x.py"],
        files_subset=[{"path": "app/x.py", "content": "y = 1"}],
    )
    patch_prompt = _render_patch_prompt(
        spec=None,
        runtime_detail="FileNotFoundError: x",
        allowlist=["app/x.py"],
        files_subset=[{"path": "app/x.py", "content": "y = 1"}],
        diag={"files_to_change": ["app/x.py"]},
    )
    for prompt in (diag_prompt, patch_prompt):
        assert "rastrea" in prompt.lower()
        assert "origen en el código" in prompt


def test_run_llm_safe_repair_forwards_previous_attempts_into_the_real_prompt():
    """Regresión: cada ronda EXTERIOR de reparación (en reparacion_runtime.py) llamaba a
    `run_llm_safe_repair` desde cero, sin decirle nunca qué parche ya se había aplicado de
    verdad y por qué la re-verificación externa (import real / wiring / petición real al
    endpoint) lo había rechazado. Confirmamos aquí que, si se le pasa ese historial, SÍ llega
    al prompt real enviado al LLM."""
    captured_prompts = []

    def fake_chat_completion_json(*, prompt, **kwargs):
        captured_prompts.append(prompt)
        if len(captured_prompts) == 1:
            # DIAGNOSE
            return json.dumps({"files_to_change": ["app/api/endpoints/upload.py"], "patch_plan": {}})
        # PATCH
        return json.dumps(
            {
                "files": [
                    {"path": "app/api/endpoints/upload.py", "content": "def create_upload():\n    pass\n"}
                ]
            }
        )

    previous_attempts = [
        {
            "patch": {"app/api/endpoints/upload.py": "def create_upload():\n    old_broken_version()\n"},
            "reason": "RUNTIME_REQUEST_PROBE_FAILED\nunexpected keyword argument 'file_path'",
        }
    ]

    with patch(
        "poc_it.orquestacion.llm_safe_repair.chat_completion_json",
        side_effect=fake_chat_completion_json,
    ):
        result = run_llm_safe_repair(
            config=SafeRepairConfig(enabled=True, max_attempts=1),
            project_dir=".",
            spec=None,
            estructura={"app/api/endpoints/upload.py": "def create_upload():\n    pass\n"},
            runtime_detail='File "app/api/endpoints/upload.py", line 1\nTypeError: x',
            previous_attempts=previous_attempts,
        )

    assert result.ok is True
    assert len(captured_prompts) == 2
    for prompt in captured_prompts:
        assert "unexpected keyword argument 'file_path'" in prompt
        assert "old_broken_version" in prompt
