from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Set, Tuple


@dataclass(frozen=True, slots=True)
class GuardrailsResult:
    ok: bool
    errors: List[str]
    repair_paths: List[str]
    warnings: List[str]


def extraer_errores_por_archivo(errores: List[str]) -> Dict[str, List[str]]:
    """
    Agrupa errores tipo "<path>: ..." por path.

    Nota: asume que el formato de error usa ':' como separador entre path y mensaje.
    """
    out: Dict[str, List[str]] = {}
    for e in errores or []:
        s = str(e)
        if ":" not in s:
            continue
        p, rest = s.split(":", 1)
        p = p.strip().replace("\\", "/")
        if not p:
            continue
        out.setdefault(p, []).append(rest.strip())
    return out


def seleccionar_error_bloqueante(errores: List[str]) -> Tuple[str, str] | None:
    """
    Selecciona un único error "prioritario" para reparación atómica.
    Devuelve (path, mensaje) si puede; si no, None.
    """
    by_file = extraer_errores_por_archivo(errores)

    # Heurística simple y estable:
    # - priorizar archivos de config/DB (suelen disparar security/env)
    # - luego endpoints/services
    preferred_prefixes = (
        "app/config/",
        "app/db",
        "app/settings",
        "app/endpoints/",
        "app/services/",
    )
    for pref in preferred_prefixes:
        for p, msgs in by_file.items():
            if p.startswith(pref) or p == "app/db.py":
                return p, msgs[0]

    for p, msgs in by_file.items():
        return p, msgs[0]
    return None


