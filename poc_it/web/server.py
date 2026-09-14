"""
Backend web de PoC-it.

Arranque: `python -m poc_it.web`.

Endpoints v1:
- POST /api/runs                    -> encola una nueva ejecución del pipeline
- GET  /api/runs                    -> lista de runs (histórico + en curso)
- GET  /api/runs/{id}               -> estado/resultado de un run
- GET  /api/runs/{id}/stream (SSE)  -> progreso en vivo
- GET  /api/runs/{id}/files         -> árbol de ficheros del proyecto generado
- GET  /api/runs/{id}/artifact      -> contenido (solo lectura) de un fichero generado
- POST /api/runs/{id}/publish       -> publica en GitLab un run terminado y publicable
- GET  /api/proxy/status | POST /api/proxy/start | POST /api/proxy/stop

El frontend vive en `ui/` (fuera de `poc_it/`, en la raíz del repo) y habla
con esta API vía el proxy de Vite en desarrollo (`ui/vite.config.js`).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from poc_it.analisis.analizador_viabilidad import PlantillaUsuario
from poc_it.web import artifacts, proxy_manager, settings_store
from poc_it.web.jobs import STREAM_DONE, registry

# El pipeline reutiliza `demo_progress`: en el proceso web siempre lo activamos
# para poder capturar y retransmitir el progreso por fase.
os.environ.setdefault("POCIT_MODE", "demo")

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIST = REPO_ROOT / "ui" / "dist"

app = FastAPI(title="PoC-it API")


class NuevaPoCRequest(BaseModel):
    nombre: str
    problema: str
    usuarios: str
    funcionalidades: str
    limites: str
    tecnologias: str
    publish: bool = True


@app.on_event("startup")
def _startup() -> None:
    try:
        proxy_manager.start()
    except Exception:
        # No bloquear el arranque del servidor si el proxy no puede levantarse;
        # el estado quedará visible vía GET /api/proxy/status.
        pass


@app.on_event("shutdown")
def _shutdown() -> None:
    # En Windows los procesos hijos no mueren solos con el padre: si nosotros
    # arrancamos el proxy, nos encargamos también de pararlo al cerrar.
    # `stop()` ya es un no-op si no fuimos nosotros quienes lo arrancamos.
    proxy_manager.stop()


@app.post("/api/runs")
def crear_run(payload: NuevaPoCRequest):
    plantilla = PlantillaUsuario(
        nombre=payload.nombre,
        problema=payload.problema,
        usuarios=payload.usuarios,
        funcionalidades=payload.funcionalidades,
        limites=payload.limites,
        tecnologias=payload.tecnologias,
    )
    job = registry.submit(plantilla, publish=payload.publish)
    return job.summary()


@app.get("/api/runs")
def listar_runs():
    jobs = [job.summary() for job in registry.list()]
    tracked_names = {
        (job["result"] or {}).get("nombre_proyecto")
        for job in jobs
        if job.get("result")
    }
    imported = artifacts.list_output_projects(exclude_names=tracked_names)
    return sorted(jobs + imported, key=lambda r: r["created_at"], reverse=True)


@app.get("/api/runs/{run_id}")
def obtener_run(run_id: str):
    if run_id.startswith(artifacts.FS_RUN_ID_PREFIX):
        try:
            return artifacts.describe_output_project(run_id[len(artifacts.FS_RUN_ID_PREFIX):])
        except artifacts.ArtifactError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    job = registry.get(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Run no encontrado")
    return job.summary()


def _nombre_proyecto_de(run_id: str) -> str:
    if run_id.startswith(artifacts.FS_RUN_ID_PREFIX):
        nombre = run_id[len(artifacts.FS_RUN_ID_PREFIX):]
        try:
            artifacts.project_dir(nombre)  # valida que el directorio exista
        except artifacts.ArtifactError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return nombre

    job = registry.get(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Run no encontrado")
    nombre = (job.result or {}).get("nombre_proyecto")
    if not nombre:
        raise HTTPException(status_code=409, detail="El run todavía no tiene un proyecto materializado")
    return nombre


@app.get("/api/runs/{run_id}/files")
def listar_ficheros(run_id: str):
    nombre = _nombre_proyecto_de(run_id)
    try:
        return artifacts.list_tree(nombre)
    except artifacts.ArtifactError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/artifact")
def leer_fichero(run_id: str, path: str):
    nombre = _nombre_proyecto_de(run_id)
    try:
        return {"path": path, "content": artifacts.read_artifact(nombre, path)}
    except artifacts.ArtifactError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/publish")
def publicar_run(run_id: str):
    if run_id.startswith(artifacts.FS_RUN_ID_PREFIX):
        raise HTTPException(
            status_code=409,
            detail="Publicación no soportada para PoCs importadas de output/ (no generadas desde esta interfaz).",
        )

    job = registry.get(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Run no encontrado")
    if job.status != "done" or not job.result:
        raise HTTPException(status_code=409, detail="El run todavía no ha terminado")
    if not job.result.get("generacion_exitosa"):
        raise HTTPException(status_code=409, detail="El proyecto no es publicable (publishable=False)")
    if job.result.get("publish_url"):
        raise HTTPException(status_code=409, detail="El proyecto ya ha sido publicado")

    from poc_it.integraciones.gitlab_publisher import GitLabPublisher

    nombre = job.result["nombre_proyecto"]
    ruta = artifacts.project_dir(nombre)

    try:
        publisher = GitLabPublisher()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    url = publisher.publicar(nombre, str(ruta))
    if not url:
        raise HTTPException(status_code=502, detail="GitLab no devolvió una URL de repositorio")

    job.result["publish_url"] = url
    job.result["publish_status"] = "OK"
    return job.summary()


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str):
    if run_id.startswith(artifacts.FS_RUN_ID_PREFIX):
        # PoC importada de output/: no hay job en memoria ni progreso que retransmitir.
        # Cerramos el stream inmediatamente para que el frontend trate ambos orígenes igual.
        async def done_only():
            yield "event: done\ndata: {}\n\n"

        return StreamingResponse(done_only(), media_type="text/event-stream")

    job = registry.get(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Run no encontrado")

    async def event_generator():
        q = job.subscribe()
        try:
            while True:
                item = await asyncio.get_event_loop().run_in_executor(None, q.get)
                if item is STREAM_DONE:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {item}\n\n"
        finally:
            job.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/proxy/status")
def proxy_status():
    return proxy_manager.status()


@app.post("/api/proxy/start")
def proxy_start():
    try:
        return proxy_manager.start()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/proxy/stop")
def proxy_stop():
    return proxy_manager.stop()


@app.get("/api/settings")
def obtener_settings():
    return settings_store.get_settings()


@app.post("/api/settings")
def actualizar_settings(values: Dict[str, str]):
    return settings_store.update_settings(values)


# Frontend compilado (`npm run build` en `ui/`). Se monta al final para que
# las rutas /api/* de arriba, ya registradas, tengan siempre prioridad.
if UI_DIST.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIST), html=True), name="ui")
