"""
Acceso de solo lectura a los artefactos de un proyecto generado
(`output/<nombre>/`) para el inspector de la interfaz web, y descubrimiento
de PoCs ya materializadas en `output/` que no pasaron por esta interfaz
(generadas por CLI, o de antes de que existiera la web).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / "output"

# Carpeta de diagnóstico global del pipeline (ver README §"output/_debug"): no es una PoC.
_EXCLUDED_OUTPUT_DIRS = {"_debug"}

_IGNORED_DIR_NAMES = {"__pycache__", ".pytest_cache", ".git"}
_MAX_READ_BYTES = 512 * 1024  # 512 KB: evita volcar binarios/artefactos enormes por accidente
_TEXT_EXTENSIONS = {
    ".md", ".txt", ".json", ".py", ".yaml", ".yml", ".ini", ".cfg", ".toml",
    ".xml", ".sha256", ".env",
}

# Prefijo de id para runs "sintéticos" descubiertos en `output/` (no rastreados como job en
# memoria). Permite a los endpoints de runs distinguir origen sin tocar el registro de jobs.
FS_RUN_ID_PREFIX = "fs:"

_ERROR_MARKER_FILES = ("README_ERROR.md", "RUNTIME_ENVIRONMENT_SETUP_ERROR.txt")


class ArtifactError(Exception):
    pass


def project_dir(nombre: str) -> Path:
    base = (OUTPUT_ROOT / nombre).resolve()
    if OUTPUT_ROOT.resolve() not in base.parents:
        raise ArtifactError("Nombre de proyecto inválido")
    if not base.is_dir():
        raise ArtifactError(f"No existe el directorio generado para '{nombre}'")
    return base


def _describe(nombre: str, root: Path) -> Dict[str, Any]:
    has_error_marker = any((root / marker).exists() for marker in _ERROR_MARKER_FILES)
    return {
        "id": f"{FS_RUN_ID_PREFIX}{nombre}",
        "nombre": nombre,
        "status": "error" if has_error_marker else "done",
        "source": "filesystem",
        "created_at": root.stat().st_mtime,
        "started_at": None,
        "finished_at": root.stat().st_mtime,
        "result": {"nombre_proyecto": nombre},
    }


def describe_output_project(nombre: str) -> Dict[str, Any]:
    """Resumen tipo-run para una PoC de `output/<nombre>/` no rastreada como job."""
    root = project_dir(nombre)
    return _describe(nombre, root)


def list_output_projects(*, exclude_names: set[str] | None = None) -> List[Dict[str, Any]]:
    """PoCs materializadas en `output/`, ignorando `_debug` y las ya cubiertas por un job.

    `exclude_names` son nombres de proyecto ya representados por un job en memoria (para no
    duplicar la misma PoC dos veces en el historial).
    """
    exclude = exclude_names or set()
    if not OUTPUT_ROOT.is_dir():
        return []

    out: List[Dict[str, Any]] = []
    for entry in OUTPUT_ROOT.iterdir():
        if not entry.is_dir():
            continue
        if entry.name in _EXCLUDED_OUTPUT_DIRS or entry.name.startswith("."):
            continue
        if entry.name in exclude:
            continue
        out.append(_describe(entry.name, entry))
    return out


def _node(path: Path, root: Path) -> Dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    if path.is_dir():
        children = sorted(
            (p for p in path.iterdir() if p.name not in _IGNORED_DIR_NAMES),
            key=lambda p: (p.is_file(), p.name.lower()),
        )
        return {
            "name": path.name,
            "path": rel,
            "type": "dir",
            "children": [_node(c, root) for c in children],
        }
    return {"name": path.name, "path": rel, "type": "file", "size": path.stat().st_size}


def list_tree(nombre: str) -> Dict[str, Any]:
    root = project_dir(nombre)
    return _node(root, root)


def read_artifact(nombre: str, rel_path: str) -> str:
    root = project_dir(nombre)
    target = (root / rel_path).resolve()

    if root not in target.parents and target != root:
        raise ArtifactError("Ruta fuera del proyecto")
    if not target.is_file():
        raise ArtifactError("El fichero no existe")
    if target.suffix.lower() not in _TEXT_EXTENSIONS:
        raise ArtifactError("Tipo de fichero no soportado por el inspector")
    if target.stat().st_size > _MAX_READ_BYTES:
        raise ArtifactError("Fichero demasiado grande para previsualizar")

    return target.read_text(encoding="utf-8", errors="replace")
