from __future__ import annotations
"""
DEPRECATED: sustituido por `poc_it.runtime.poc_runtime_environment`.

Este módulo creaba el venv del proyecto generado en una ruta derivada por hash
(`.poc_it/venvs/<sha1>/`, relativa al cwd del proceso de PoC-it) que NO coincidía con la ruta
que el resto del pipeline (runtime_verify, runtime_probe, wiring_verifier, pytest) esperaba
(`<project_dir>/.poc_it/venv`). Esa inconsistencia hacía que el venv preparado aquí nunca se
usara realmente, y el pipeline acababa cayendo de vuelta al Python de PoC-it (falsos negativos
tipo `ModuleNotFoundError`).

`poc_it.runtime.poc_runtime_environment.prepare_poc_runtime_environment` es ahora la ÚNICA
fuente de verdad: siempre crea el venv en `<project_dir>/.poc_it/venv` y devuelve un
`PocRuntimeEnvironment` con el `python_executable` real que deben usar todos los callers.

Se mantiene esta función como wrapper fino solo por compatibilidad, en caso de que algún código
externo la siga importando.
"""

from dataclasses import dataclass
from typing import Any

from poc_it.runtime.poc_runtime_environment import prepare_poc_runtime_environment


@dataclass(frozen=True)
class VenvReadyResult:
    ok: bool
    detail: str


def ensure_project_venv_ready(*, project_dir: str, estructura: dict[str, str] | None = None, spec: Any = None) -> VenvReadyResult:
    """DEPRECATED: usa `poc_it.runtime.poc_runtime_environment.prepare_poc_runtime_environment`."""
    env = prepare_poc_runtime_environment(project_dir)
    return VenvReadyResult(ok=env.ready, detail=env.detail)
