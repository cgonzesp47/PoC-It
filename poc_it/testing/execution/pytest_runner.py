from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class PytestExecutionResult:
    command: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    collected_tests: int

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.returncode == 0 and self.collected_tests > 0


_COLLECTED_TESTS_RE = re.compile(r"collected\s+(?P<count>\d+)\s+items?", re.IGNORECASE)
_COLLECT_ONLY_FILE_COUNT_RE = re.compile(r"^\s*(?:<Dir\s+)?tests?[\\/].*:\s*(?P<count>\d+)\s*$", re.IGNORECASE | re.MULTILINE)


def _extract_collected_tests(output: str) -> int:
    if not output:
        return 0

    match = None
    for current in _COLLECTED_TESTS_RE.finditer(output):
        match = current
    if match is not None:
        try:
            return int(match.group("count"))
        except Exception:
            return 0

    collected_from_files = 0
    found_file_counts = False
    for current in _COLLECT_ONLY_FILE_COUNT_RE.finditer(output):
        found_file_counts = True
        try:
            collected_from_files += int(current.group("count"))
        except Exception:
            return 0

    if found_file_counts:
        return collected_from_files

    path_lines = [
        line
        for line in output.splitlines()
        if re.match(r"^\s*(?:<Dir\s+)?tests?[\\/].*test_.*\.py\s*$", line, re.IGNORECASE)
    ]
    if path_lines:
        return len(path_lines)

    return 0


def run_pytest_in_project(
    project_root: Path,
    *,
    timeout_seconds: int = 60,
    extra_env: Mapping[str, str] | None = None,
    python_executable: str | None = None,
) -> PytestExecutionResult:
    """Ejecuta pytest sobre `project_root`.

    `python_executable`: intérprete a usar. Debe ser el Python del entorno aislado de la PoC
    generada (`.poc_it/venv`, ver `poc_it.runtime.poc_runtime_environment`), NUNCA
    `sys.executable` del proceso de PoC-it, si se está ejecutando código de una PoC generada.
    Por compatibilidad, si no se pasa, se usa `sys.executable` (comportamiento previo) — pensado
    para callers que ejecutan pytest sobre fixtures propias de PoC-it, no sobre una PoC generada.
    """
    root = Path(project_root).resolve()
    py = python_executable or sys.executable
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("NO_PROXY", "*")
    env.setdefault("no_proxy", "*")
    if extra_env:
        for key, value in extra_env.items():
            env[str(key)] = str(value)

    collect_command = (py, "-m", "pytest", "--collect-only", "-q")
    command = (py, "-m", "pytest", "-q")
    started = time.perf_counter()
    try:
        collect_completed = subprocess.run(
            collect_command,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        collect_stdout = collect_completed.stdout or ""
        collect_stderr = collect_completed.stderr or ""
        collected_tests = _extract_collected_tests(f"{collect_stdout}\n{collect_stderr}")

        completed = subprocess.run(
            command,
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        duration = time.perf_counter() - started
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if collect_stdout:
            stdout = f"[collect-only]\n{collect_stdout}\n{stdout}"
        if collect_stderr:
            stderr = f"[collect-only]\n{collect_stderr}\n{stderr}"
        return PytestExecutionResult(
            command=command,
            cwd=str(root),
            returncode=int(completed.returncode),
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            timed_out=False,
            collected_tests=collected_tests,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - started
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout.decode("utf-8", errors="replace") if exc.stdout else "")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "")
        return PytestExecutionResult(
            command=command,
            cwd=str(root),
            returncode=-1,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=duration,
            timed_out=True,
            collected_tests=0,
        )
