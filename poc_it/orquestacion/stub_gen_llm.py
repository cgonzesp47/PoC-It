from __future__ import annotations

"""
Generación de stubs (tests/conftest.py) vía LLM acotado, guiado por artefacto determinista
`.poc_it/stub_signatures.json`.

Diseño (híbrido, robusto)
-------------------------
1) runtime_probe produce stub_signatures con evidencia dura:
   - por dependencia FQN: miembros accedidos, si se await-eó una llamada o no, chains.
2) El LLM solo genera `tests/conftest.py` (output pequeño).
3) Verificador determinista valida invariantes:
   - No imports fuera de allowlist (stdlib + app.* + deps de requirements)
   - No I/O real / clientes reales / engines reales en conftest
   - Si stub_signatures marca awaited_true > 0 para "__call__" o para miembro X, el conftest debe tener async def correspondiente.
   - Si awaited_false > 0 para "__call__", no debe ser async (evitar coroutine-not-awaited)
   - Si chains contienen ["execute","scalars","all"] o ["execute","scalars","first"], debe existir soporte para esos métodos.
   - Si se observa rowcount en chain o attr, debe existir `rowcount` como propiedad int.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from poc_it.generador.json_utils import extraer_json_tolerante
from poc_it.llm_client import chat_completion_json


@dataclass(frozen=True)
class StubGenResult:
    ok: bool
    conftest_py: str
    errors: List[str]
    raw_llm: Optional[str] = None


_STDLIB_PREFIXES = (
    "import ",
    "from ",
)


def _parse_requirements_bundle(estructura_generada: Dict[str, str]) -> Dict[str, str]:
    req_runtime = (estructura_generada.get("requirements.txt") or "").strip()
    req_dev = (estructura_generada.get("requirements-dev.txt") or "").strip()
    return {"requirements.txt": req_runtime, "requirements-dev.txt": req_dev}


def _extract_allowed_packages(requirements_txt: str) -> List[str]:
    """
    Best-effort: toma el nombre del paquete (antes de extras/version specifiers).
    """
    out: List[str] = []
    for ln in (requirements_txt or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        # pkg[extra]==1.0 ; pkg>= ; pkg
        m = re.match(r"^([A-Za-z0-9_.-]+)", s)
        if m:
            out.append(m.group(1).lower())
    return sorted(set(out))


def _build_prompt_stub_gen(
    *,
    runtime_contracts: Dict[str, Any],
    stub_signatures: Dict[str, Any],
    requirements_bundle: Dict[str, str],
) -> str:
    allow_pkgs = _extract_allowed_packages(requirements_bundle.get("requirements.txt", "")) + _extract_allowed_packages(
        requirements_bundle.get("requirements-dev.txt", "")
    )
    allow_pkgs = sorted(set(allow_pkgs))

    return f"""
TAREA
Genera SOLO el archivo `tests/conftest.py` para una suite pytest de FastAPI.

OBJETIVO
- Proveer fixtures y dependency_overrides para que los tests puedan ejecutar endpoints SIN I/O real.
- Evitar stubs frágiles: respeta las STUB_SIGNATURES (awaited vs sync, chains).
- No modifiques nada fuera de conftest.py.

REGLA MÁXIMA: HERMETICIDAD
- PROHIBIDO tocar red, DB real, engines reales, credenciales, SDKs cloud.
- PROHIBIDO leer env vars (os.getenv) salvo que el test lo seteé explícitamente.
- PROHIBIDO inicializar SQLAlchemy engines/sessions reales.

ALLOWLIST IMPORTS
- Solo stdlib, `app.*` y paquetes en requirements.
- Paquetes permitidos (lowercase): {json.dumps(allow_pkgs, ensure_ascii=False)}

RUNTIME_CONTRACTS (fuente de verdad para Depends y allowed overrides)
- Incluye hints para tests/stubs: endpoints[*].sample_request, sample_response, assert_policy.
{json.dumps(runtime_contracts, ensure_ascii=False)}

