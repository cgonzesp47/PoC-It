from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any


def is_demo_mode() -> bool:
    """Return True iff POCIT_MODE=demo (case-insensitive)."""
    return os.getenv("POCIT_MODE", "").strip().lower() == "demo"


@dataclass
class DemoProgress:
    """Simple console-only progress helper for demo mode.

    - No logging handlers
    - No file output
    - Does nothing unless POCIT_MODE=demo
    """

    stream: Any = sys.stdout

    def _emit(self, msg: str) -> None:
        if not is_demo_mode():
            return
        # Avoid extra blank lines / leading-trailing whitespace in demo.
        self.stream.write(msg.rstrip() + "\n")
        self.stream.flush()

    def step(self, idx: int, total: int, title: str) -> None:
        self._emit(f"[{idx}/{total}] {title}")

    def info(self, msg: str) -> None:
        # Evitar que se impriman líneas "vacías" que generan saltos de línea fantasma en demo.
        if msg is None:
            return
        if isinstance(msg, str) and not msg.strip():
            return
        self._emit(f"      {msg}")

    def ok(self, msg: str) -> None:
        self._emit(f"      {msg}")

    def warn(self, msg: str) -> None:
        self._emit(f"      {msg}")

    def error(self, msg: str) -> None:
        self._emit(f"      {msg}")


demo_progress = DemoProgress()
