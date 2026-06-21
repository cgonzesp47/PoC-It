from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


GenerationMode = Literal["PARCIAL", "COMPLETO", "ASESOR"]
RunStatus = Literal["OK", "OK_DEGRADED", "ERROR"]


@dataclass
class RunResult:
    """
    Fuente de verdad del estado final de una ejecución.

    Motivación:
    - Evitar heurísticas basadas en strings de logs o en parsing frágil de stdout.
    - Centralizar la política de publicabilidad en un único objeto estructurado.
    """

    mode: GenerationMode
    status: RunStatus
    publishable: bool

    # Motivos/diagnóstico (human friendly)
    reasons: List[str] = field(default_factory=list)

    # Artefactos generados por el pipeline (paths relativos dentro del proyecto generado)
    artifacts: Dict[str, str] = field(default_factory=dict)

    # Métricas / números (attempts, duración, etc.)
    metrics: Dict[str, Any] = field(default_factory=dict)

    # Campos opcionales de compatibilidad con el contrato anterior
    pytest_ok: Optional[bool] = None
    degraded: Optional[bool] = None
    degrade_type: Optional[str] = None

    @staticmethod
    def ok(mode: GenerationMode, *, degraded: bool = False, degrade_type: Optional[str] = None) -> "RunResult":
        if degraded:
            return RunResult(
                mode=mode,
                status="OK_DEGRADED",
                publishable=True,
                reasons=[f"degraded:{degrade_type or 'unknown'}"],
                pytest_ok=True,
                degraded=True,
                degrade_type=degrade_type,
            )
        return RunResult(mode=mode, status="OK", publishable=True, pytest_ok=True, degraded=False)

    @staticmethod
    def error(mode: GenerationMode, reason: str) -> "RunResult":
        return RunResult(mode=mode, status="ERROR", publishable=False, reasons=[reason], pytest_ok=False, degraded=None)