def guardrails_por_spec(spec: dict, files_generados: List[Dict[str, str]]) -> GuardrailsResult:
    """
    Guardrails genéricos basados en SPEC (sin conocimiento de dominio).

    Reglas:
    - No endpoints extra (routers incluidos y decorators deben corresponder al SPEC)
    - Enforce request.type json vs multipart (UploadFile/File/python-multipart)
    - Logging obligatorio: si hay 'except Exception' debe haber logger.exception en el mismo fichero
      (BLOCK para endpoints/services; WARNING en el resto)
    - Respuesta coherente con response.json_example (heurística suave, solo request.type=none)
    - Enforce restricciones declaradas en SPEC.restrictions (con severidad BLOCK/WARN)

    Devuelve GuardrailsResult con:
    - ok: True si no hay errores bloqueantes
    - errors: lista de errores bloqueantes
    - repair_paths: lista de paths recomendados para repair
    - warnings: lista de warnings
    """
    errores: List[str] = []
    warnings: List[str] = []
    reparar: Set[str] = set()

    # --- índice path->content ---
    by_path: Dict[str, str] = {
        (f.get("path") or "").replace("\\", "/"): (f.get("content") or "")
        for f in files_generados
        if isinstance(f, dict) and f.get("path")
    }

    # --- endpoints esperados del SPEC ---
    spec_eps = spec.get("endpoints", [])
    expected_ep_files: Set[str] = set()
    expected_ep_paths: Set[Tuple[str, str]] = set()  # (METHOD, "/path")
    request_type_by_path: Dict[str, str] = {}
    response_example_by_path: Dict[str, Any] = {}

    if isinstance(spec_eps, list):
        for ep in spec_eps:
            if not isinstance(ep, dict):
                continue
            ep_file = str(ep.get("file") or "").replace("\\", "/")
            if ep_file:
                expected_ep_files.add(ep_file)

            method = str(ep.get("method") or "").upper().strip()
            path = str(ep.get("path") or "").strip()
            if method and path:
                expected_ep_paths.add((method, path))
                req = ep.get("request") or {}
                if isinstance(req, dict):
                    rt = str(req.get("type") or "").strip()
                    if rt:
                        request_type_by_path[path] = rt
                resp = ep.get("response") or {}
                if isinstance(resp, dict) and "json_example" in resp:
                    response_example_by_path[path] = resp.get("json_example")

    # --- 1) main.py: include_router debe incluir SOLO routers de endpoints listados ---
    main_src = by_path.get("app/main.py", "")
    if main_src:
        # Heurística: si main.py importa app.endpoints.X y X.py no está en spec -> error
        for m in re.findall(
            r"from\s+app\.endpoints\.([a-zA-Z0-9_]+)\s+import\s+router",
            main_src,
        ):
            f = f"app/endpoints/{m}.py"
            if expected_ep_files and f not in expected_ep_files:
                errores.append(f"Endpoint extra no listado en SPEC (importado en main.py): {f}")
                reparar.add("app/main.py")

    # --- 2) Enforce request.type=json vs multipart ---
    # - Si el SPEC declara explícitamente request.type por endpoint, lo aplicamos por path.
    # - Si el SPEC NO lo declara (modelo incompleto), evitamos falsos positivos y NO bloqueamos la generación.
    any_multipart_endpoint = any(rt == "multipart" for rt in request_type_by_path.values())
    
    # Si el SPEC no trae request.type, no podemos validar multipart vs json.
    has_request_types = len(request_type_by_path) > 0

    if has_request_types:
        # Prohíbe multipart global si no hay ningún endpoint multipart declarado.
        # Para reducir intermitencias, solo lo aplicamos cuando el SPEC declara
        # al menos un endpoint JSON (es decir, sabemos que NO debe ser multipart).
        any_json_endpoint = any(rt == "json" for rt in request_type_by_path.values())

        if any_json_endpoint:
            for p, src in by_path.items():
                if not p.startswith("app/endpoints/") or not p.endswith(".py"):
                    continue
                uses_uploadfile = "UploadFile" in src or "File(" in src
                if uses_uploadfile and not any_multipart_endpoint:
                    errores.append(
                        "Se detecta multipart (UploadFile/File) pero el SPEC declara endpoints json y no declara multipart: "
                        + p
                    )
                    reparar.add(p)
                    # asegurar repair de requirements.txt para eliminar python-multipart
                    reparar.add("requirements.txt")

        # requirements: python-multipart solo si existe endpoint multipart en SPEC
        reqs = by_path.get("requirements.txt", "")
        if reqs:
            has_multipart = any(
                line.strip().lower() == "python-multipart" for line in reqs.splitlines()
            )
            if has_multipart and not any_multipart_endpoint:
                errores.append(
                    "requirements.txt incluye python-multipart pero el SPEC no declara endpoints multipart."
                )
                reparar.add("requirements.txt")

    # --- 3) Logging obligatorio: except Exception -> logger.exception ---
    # Punto medio: WARNING por defecto; BLOCK solo en endpoints/servicios donde la trazabilidad es crítica.
    for p, src in by_path.items():
        if not p.endswith(".py"):
            continue
        if "except Exception" in src and "logger.exception" not in src:
            msg = f"Falta logging obligatorio (logger.exception) en: {p}"
            if p.startswith(("app/endpoints/", "app/services/")):
                errores.append(msg)
                reparar.add(p)
            else:
                warnings.append(msg)

    # --- 4) Respuesta coherente con json_example (heurística) ---
    for path, json_example in response_example_by_path.items():
        request_type = request_type_by_path.get(path)
        if request_type != "none":
            continue
        if not isinstance(json_example, dict) or not json_example:
            continue

        expected_keys = [str(k) for k in json_example.keys()]

        for file_path, source_code in by_path.items():
            if not file_path.startswith("app/endpoints/") or not file_path.endswith(".py"):
                continue
            if path not in source_code or "@router" not in source_code:
                continue

            # Si el endpoint NO menciona ninguna key del ejemplo, es muy probable que no cumpla el contrato.
            mentions_any_key = any(
                f"\"{key}\"" in source_code or f"'{key}'" in source_code
                for key in expected_keys
            )
            if not mentions_any_key:
                errores.append(
                    f"Endpoint {path} no parece incluir ninguna de las claves esperadas {expected_keys} (json_example del SPEC): {file_path}"
                )
                reparar.add(file_path)

    # --- 5) Enforce restricciones genéricas (SPEC.restrictions) ---
    def _match_glob(path: str, pattern: str) -> bool:
        if pattern == "*" or not pattern:
            return True
        if pattern.startswith("*") and pattern.endswith("*"):
            return pattern.strip("*") in path
        if pattern.startswith("*"):
            return path.endswith(pattern[1:])
        if pattern.endswith("*"):
            return path.startswith(pattern[:-1])
        return path == pattern

    def _is_regex(s: str) -> bool:
        return isinstance(s, str) and s.startswith("re:")

    def _needle_matches(src: str, needle: str) -> bool:
        """
        Matcheo flexible para restricciones:
        - Por defecto: substring literal
        - Si needle empieza por 're:': se interpreta como regex (Python re)
        """
        if not isinstance(needle, str) or needle == "":
            return False
        if _is_regex(needle):
            pattern = needle[3:]
            try:
                return re.search(pattern, src) is not None
            except re.error:
                return pattern in src
        return needle in src

    def _applies(path: str, patterns: Iterable[str]) -> bool:
        return any(_match_glob(path, p) for p in patterns)

    restrictions = spec.get("restrictions", [])
    if isinstance(restrictions, list) and restrictions:
        for r in restrictions:
            if not isinstance(r, dict):
                continue

            rid = str(r.get("id") or "restriction").strip()
            applies_to = r.get("applies_to")
            if not isinstance(applies_to, list) or not applies_to:
                applies_to = ["*"]

            severity = str(r.get("severity") or "").strip().upper()
            if severity not in ("BLOCK", "WARN"):
                severity = "BLOCK"

            must_not = r.get("must_not_contain") or []
            must_any = r.get("must_contain_any") or []

            if not isinstance(must_not, list):
                must_not = [str(must_not)]
            if not isinstance(must_any, list):
                must_any = [str(must_any)]

            for p, src in by_path.items():
                if not _applies(p, applies_to):
                    continue

                # must_not_contain
                for needle in must_not:
                    if not needle:
                        continue
                    if _needle_matches(src, str(needle)):
                        msg = f"{p}: viola restriction '{rid}': contiene '{needle}'"
                        if severity == "BLOCK":
                            errores.append(msg)
                            reparar.add(p)
                        else:
                            warnings.append(msg)

                # must_contain_any (si se define, al menos uno debe aparecer)
                if must_any:
                    if not any(_needle_matches(src, str(needle)) for needle in must_any):
                        msg = f"{p}: viola restriction '{rid}': no contiene ninguno de {must_any}"
                        if severity == "BLOCK":
                            errores.append(msg)
                            reparar.add(p)
                        else:
                            warnings.append(msg)

    ok = len(errores) == 0
    return GuardrailsResult(ok=ok, errors=errores, repair_paths=sorted(reparar), warnings=warnings)
