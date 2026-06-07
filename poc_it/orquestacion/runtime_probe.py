from __future__ import annotations
"""
Runtime probe hermético (request-time) para FastAPI.

Motivación
----------
`runtime_verify_fastapi_project` valida import-time. Sin embargo, muchos fallos reales
solo aparecen al ejecutar handlers (NameError, AttributeError, mal cableado de Depends, etc.).

Este probe ejecuta, en un proceso separado, una batería mínima:
- /openapi.json (siempre)
- y, para endpoints seleccionados en runtime_contracts (normalmente herméticos),
  realiza una request con overrides de dependencias permitidas.

NO levanta uvicorn. Usa TestClient. El objetivo es obtener un traceback accionable
para alimentar el loop de reparación LLM sobre app/**.
"""

import json
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional
import os

from poc_it.runtime_contracts import RUNTIME_CONTRACTS_PATH


@dataclass(frozen=True)
class RuntimeProbeResult:
    ok: bool
    detail: str


def _safe_json_loads(s: str) -> Optional[dict]:
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _safe_extract_marker_json(out: str, marker: str) -> Optional[dict]:
    """Extrae JSON tras un marker aunque exista texto adicional después.

    Strategy:
    - coger substring tras marker
    - intentar parsear directo
    - si falla, recortar hasta último '}' y reintentar (best-effort)
    """
    if not out or marker not in out:
        return None
    tail = out.split(marker, 1)[1].strip()
    obj = _safe_json_loads(tail)
    if isinstance(obj, dict):
        return obj
    # best-effort: recortar hasta el último '}' (evitar stderr/traces después).
    # OJO: si el JSON tiene nuevas líneas después (p.ej. "MARKER: {..}\\nOTHER_MARKER: ..."),
    # el rfind('}') sigue valiendo, pero el substring puede incluir otro marker antes del último }.
    # Así que recortamos también al primer salto de línea si ayuda.
    try:
        # 1) Intento: hasta último '}'
        end = tail.rfind("}")
        if end != -1:
            cand = tail[: end + 1]
            obj2 = _safe_json_loads(cand)
            if isinstance(obj2, dict):
                return obj2
        # 2) Intento: hasta el primer salto de línea (marker en línea única)
        nl = tail.find("\n")
        if nl != -1:
            obj3 = _safe_json_loads(tail[:nl].strip())
            return obj3 if isinstance(obj3, dict) else None
    except Exception:
        return None
    return None


