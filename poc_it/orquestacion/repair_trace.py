from __future__ import annotations
"""
Trazas persistentes del pipeline de reparación.

Objetivo
--------
- Saber QUÉ se intentó reparar, CUÁNDO, con QUÉ inputs (clase de fallo, gates) y QUÉ archivos cambió.
- Evitar leer logs enormes para depurar convergencia.

El trace es best-effort: nunca debe romper el pipeline.
"""

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

TRACE_PATH = ".poc_it/repair_trace.json"


@dataclass
class TraceEvent:
    ts: float
    phase: str
    attempt: int
    action: str
    classifier_kind: Optional[str] = None
    detail: Optional[str] = None
    gate_reason: Optional[str] = None
    files_changed: Optional[List[str]] = None
    pytest_head: Optional[str] = None


def append_trace_event(project_dir: str, event: TraceEvent) -> None:
    try:
        path = os.path.join(project_dir, TRACE_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        data: Dict[str, Any] = {"events": []}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                    if isinstance(obj, dict) and isinstance(obj.get("events"), list):
                        data = obj
            except Exception:
                # si está corrupto, lo sobreescribimos
                data = {"events": []}

        data["events"].append(asdict(event))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        return


def now_ts() -> float:
    return time.time()
