"""
GitLab Publisher – Funcionalidad independiente de publicación automática.

Responsabilidades:
- Crear repositorio en GitLab dentro del grupo configurado.
- Subir todo el contenido generado (output/PoCXXX).
- Devolver URL del repositorio creado.
- No romper el flujo principal si falla.

Requiere variables de entorno:

POCIT_GITLAB_TOKEN
POCIT_GITLAB_GROUP
POCIT_GITLAB_URL
"""

import os
import re
import shutil
import subprocess
import tempfile
import unicodedata

import requests
from pathlib import Path
from typing import Optional


def _slugify_path(name: str, max_len: int = 60) -> str:
    """
    Genera un 'path' válido para GitLab a partir de un nombre arbitrario.
    - ASCII
    - minúsculas
    - separadores '-'
    - solo [a-z0-9-]
    - longitud acotada
    """
    raw = (name or "").strip().lower()
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    raw = re.sub(r"[^a-z0-9]+", "-", raw)
    raw = raw.strip("-")
    raw = re.sub(r"-{2,}", "-", raw)
    if not raw:
        raw = "poc-it"
    return raw[:max_len].strip("-") or "poc-it"


class GitLabPublisher:

    def __init__(self) -> None:
        self.token = os.getenv("POCIT_GITLAB_TOKEN")
        # Puede ser grupo o subgrupo. Recomendado: full_path (p.ej. "FORTEFORTE/poc-generated")
        self.namespace_path = os.getenv("POCIT_GITLAB_GROUP", "FORTEFORTE")
        # Escape hatch: permitir fijar directamente el ID del grupo/subgrupo para evitar problemas de search/permissions.
        # Útil en instancias GitLab donde `GET /groups/{full_path}` devuelve 404 o el search no lista subgrupos.
        gid_env = os.getenv("POCIT_GITLAB_GROUP_ID")
        self.namespace_id = int(gid_env) if gid_env and gid_env.isdigit() else None
        self.base_url = os.getenv("POCIT_GITLAB_URL")

        if not self.token:
            raise RuntimeError("POCIT_GITLAB_TOKEN no configurado")

        if not self.base_url:
            raise RuntimeError("POCIT_GITLAB_URL no configurado")

        # Normalización robusta de base_url (evita pasar URL de grupo web tipo .../git/FORTEFORTE)
        from urllib.parse import urlparse

        parsed = urlparse(self.base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # GitLab corporativo suele ir bajo /git
        if "/git" in parsed.path:
            base += "/git"
        # Si alguien pasó /git/GRUPO, lo reducimos a /git
        # (evita que la API apunte a HTML del portal del grupo)
        self.base_url = base.rstrip("/")

        self.api_url = f"{self.base_url}/api/v4"

        self.headers = {
            "PRIVATE-TOKEN": self.token
        }

    # ==========================================================
    # API GITLAB
    # ==========================================================

    def _get_group_id(self) -> int:
        """Obtiene el ID real del namespace (grupo/subgrupo) en GitLab.

        `name` no es único en GitLab, así que resolvemos por `full_path` siempre que sea posible.
        """
        from urllib.parse import quote

        key = (self.namespace_path or "").strip().strip("/")
        if not key:
            raise RuntimeError("POCIT_GITLAB_GROUP vacío/no configurado")

        # 0) Si el usuario proporciona el ID explícitamente, no hacemos búsqueda.
        if self.namespace_id is not None:
            return self.namespace_id

        # 1) Intento directo (hay instancias donde funciona con full_path URL-encoded)
        r = requests.get(
            f"{self.api_url}/groups/{quote(key, safe='')}",
            headers=self.headers,
        )
        if r.status_code == 200 and r.text.strip():
            try:
                data = r.json()
                gid = data.get("id")
                if isinstance(gid, int):
                    return gid
            except Exception:
                pass

        # 2) Fallback robusto:
        #    - Si viene un full_path (tiene '/'), el search por ese string puede devolver []
        #      En ese caso buscamos por el último segmento (path) y filtramos por full_path exacto.
        search_term = key.split("/")[-1] if "/" in key else key

        response = requests.get(
            f"{self.api_url}/groups",
            headers=self.headers,
            params={"search": search_term},
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            raise RuntimeError(
                f"GitLab API devolvió HTML en lugar de JSON.\n"
                f"Revisa POCIT_GITLAB_URL (debe ser base GitLab, no URL del grupo).\n"
                f"URL usada: {self.api_url}\n"
                f"Content-Type: {content_type}\n"
                f"Body: {response.text[:200]}"
            )

        if not response.text.strip():
            raise RuntimeError(f"Respuesta vacía al obtener grupos (status={response.status_code})")

        groups = response.json()
        target = key.lower().strip("/")

        for group in groups:
            fp = str(group.get("full_path") or "").lower().strip("/")
            if fp == target:
                gid = group.get("id")
                if isinstance(gid, int):
                    return gid

        raise RuntimeError(
            f"No se pudo resolver el namespace '{self.namespace_path}'. "
            f"Si es un subgrupo, usa full_path (ej: 'FORTEFORTE/poc-generated')."
        )

    def _create_project(self, name: str, namespace_id: Optional[int]) -> dict:
        """Crea un nuevo proyecto en GitLab.

        Si namespace_id es None, GitLab lo creará en el namespace del usuario asociado al token.
        """
        # GitLab valida `name` con un set de caracteres permitido. Como el nombre de PoC puede venir
        # con símbolos “en-dash” (–), comillas tipográficas, etc., normalizamos y filtramos.
        safe_name = name or "poc-it"

        # 1) Normalización a ASCII (quita tipografía rara como “–”)
        safe_name = unicodedata.normalize("NFKD", safe_name).encode("ascii", "ignore").decode("ascii")

        # 2) Reemplaza separadores/cualquier cosa rara por espacio
        # Nota: en character class, '-' debe ir al final o escapado correctamente.
        safe_name = re.sub(r"[^A-Za-z0-9_.+ -]+", " ", safe_name)

        # 3) Colapsa espacios
        safe_name = re.sub(r"\s+", " ", safe_name).strip()

        # 4) Debe empezar por letra/dígito/'_' (GitLab también acepta emoji, pero aquí trabajamos en ASCII)
        safe_name = re.sub(r"^[^A-Za-z0-9_]+", "", safe_name).strip()

        # 5) Si queda vacío, fallback seguro
        if not safe_name:
            safe_name = "poc-it"

        payload = {
            "name": safe_name,
            # `path` explícito para evitar 400 por caracteres inválidos / slug derivado.
            "path": _slugify_path(safe_name),
            "visibility": "private",
            "initialize_with_readme": False,
        }
        if namespace_id is not None:
            payload["namespace_id"] = namespace_id

        response = requests.post(
            f"{self.api_url}/projects",
            headers=self.headers,
            json=payload,
        )

        if response.status_code >= 400:
            # GitLab suele devolver JSON con details del campo inválido; exponemos también name/path saneados.
            raise RuntimeError(
                f"Error al crear proyecto en GitLab (status={response.status_code}). "
                f"Payload.name={payload.get('name')!r} Payload.path={payload.get('path')!r}. "
                f"Body={response.text[:800]}"
            )

        if not response.text.strip():
            raise RuntimeError(f"Respuesta vacía al crear proyecto (status={response.status_code})")
        try:
            return response.json()
        except Exception:
            raise RuntimeError(
                f"Respuesta inválida al crear proyecto (status={response.status_code}): {response.text[:300]}"
            )

    # ==========================================================
    # GIT LOCAL
    # ==========================================================

    def _run_git(self, args: list[str], cwd: Path) -> None:
        subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

    def _push_directory(self, source_dir: Path, remote_url: str) -> None:
        """Inicializa repo local temporal y hace push."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Copiar contenido generado
            shutil.copytree(source_dir, tmp_path / source_dir.name)

            project_path = tmp_path / source_dir.name

            # Inicializar repo
            self._run_git(["init"], project_path)
            self._run_git(["branch", "-M", "main"], project_path)
            self._run_git(["remote", "add", "origin", remote_url], project_path)
            self._run_git(["add", "."], project_path)
            self._run_git(["commit", "-m", "Initial commit generado por PoC-it"], project_path)

            # Autenticación vía token HTTP
            auth_url = remote_url.replace(
                "https://",
                f"https://oauth2:{self.token}@"
            )

            self._run_git(["push", "-u", auth_url, "main"], project_path)

    # ==========================================================
    # MÉTODO PÚBLICO
    # ==========================================================

    def publicar(self, nombre_proyecto: str, ruta_poc: str) -> Optional[str]:
        """
        Crea repositorio y publica contenido.

        Devuelve URL del repositorio si todo va bien.
        Si falla, devuelve None.

        Estrategia robusta:
        - Intentar crear en grupo configurado.
        - Si GitLab responde `namespace is not valid`, fallback a crear en el namespace del usuario (sin namespace_id).
        """
        try:
            group_id = self._get_group_id()
            try:
                project_data = self._create_project(nombre_proyecto, group_id)
            except Exception as e:
                msg = str(e)

                # Si el namespace configurado no es válido, NO intentamos fallback a personal
                # porque en esta instancia está prohibido (403) y además oculta el problema real.
                if "namespace" in msg and "is not valid" in msg:
                    raise RuntimeError(
                        "No se pudo crear el proyecto en el grupo/subgrupo configurado (namespace_id inválido).\n"
                        "Causas típicas:\n"
                        "- El token no tiene permisos sobre ese grupo/subgrupo.\n"
                        "- El ID no corresponde a un namespace accesible para el token.\n\n"
                        "Acciones:\n"
                        "1) Verifica que el token pertenece a un usuario con rol Developer/Maintainer en el subgrupo.\n"
                        "2) Confirma el ID real del namespace con un usuario que sí lo vea.\n"
                        "3) Si no puedes listar grupos por API, usa `POCIT_GITLAB_GROUP_ID` pero debe ser un ID válido y accesible.\n"
                    ) from e

                # Fallback legacy solo si el error es otro (p.ej. slug inválido) y queremos reintentar,
                # pero nunca para errores explícitos de namespace.
                raise

            remote_url = project_data["http_url_to_repo"]
            web_url = project_data["web_url"]

            self._push_directory(Path(ruta_poc), remote_url)

            return web_url

        except Exception as e:
            print("\n[GitLabPublisher] Error durante publicación:")
            print(str(e))
            return None