def run_runtime_probe(
    *,
    project_dir: str,
    estructura: Dict[str, str],
    max_endpoints: int = 3,
) -> RuntimeProbeResult:
    """
    Ejecuta un probe request-time. Selecciona endpoints desde runtime_contracts:
    - Solo endpoints con depends_imports no vacío (para poder overridear herméticamente).
    - Limita a max_endpoints para evitar probes largos.

    Devuelve ok=False y detail con stdout/stderr si:
    - falla import app.main
    - openapi != 200
    - una request a endpoint seleccionado devuelve >=500 o lanza excepción
    """
    rc_raw = estructura.get(RUNTIME_CONTRACTS_PATH) or ""
    rc = _safe_json_loads(rc_raw) if isinstance(rc_raw, str) and rc_raw.strip() else None
    endpoints: List[dict] = []
    allow: Optional[List[str]] = None
    if isinstance(rc, dict):
        eps = rc.get("endpoints") or []
        if isinstance(eps, list):
            endpoints = [e for e in eps if isinstance(e, dict)]
        al = rc.get("allowed_dependency_overrides")
        if isinstance(al, list):
            allow = [str(x) for x in al if str(x).strip()]
        else:
            # Compat: si no viene allowlist, derivarla de depends_imports (mejor que vaciarla).
            derived: List[str] = []
            for ep in endpoints:
                deps = ep.get("depends_imports") or []
                if isinstance(deps, list):
                    for d in deps:
                        s = str(d).strip()
                        if s:
                            derived.append(s)
            allow = sorted(set(derived)) if derived else None

    # Pre-filtrado: solo endpoints overrideables (depends_imports no vacío)
    selected: List[dict] = []
    for ep in endpoints:
        deps = ep.get("depends_imports") or []
        if not isinstance(deps, list) or not deps:
            continue
        if allow is not None and allow:
            if not all(str(d) in allow for d in deps):
                continue
        selected.append(ep)
        if len(selected) >= max_endpoints:
            break

    payload = json.dumps({"endpoints": selected}, ensure_ascii=False)

    code = r"""
import json
import traceback
from fastapi.testclient import TestClient

# ---- Stub instrumentation (for stub_signatures.json) ----
_STUB_SIG = {"deps": {}}

def _sig_touch(dep_fqn: str, member: str, *, kind: str, awaited: bool | None = None, chain: list[str] | None = None):
    d = _STUB_SIG["deps"].setdefault(dep_fqn, {"members": {}, "chains": []})
    m = d["members"].setdefault(member, {"kind": kind, "awaited_true": 0, "awaited_false": 0})
    if awaited is True:
        m["awaited_true"] += 1
    elif awaited is False:
        m["awaited_false"] += 1
    if chain:
        d["chains"].append({"chain": list(chain)})

class _StubAttrProxy:
    # Proxy/stub TOTAL.
    # Objetivo: que el probe NO aborte aunque el código llame/await-e miembros arbitrarios.
    # Estrategia: cualquier attr/call/await devuelve otro proxy, nunca None.

    def __init__(self, dep_fqn: str, chain: list[str] | None = None):
        self._dep_fqn = dep_fqn
        self._chain = list(chain or [])

    def __getattr__(self, name: str):
        _sig_touch(self._dep_fqn, name, kind="attr", awaited=None, chain=self._chain + [name])
        return _StubAttrProxy(self._dep_fqn, self._chain + [name])

    def __call__(self, *args, **kwargs):
        # Llamada sync. Devolvemos proxy para permitir chaining (p.ej. svc.client.files().create()).
        _sig_touch(self._dep_fqn, "__call__", kind="call", awaited=False, chain=self._chain)
        return _StubAttrProxy(self._dep_fqn, self._chain + ["__call__"])

    def __await__(self):
        # Await -> devolvemos proxy (no None) para evitar NoneType can't be awaited
        _sig_touch(self._dep_fqn, "__call__", kind="call", awaited=True, chain=self._chain)
        async def _noop():
            return _StubAttrProxy(self._dep_fqn, self._chain + ["__await__"])
        return _noop().__await__()

def _mk_stub(dep_fqn: str):
    return _StubAttrProxy(dep_fqn)

def _override_callable(target_fqn: str):
    # import FQN callable: a.b.c.fn
    mod, _, attr = target_fqn.rpartition(".")
    if not mod or not attr:
        raise ValueError(f"Bad FQN: {target_fqn}")
    m = __import__(mod, fromlist=[attr])
    return getattr(m, attr)

def _extract_params_from_openapi(openapi: dict, path: str, method: str):
    # Fuente estable: OpenAPI generado por FastAPI
    out = {
        "path_params": [],
        "query_params_required": [],
        "query_params_optional": [],
        "request_required_fields": [],
        "request_body_param": None,
        # Tipos de fields del body (best-effort) para construir payloads que eviten 422 pre-handler
        "request_field_types": {},
        # response hints (para asserts)
        "response_media_type": None,
        "response_json_shape": None,
        "response_json_required_keys": [],
        # nuevos: ejemplos/policy para alinear tests+stubs+repair (modo PARCIAL)
        "sample_request": None,
        "sample_response": None,
        "assert_policy": "shape_only",
    }
    try:
        op = (((openapi or {}).get("paths") or {}).get(path) or {}).get(method.lower()) or {}
        params = op.get("parameters") or []
        if isinstance(params, list):
            for p in params:
                if not isinstance(p, dict):
                    continue
                name = str(p.get("name") or "").strip()
                where = str(p.get("in") or "").strip()
                req = bool(p.get("required", False))
                if not name or not where:
                    continue
                if where == "path":
                    out["path_params"].append(name)
                elif where == "query":
                    (out["query_params_required"] if req else out["query_params_optional"]).append(name)

        rb = op.get("requestBody") or {}
        content = (rb.get("content") or {}) if isinstance(rb, dict) else {}
        app_json = content.get("application/json") if isinstance(content, dict) else None
        schema = (app_json or {}).get("schema") if isinstance(app_json, dict) else None

        # Si el requestBody es $ref a un schema con required, lo resolvemos best-effort
        def _resolve_ref(s):
            if not isinstance(s, dict):
                return None
            ref = s.get("$ref")
            if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
                return s
            key = ref.split("/")[-1]
            return (((openapi or {}).get("components") or {}).get("schemas") or {}).get(key)

        schema = _resolve_ref(schema) if schema else None
        if isinstance(schema, dict) and schema.get("type") == "object":
            req = schema.get("required") or []
            if isinstance(req, list):
                out["request_required_fields"] = [str(x) for x in req if str(x).strip()]
            props = schema.get("properties") or {}
            if isinstance(props, dict) and props:
                # extraer tipos por propiedad (best-effort)
                for k, v in props.items():
                    if not isinstance(k, str) or not isinstance(v, dict):
                        continue
                    t = v.get("type")
                    if isinstance(t, str) and t:
                        out["request_field_types"][k] = t

            if out["request_required_fields"] or props:
                out["request_body_param"] = "body"

            # sample_request: ejemplo mínimo para evitar 422 (usa required si existe; si no, 1-3 props)
            sample_req = {}
            keys = out["request_required_fields"] if out["request_required_fields"] else list(props.keys())[:3]
            def _dummy_for_type(t: str):
                t = (t or "").lower().strip()
                if t in ("integer", "int"):
                    return 1
                if t in ("number", "float"):
                    return 1.0
                if t in ("boolean", "bool"):
                    return True
                return "x"
            for k in keys:
                tk = str(k)
                sample_req[tk] = _dummy_for_type(str(out["request_field_types"].get(tk, "string")))
            out["sample_request"] = sample_req if sample_req else None

        # Response hints (200/201 -> application/json schema)
        responses = op.get("responses") or {}
        resp2xx = None
        for code in ("200", "201", "202", "204"):
            if isinstance(responses, dict) and code in responses:
                resp2xx = responses.get(code)
                break
        if isinstance(resp2xx, dict):
            c2 = (resp2xx.get("content") or {}) if isinstance(resp2xx.get("content"), dict) else {}
            if c2:
                # prefer application/json si existe
                mt = "application/json" if "application/json" in c2 else next(iter(c2.keys()), None)
                if mt and isinstance(c2.get(mt), dict):
                    out["response_media_type"] = str(mt)
                    s2 = c2.get(mt, {}).get("schema")
                    s2 = _resolve_ref(s2) if s2 else None
                    if isinstance(s2, dict):
                        t = s2.get("type")
                        if isinstance(t, str):
                            out["response_json_shape"] = t
                        elif "$ref" in s2:
                            out["response_json_shape"] = "object"
                        if out["response_json_shape"] == "object":
                            reqk = s2.get("required") or []
                            props = s2.get("properties") or {}
                            if isinstance(reqk, list) and reqk:
                                out["response_json_required_keys"] = [str(x) for x in reqk if str(x).strip()]
                            elif isinstance(props, dict) and props:
                                # fallback: keys conocidas (no sabemos required)
                                out["response_json_required_keys"] = [str(x) for x in list(props.keys())[:8]]

                            # sample_response: solo claves "required" si existen; si no, 1-4 props
                            keys = out["response_json_required_keys"] if out["response_json_required_keys"] else list(props.keys())[:4]
                            sample_resp = {str(k): "x" for k in keys if str(k).strip()}
                            out["sample_response"] = sample_resp if sample_resp else None
                    elif out["response_media_type"] == "application/json":
                        out["response_json_shape"] = "unknown"
    except Exception:
        pass
    return out

def _extract_params_from_signature(app, path: str, method: str, path_params: list[str] | None = None):
    # Fallback estable (sin internals frágiles): inspección de firma del endpoint.
    # Regla: si es escalar y no tiene default => query required.
    try:
        import inspect
        from fastapi.routing import APIRoute
        from fastapi.params import Depends as _Depends
        from pydantic import BaseModel
    except Exception:
        return {"query_params_required": [], "request_required_fields": [], "request_body_param": None}

    path_params = [str(x) for x in (path_params or []) if str(x).strip()]
    for r in getattr(app, "routes", []) or []:
        try:
            if not isinstance(r, APIRoute):
                continue
            if getattr(r, "path", None) != path:
                continue
            methods = {m.upper() for m in (getattr(r, "methods", None) or set())}
            if method.upper() not in methods:
                continue
            endpoint = getattr(r, "endpoint", None)
            if endpoint is None:
                break

            sig = inspect.signature(endpoint)
            q_required: list[str] = []
            req_fields: list[str] = []
            body_param = None

            for name, param in sig.parameters.items():
                if name in path_params:
                    continue
                # depende de FastAPI: Dependencias suelen tener default=Depends(...)
                default = param.default
                if isinstance(default, _Depends):
                    continue

                ann = param.annotation
                if ann is inspect._empty:
                    ann = None

                is_required = default is inspect._empty

                # Body model
                try:
                    if ann and isinstance(ann, type) and issubclass(ann, BaseModel):
                        body_param = "body"
                        # requeridos del modelo
                        fields = getattr(ann, "model_fields", None) or getattr(ann, "__fields__", None) or {}
                        if isinstance(fields, dict):
                            for fname, finfo in fields.items():
                                req = False
                                if hasattr(finfo, "is_required"):
                                    req = bool(finfo.is_required())
                                elif hasattr(finfo, "required"):
                                    req = bool(getattr(finfo, "required"))
                                if req:
                                    req_fields.append(str(fname))
                        continue
                except Exception:
                    pass

                # Scalar required => query
                if is_required and ann in (str, int, float, bool):
                    q_required.append(name)

            return {
                "query_params_required": q_required,
                "request_required_fields": req_fields,
                "request_body_param": body_param,
            }
        except Exception:
            continue

    return {"query_params_required": [], "request_required_fields": [], "request_body_param": None}

def main():
    try:
        from app.main import app
    except Exception as e:
        print("IMPORT_ERROR:", repr(e))
        traceback.print_exc()
        raise

    c = TestClient(app)

    # OpenAPI probe
    r = c.get("/openapi.json")
    print("OPENAPI_STATUS:", r.status_code)
    if r.status_code >= 500:
        raise RuntimeError(f"openapi failed: {r.status_code}")

    selected = json.loads(r'''__ENDPOINTS_JSON__''') or {}
    eps = selected.get("endpoints") or []

    openapi = {}
    try:
        openapi = r.json() if hasattr(r, "json") else {}
    except Exception:
        openapi = {}

    # Apply overrides if needed per endpoint
    # We clear overrides for each request to avoid cross-test contamination.
    #
    # IMPORTANT (PARCIAL): este probe es best-effort. Nunca debe abortar por un endpoint.
    errors = []
    for ep in eps:
        path = ep.get("path")
        method = (ep.get("method") or "GET").upper()
        deps_imports = ep.get("depends_imports") or []

        # Enriquecer facts del endpoint (solo para shaping determinista)
        facts = _extract_params_from_openapi(openapi, path, method)
        ep.setdefault("path_params", facts.get("path_params") or [])
        ep.setdefault("query_params_required", facts.get("query_params_required") or [])
        ep.setdefault("query_params_optional", facts.get("query_params_optional") or [])
        ep.setdefault("request_required_fields", facts.get("request_required_fields") or [])
        ep.setdefault("request_body_param", facts.get("request_body_param"))
        ep.setdefault("response_media_type", facts.get("response_media_type"))
        ep.setdefault("response_json_shape", facts.get("response_json_shape"))
        ep.setdefault("response_json_required_keys", facts.get("response_json_required_keys") or [])

        # Fallback: si OpenAPI no declara query/body, inferir desde signature
        if not (ep.get("query_params_required") or ep.get("request_required_fields")):
            fs = _extract_params_from_signature(app, path, method, ep.get("path_params") or [])
            if fs.get("query_params_required"):
                ep["query_params_required"] = fs.get("query_params_required")
            if fs.get("request_required_fields"):
                ep["request_required_fields"] = fs.get("request_required_fields")
            if fs.get("request_body_param"):
                ep["request_body_param"] = fs.get("request_body_param")

        app.dependency_overrides = {}
        for fqn in deps_imports:
            try:
                dep = _override_callable(fqn)
                async def _override(_fqn=fqn):
                    return _mk_stub(_fqn)
                app.dependency_overrides[dep] = _override
            except Exception as e:
                print("OVERRIDE_ERROR:", fqn, repr(e))
                traceback.print_exc()
                raise

        # request shaping determinista:
        # - si hay query params requeridos: enviarlos
        # - si hay body requerido: enviar json con campos dummy
        #
        # En PARCIAL, si falla (>=500 o excepción), lo registramos y continuamos para poder
        # emitir ENDPOINT_FACTS_JSON / STUB_SIGNATURES_JSON al final.
        try:
            params = None
            json_body = None

            qreq = ep.get("query_params_required") or []
            if isinstance(qreq, list) and qreq:
                params = {str(k): "x" for k in qreq}

            req_fields = ep.get("request_required_fields") or []
            if isinstance(req_fields, list) and req_fields:
                # payload type-aware para reducir 422 pre-handler (si podemos)
                ftypes = ep.get("request_field_types") or {}
                if not isinstance(ftypes, dict):
                    ftypes = {}
                def _dummy_for_type(t: str):
                    t = (t or "").lower().strip()
                    if t in ("integer", "int"):
                        return 1
                    if t in ("number", "float"):
                        return 1.0
                    if t in ("boolean", "bool"):
                        return True
                    # string/default
                    return "x"
                json_body = {str(k): _dummy_for_type(str(ftypes.get(str(k), "string"))) for k in req_fields}

            if method in ("POST", "PUT", "PATCH"):
                resp = c.request(method, path, params=params, json=json_body)
            else:
                resp = c.request(method, path, params=params)

            print("EP_STATUS:", method, path, resp.status_code)
            if resp.status_code >= 500:
                errors.append({
                    "method": method,
                    "path": path,
                    "status_code": int(getattr(resp, "status_code", 0) or 0),
                    "detail": (getattr(resp, "text", "") or "")[:400],
                })
        except Exception as e:
            errors.append({
                "method": method,
                "path": path,
                "status_code": None,
                "exception": repr(e),
            })
        finally:
            app.dependency_overrides = {}

    # Emitimos los endpoints enriquecidos como JSON para inspección (stdout)
    print("ENDPOINT_FACTS_JSON:", json.dumps({"endpoints": eps}, ensure_ascii=False))
    # Emitimos también signatures de stubs (stdout) para persistir como artefacto.
    # Fallback: si no se tocó ninguna dep pero sí había endpoints seleccionados con observed_calls,
    # rellenamos members desde observed_calls para no dejar deps vacío.
    try:
        if (not _STUB_SIG.get("deps")) and isinstance(eps, list) and eps:
            for ep in eps:
                if not isinstance(ep, dict):
                    continue
                deps_imports = ep.get("depends_imports") or []
                calls = ep.get("observed_calls") or []
                if not isinstance(deps_imports, list) or not isinstance(calls, list):
                    continue
                # usamos el primer depends_import como "dep principal" (típicamente get_service)
                for dep_fqn in deps_imports:
                    dep_fqn = str(dep_fqn).strip()
                    if not dep_fqn:
                        continue
                    for cinfo in calls:
                        if not isinstance(cinfo, dict):
                            continue
                        mname = str(cinfo.get("method_name") or "").strip()
                        awaited = bool(cinfo.get("awaited", False))
                        if not mname:
                            continue
                        # registrar "touch" sintético (call)
                        _sig_touch(dep_fqn, mname, kind="call", awaited=awaited, chain=[mname])
    except Exception:
        pass
    print("STUB_SIGNATURES_JSON:", json.dumps(_STUB_SIG, ensure_ascii=False))
    # Errores por endpoint (best-effort). Útil para diagnóstico posterior sin romper el probe.
    print("ENDPOINT_ERRORS_JSON:", json.dumps({"errors": errors}, ensure_ascii=False))

if __name__ == "__main__":
    main()
""".replace("__ENDPOINTS_JSON__", payload.replace("\\", "\\\\").replace("'", "\\'"))

    # Importante: ejecutar el probe con el Python del venv del proyecto generado si existe,
    # para evitar falsos negativos en máquinas donde PoC-it no tiene instaladas las deps del proyecto.
    venv_py = os.path.join(project_dir, ".poc_it", "venv", "Scripts", "python.exe")
    if not os.path.exists(venv_py):
        venv_py = os.path.join(project_dir, ".poc_it", "venv", "bin", "python")
    py = venv_py if os.path.exists(venv_py) else "python"

    p = subprocess.run(
        [py, "-c", code],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    if p.returncode != 0:
        return RuntimeProbeResult(ok=False, detail=out)

    # Si el probe emitió facts enriquecidos, los propagamos a runtime_contracts en memoria
    # para que la generación/repair de tests pueda decidir params vs json sin heurísticas.
    try:
        # Persistimos stub_signatures como artefacto (best-effort) siempre que esté el marker,
        # independientemente de si ENDPOINT_FACTS_JSON es parseable.
        try:
            sobj = _safe_extract_marker_json(out, "STUB_SIGNATURES_JSON:")
            if isinstance(sobj, dict):
                estructura[".poc_it/stub_signatures.json"] = json.dumps(sobj, ensure_ascii=False, indent=2)
        except Exception:
            pass

        marker = "ENDPOINT_FACTS_JSON:"
        if marker in out and isinstance(rc, dict) and endpoints:
            facts_obj = _safe_extract_marker_json(out, marker)
            if isinstance(facts_obj, dict):
                facts_eps = facts_obj.get("endpoints") or []
                if isinstance(facts_eps, list):
                    # Merge por (method,path)
                    by_key = {}
                    for fep in facts_eps:
                        if not isinstance(fep, dict):
                            continue
                        k = (str(fep.get("method") or "").upper(), str(fep.get("path") or ""))
                        if k[0] and k[1]:
                            by_key[k] = fep

                    merged = []
                    for ep in endpoints:
                        if not isinstance(ep, dict):
                            continue
                        k = (str(ep.get("method") or "").upper(), str(ep.get("path") or ""))
                        fep = by_key.get(k)
                        if fep:
                            # Copiamos campos de binding request + hints response (no tocamos deps/calls)
                            for fld in (
                                "query_params_required",
                                "query_params_optional",
                                "request_body_param",
                                "request_required_fields",
                                "request_optional_fields",
                                "response_media_type",
                                "response_json_shape",
                                "response_json_required_keys",
                                "sample_request",
                                "sample_response",
                                "assert_policy",
                            ):
                                if fld in fep:
                                    ep[fld] = fep.get(fld)
                        merged.append(ep)

                    rc["endpoints"] = merged
                    estructura[RUNTIME_CONTRACTS_PATH] = json.dumps(rc, ensure_ascii=False, indent=2)
    except Exception:
        # Best-effort: no romper el pipeline por el merge de facts
        pass

    return RuntimeProbeResult(ok=True, detail=out)
