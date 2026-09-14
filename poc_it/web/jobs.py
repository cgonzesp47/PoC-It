"""
Registro y ejecución en background de runs del pipeline PoC-it para la interfaz web.

v1: un solo run a la vez (ver plan de arquitectura). Los runs entrantes mientras
hay uno en curso se encolan en `_job_queue` y un único hilo trabajador los
procesa en orden, redirigiendo `demo_progress` a colas por-suscriptor para que
`GET /runs/{id}/stream` (SSE) pueda reenviar el progreso en vivo.
"""

from __future__ import annotations

import asyncio
import io
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from poc_it.analisis.analizador_viabilidad import PlantillaUsuario
from poc_it.entrada.demo_progress import demo_progress
from poc_it.pipeline_runner import run_pipeline

# Sentinel para indicar a los suscriptores SSE que el run ha terminado.
STREAM_DONE = object()


@dataclass
class RunJob:
    id: str
    plantilla: PlantillaUsuario
    publish: bool = True
    status: str = "queued"  # queued | running | done | error
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    log_lines: List[str] = field(default_factory=list)
    _subscribers: List["queue.Queue[Any]"] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "nombre": self.plantilla.nombre,
            "status": self.status,
            "source": "job",
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
        }

    def subscribe(self) -> "queue.Queue[Any]":
        q: "queue.Queue[Any]" = queue.Queue()
        with self._lock:
            # Repone al nuevo suscriptor el historial ya emitido antes de sumarlo a live.
            for line in self.log_lines:
                q.put(line)
            if self.status in ("done", "error"):
                q.put(STREAM_DONE)
            else:
                self._subscribers.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue[Any]") -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _emit(self, line: str) -> None:
        with self._lock:
            self.log_lines.append(line)
            for q in self._subscribers:
                q.put(line)

    def _close_stream(self) -> None:
        with self._lock:
            for q in self._subscribers:
                q.put(STREAM_DONE)
            self._subscribers.clear()


class _JobLogWriter(io.TextIOBase):
    """Sustituye `demo_progress.stream`: cada línea escrita se reenvía al job."""

    def __init__(self, job: RunJob):
        self._job = job

    def write(self, s: str) -> int:
        if not s:
            return 0
        for line in s.splitlines():
            if line.strip():
                self._job._emit(line)
        return len(s)

    def flush(self) -> None:
        return None


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: Dict[str, RunJob] = {}
        self._order: List[str] = []
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()

    def submit(self, plantilla: PlantillaUsuario, publish: bool = True) -> RunJob:
        job = RunJob(id=str(uuid.uuid4()), plantilla=plantilla, publish=publish)
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> Optional[RunJob]:
        return self._jobs.get(job_id)

    def list(self) -> List[RunJob]:
        return [self._jobs[jid] for jid in reversed(self._order)]

    def _run_worker(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self._jobs.get(job_id)
            if job is None:
                continue
            self._execute(job)

    def _execute(self, job: RunJob) -> None:
        job.status = "running"
        job.started_at = time.time()

        original_stream = demo_progress.stream
        demo_progress.stream = _JobLogWriter(job)
        try:
            result = asyncio.run(run_pipeline(job.plantilla, publish=job.publish))
            job.result = result
            job.status = "error" if result.get("error") else "done"
        except Exception as exc:  # salvaguarda: nunca debe tumbar al hilo trabajador
            job.result = {"error": str(exc)}
            job.status = "error"
        finally:
            demo_progress.stream = original_stream
            job.finished_at = time.time()
            job._close_stream()


registry = JobRegistry()
