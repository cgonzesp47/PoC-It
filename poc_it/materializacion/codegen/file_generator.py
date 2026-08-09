from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Dict, List

from poc_it.infraestructura.llm_client import LLMTruncatedResponseError

from poc_it.generador.prompts_file_contracts import (
    build_prompt_file_contract,
    build_prompt_file_contract_fix,
)

from .debug import (
    dump_codegen_attempt_metadata,
    dump_codegen_contract_errors,
    dump_codegen_file_accepted,
    dump_codegen_raw_for_debug,
)
from .models import CodegenIssue, FileGenerationResult
from .semantic_repair import summarize_generated_interfaces
from .parsing import parse_single_file_response
from .structural_validation import validate_generated_file_structure
from .semantic_validation import validate_generated_file_semantics


LOGGER = logging.getLogger(__name__)

_CODEGEN_PROMPT_WARN_CHARS = 25000


class FileContractGenerator:
    def __init__(
        self,
        *,
        llm_call: Callable[..., str],
        max_attempts: int,
    ) -> None:
        self._llm_call = llm_call
        self._max_attempts = max(1, int(max_attempts))

    def generate(
        self,
        *,
        spec: Dict[str, Any],
        file_contract: Dict[str, Any],
        related_contracts: List[Dict[str, Any]],
        descripcion_global: str,
        contexto_normalizado: Dict[str, Any],
    ) -> FileGenerationResult:
        path = str(file_contract.get("path") or "").replace("\\", "/")
        kind = str(file_contract.get("kind") or "").strip()
        last_generated_content = ""
        last_issues: List[CodegenIssue] = []
        prompt_chars = 0
        prompt_words = 0

        for attempt in range(1, self._max_attempts + 1):
            if attempt == 1:
                prompt = build_prompt_file_contract(
                    spec=spec,
                    file_contract=file_contract,
                    related_contracts=related_contracts,
                    descripcion_global=descripcion_global,
                    contexto_normalizado=contexto_normalizado,
                )
                temperature = 0.2
                fase = "generacion_codigo_file_contract"
            else:
                prompt = build_prompt_file_contract_fix(
                    spec=spec,
                    file_contract=file_contract,
                    previous_content=last_generated_content,
                    errors=[
                        f"[{issue.code}] {issue.message}" for issue in last_issues
                    ]
                    or ["previous_attempt_failed"],
                    related_contracts=related_contracts,
                    structured_issues=[issue.to_dict() for issue in last_issues],
                    generated_interface_summaries=[
                        summary.__dict__
                        for summary in summarize_generated_interfaces(
                            generated_files=[],
                            file_contracts=[file_contract, *related_contracts],
                        )
                        if summary.path != path
                    ],
                )
                temperature = 0.1
                fase = "generacion_codigo_file_contract_fix"

            requested_max_tokens = _max_tokens_for_kind(kind)
            prompt_chars = len(prompt)
            prompt_words = len(prompt.split())
            LOGGER.debug(
                "[CODEGEN] file=%s prompt_chars=%s prompt_words=%s max_tokens=%s",
                path,
                prompt_chars,
                prompt_words,
                requested_max_tokens,
            )
            if prompt_chars > _CODEGEN_PROMPT_WARN_CHARS:
                LOGGER.warning(
                    "[CODEGEN] Prompt grande para %s: %d chars",
                    path,
                    prompt_chars,
                )

            try:
                raw = self._llm_call(
                    prompt=prompt,
                    system=None,
                    temperature=temperature,
                    max_tokens=requested_max_tokens,
                    fase=fase,
                    provider_hint="code-gen",
                )
                dump_codegen_attempt_metadata(
                    path=path,
                    intento=attempt,
                    payload={
                        "file": path,
                        "attempt": attempt,
                        "provider": "llm_call",
                        "model": "code-gen" if attempt == 1 else "code-gen-fix",
                        "finish_reason": None,
                        "requested_max_tokens": requested_max_tokens,
                        "prompt_chars": prompt_chars,
                        "prompt_words": prompt_words,
                        "response_chars": len(raw or ""),
                        "response_complete": True,
                        "error_type": None,
                    },
                )
            except LLMTruncatedResponseError as exc:
                dump_codegen_raw_for_debug(
                    path=path,
                    intento=attempt,
                    raw=exc.partial_content,
                )
                dump_codegen_attempt_metadata(
                    path=path,
                    intento=attempt,
                    payload={
                        "file": path,
                        "attempt": attempt,
                        "provider": exc.provider,
                        "model": exc.model,
                        "finish_reason": exc.finish_reason,
                        "requested_max_tokens": requested_max_tokens,
                        "prompt_chars": prompt_chars,
                        "prompt_words": prompt_words,
                        "response_chars": len(exc.partial_content or ""),
                        "response_complete": False,
                        "error_type": "LLMTruncatedResponseError",
                    },
                )
                last_generated_content = ""
                last_issues = [
                    CodegenIssue(
                        code="LLM_TRUNCATED_RESPONSE",
                        path=path,
                        message=str(exc),
                    )
                ]
                continue
            generated_file, parse_issues = parse_single_file_response(
                raw=raw,
                expected_path=path,
                allow_empty_content=False,
            )
            if parse_issues:
                dump_codegen_raw_for_debug(path=path, intento=attempt, raw=raw)
                dump_codegen_contract_errors(path=path, issues=parse_issues)
                last_issues = parse_issues
                continue

            assert generated_file is not None
            last_generated_content = generated_file.content
            issues = validate_generated_file_structure(
                generated_file=generated_file,
                file_contract=file_contract,
            )
            if issues:
                dump_codegen_contract_errors(path=path, issues=issues)
                last_issues = issues
                continue

            semantic_issues = validate_generated_file_semantics(
                generated_file=generated_file,
                file_contract=file_contract,
            )
            blocking_semantic_issues = [
                issue for issue in semantic_issues if issue.severity == "error"
            ]
            if blocking_semantic_issues:
                dump_codegen_contract_errors(path=path, issues=semantic_issues)
                last_issues = semantic_issues
                continue

            dump_codegen_file_accepted(generated_file=generated_file)
            return FileGenerationResult(
                file=generated_file,
                issues=[],
                attempts=attempt,
            )

        return FileGenerationResult(
            file=None,
            issues=last_issues
            or [
                CodegenIssue(
                    code="CODEGEN_GENERATION_FAILED",
                    path=path,
                    message="No se pudo generar el archivo requerido.",
                )
            ],
            attempts=self._max_attempts,
        )


def _max_tokens_for_kind(kind: str) -> int:
    kind = (kind or "").strip()
    if kind in ("endpoint",):
        return 3000
    if kind in ("router", "main", "config"):
        return 2200
    if kind in ("requirements", "docs"):
        return 1400
    return 1800