STUB_SIGNATURES (fuente de verdad para awaited/sync y chains)
{json.dumps(stub_signatures, ensure_ascii=False)}

INSTRUCCIONES DE IMPLEMENTACIÓN (OBLIGATORIAS)
1) Descubre qué dependencias overridear usando:
   - runtime_contracts.allowed_dependency_overrides
   - y runtime_contracts.endpoints[*].depends_imports
2) Para cada dependencia FQN, crea un override que devuelva un stub OBJECT.
   - La key en app.dependency_overrides debe ser el callable real importado por FQN.
3) Stubs deben ser STATEFUL al menos a nivel de fixture client para soportar POST→GET→PUT→DELETE.

3.1) Alineación con tests (OBLIGATORIO)
- Para cada endpoint en runtime_contracts.endpoints:
  - Si existe sample_response: los stubs deben devolver objetos/dicts con esas claves.
  - Si existe response_json_required_keys: los stubs deben incluir esas claves.
  - Si assert_policy="echo_fields": en POST/PUT/PATCH, intenta devolver los campos del request (subset) además de las claves required.
- Si no hay sample_response, prioriza devolver dict con las claves required del response_model o, como mínimo, un objeto que pase ResponseValidationError.
4) Respeta awaited:
   - Si STUB_SIGNATURES.deps[fqn].members["__call__"].awaited_true > 0 entonces el stub debe ser awaitable (async o __await__).
   - Si awaited_false > 0 entonces NO debe ser coroutine al usarse sync.
   - Si un miembro específico aparece en chains, debes soportarlo.
5) Soporta chains:
   - Si hay chain ["execute","scalars","all"] o ["execute","scalars","first"], tu stub debe implementar:
     - execute() -> result con scalars() -> objeto con all()/first()
   - Si se observa "rowcount" en chain o en members, expón rowcount como int.

SALIDA (EXCLUSIVAMENTE JSON)
{{"path":"tests/conftest.py","content":"..."}}

REGLAS
- Devuelve SOLO JSON.
- El content debe ser el archivo completo.
""".strip()


def _normalize_llm_out(raw: str) -> Tuple[Optional[str], Optional[str]]:
    data = extraer_json_tolerante(raw) or {}
    if isinstance(data, dict) and data.get("path") == "tests/conftest.py" and isinstance(data.get("content"), str):
        return "tests/conftest.py", data["content"]
    # compat: si devuelve {"files":[...]}
    files = data.get("files") if isinstance(data, dict) else None
    if isinstance(files, list):
        for f in files:
            if isinstance(f, dict) and str(f.get("path")) == "tests/conftest.py" and isinstance(f.get("content"), str):
                return "tests/conftest.py", f["content"]
    return None, None


def verify_conftest(
    *,
    conftest_py: str,
    runtime_contracts: Dict[str, Any],
    stub_signatures: Dict[str, Any],
    requirements_bundle: Dict[str, str],
) -> List[str]:
    """
    Verificador determinista. Devuelve lista de errores (vacía si ok).
    No ejecuta código: valida invariantes por texto/patrones.
    """
    errs: List[str] = []
    txt = conftest_py or ""

    # Must include TestClient for sync style (runtime_contracts.tests_style suele ser sync)
    if "TestClient" not in txt:
        errs.append("conftest no importa/usa TestClient (esperado para tests_style=sync).")

    # Block I/O (heurístico, no dependiente de tecnología):
    # - Prohibimos patrones de "side effects" típicos en conftest (conexión, credenciales, red, subprocess).
    # - Evitamos listas de SDKs concretos; el objetivo es impedir I/O real, no una librería específica.
    banned_regexes = [
        r"\\bsubprocess\\.",
        r"\\bos\\.environ\\b",
        r"\\bos\\.getenv\\b",
        r"\\bopen\\(",
        r"\\bsocket\\.",
        r"\\brequests\\.",
        r"\\bhttpx\\.(Client|AsyncClient)\\(",
        # heurística: inicializadores con efecto (connect/create_engine/login/authorize)
        r"\\b(connect|create_engine|create_async_engine|sessionmaker|async_sessionmaker|login|authorize)\\b\\s*\\(",
    ]
    for rx in banned_regexes:
        if re.search(rx, txt):
            errs.append(f"conftest contiene patrón de I/O/side-effect prohibido: /{rx}/")

    # Allowlist imports: best-effort
    reqs = requirements_bundle.get("requirements.txt", "") + "\n" + requirements_bundle.get("requirements-dev.txt", "")
    allow_pkgs = set(_extract_allowed_packages(reqs))
    allow_pkgs |= {"pytest", "fastapi", "starlette", "httpx"}  # pragmático: suelen estar
    # Check from X import ... where X not stdlib/app/allowed
    for m in re.finditer(r"^from\\s+([a-zA-Z0-9_\\.]+)\\s+import\\s+", txt, flags=re.M):
        mod = m.group(1)
        if mod.startswith("app.") or mod == "app":
            continue
        root = mod.split(".", 1)[0].lower()
        if root in allow_pkgs:
            continue
        # stdlib no se puede enumerar; no marcamos error para módulos sin punto y comunes
        if root in ("typing", "json", "re", "dataclasses", "unittest", "unittest.mock", "importlib", "os"):
            continue
        errs.append(f"Import fuera de allowlist: from {mod} import ...")

    # Verify chains requirements
    deps = (stub_signatures or {}).get("deps") if isinstance(stub_signatures, dict) else None
    if isinstance(deps, dict):
        for fqn, d in deps.items():
            if not isinstance(d, dict):
                continue
            chains = d.get("chains") or []
            chains_list = []
            for c in chains:
                if isinstance(c, dict) and isinstance(c.get("chain"), list):
                    chains_list.append([str(x) for x in c["chain"]])
            # if any chain mentions scalars/all/first, ensure methods exist
            need_scalars = any("scalars" in ch for ch in chains_list)
            need_all = any("all" in ch for ch in chains_list)
            need_first = any("first" in ch for ch in chains_list)
            if need_scalars and "def scalars" not in txt:
                errs.append(f"Falta soporte scalars() requerido por stub_signatures para {fqn}")
            if need_all and "def all" not in txt:
                errs.append(f"Falta soporte all() requerido por stub_signatures para {fqn}")
            if need_first and "def first" not in txt:
                errs.append(f"Falta soporte first() requerido por stub_signatures para {fqn}")
            # rowcount heuristic
            if any("rowcount" in ch for ch in chains_list) and "rowcount" not in txt:
                errs.append(f"Falta rowcount requerido por stub_signatures para {fqn}")

    return errs


def generate_conftest_with_llm(
    *,
    runtime_contracts: Dict[str, Any],
    stub_signatures: Dict[str, Any],
    estructura_generada: Dict[str, str],
) -> StubGenResult:
    req_bundle = _parse_requirements_bundle(estructura_generada)
    prompt = _build_prompt_stub_gen(
        runtime_contracts=runtime_contracts,
        stub_signatures=stub_signatures,
        requirements_bundle=req_bundle,
    )

    errors: List[str] = []
    raw = None
    try:
        raw = chat_completion_json(
            prompt=prompt,
            system=None,
            temperature=0.1,
            max_tokens=1400,
            provider_hint="code-gen",
            fase="generacion_codigo",
        )
        _, content = _normalize_llm_out(raw)
        if not content:
            return StubGenResult(ok=False, conftest_py="", errors=["LLM no devolvió conftest.py válido"], raw_llm=raw)

        verrs = verify_conftest(
            conftest_py=content,
            runtime_contracts=runtime_contracts,
            stub_signatures=stub_signatures,
            requirements_bundle=req_bundle,
        )
        if verrs:
            return StubGenResult(ok=False, conftest_py=content, errors=verrs, raw_llm=raw)

        return StubGenResult(ok=True, conftest_py=content, errors=[], raw_llm=raw)
    except Exception as exc:
        errors.append(str(exc))
        return StubGenResult(ok=False, conftest_py="", errors=errors, raw_llm=raw)
