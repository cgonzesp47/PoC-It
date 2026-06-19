from __future__ import annotations
"""
Reparación/generación de harness de overrides (tests/conftest.py) vía LLM acotado.

Motivación
----------
En modo PARCIAL, el fallo más recurrente no es el "assert", sino el wiring/harness:
- `app.dependency_overrides[dep] = MagicMock()` (NO callable) → FastAPI intenta llamarlo y revienta
- override async/sync incorrecto
- override get_db que retorna dict / MockService sin contrato de sesión
- dependencia stateful creada con lambda (pierde estado entre calls) → POST→GET falla

En vez de fixers hiper-deterministas por regex, usamos una fase LLM dedicada (similar a stub_gen_llm)
que genera SOLO `tests/conftest.py` (o, si no existe suite tests/, lo crea) y lo valida con
un verificador determinista.

Entrada:
- runtime_contracts (fuente de verdad de allowed_dependency_overrides + depends_imports + tests_style)
- stub_signatures (si existe; awaited/chains)
- requirements bundle (allowlist imports)
- pytest_output (opcional; para focalizar)
- current_tests (para detectar harness embebido y moverlo a conftest)

Salida:
- Patch JSON con `tests/conftest.py` (contenido completo)

Este módulo NO ejecuta pytest; lo integra `pytest_llm_repair.py`.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from poc_it.generador.json_utils import extraer_json_tolerante
from poc_it.infraestructura.llm_client import chat_completion_json


@dataclass(frozen=True)
class OverrideRepairResult:
    ok: bool
    patched_files: Dict[str, str]
    errors: List[str]
    raw_llm: Optional[str] = None


def _parse_requirements_bundle(estructura: Dict[str, str]) -> Dict[str, str]:
    req_runtime = (estructura.get("requirements.txt") or "").strip()
    req_dev = (estructura.get("requirements-dev.txt") or "").strip()
    return {"requirements.txt": req_runtime, "requirements-dev.txt": req_dev}


def _extract_allowed_packages(requirements_txt: str) -> List[str]:
    out: List[str] = []
    for ln in (requirements_txt or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.-]+)", s)
        if m:
            out.append(m.group(1).lower())
    return sorted(set(out))


def _normalize_llm_out(raw: str) -> Dict[str, str]:
    """Acepta {files:[...]} o {path,content}."""
    data = extraer_json_tolerante(raw) or {}
    out: Dict[str, str] = {}

    if isinstance(data, dict) and data.get("path") == "tests/conftest.py" and isinstance(data.get("content"), str):
        out["tests/conftest.py"] = data["content"]
        return out

    files = data.get("files") if isinstance(data, dict) else None
    if isinstance(files, list):
        for f in files:
            if not isinstance(f, dict):
                continue
            path = str(f.get("path") or "").replace("\\", "/").strip()
            content = f.get("content")
            if path == "tests/conftest.py" and isinstance(content, str):
                out[path] = content
    return out


# --- Deterministic verification (text-based, conservative) --------------------


# Match simple one-line assignments like:
#   app.dependency_overrides[get_db] = override_get_db
#   app.dependency_overrides[dep] = MagicMock()
# We keep it intentionally conservative: only single-line RHS.
_DEP_OVERRIDES_ASSIGN_RE = re.compile(
    r"dependency_overrides\s*\[\s*(?P<dep>[^\]]+)\s*\]\s*=\s*(?P<rhs>[^\n#]+)",
    re.MULTILINE,
)

# RHS clearly non-callable instance assignment patterns.
_BANNED_OVERRIDE_RHS_RE = re.compile(
    r"\b(MagicMock|AsyncMock|Mock)\s*\(\s*\)\s*$|\{\s*\}$|\[\s*\]$",
    re.IGNORECASE,
)


def verify_overrides_conftest(conftest_py: str) -> List[str]:
    errs: List[str] = []
    txt = conftest_py or ""

    # 1) Must import pytest and build client fixture.
    if "import pytest" not in txt:
        errs.append("conftest.py must import pytest")
    if re.search(r"@pytest\\.fixture", txt) is None:
        errs.append("conftest.py must declare at least one pytest fixture")

    # 2) dependency_overrides RHS must be callable name or function, not MagicMock()
    for m in _DEP_OVERRIDES_ASSIGN_RE.finditer(txt):
        rhs = (m.group("rhs") or "").strip()
        # allow comments continuation; just evaluate the line
        rhs = rhs.split("#", 1)[0].strip()
        if _BANNED_OVERRIDE_RHS_RE.search(rhs):
            errs.append(f"Invalid dependency_overrides assignment RHS (must be callable, not instance): {rhs!r}")

    # 3) Hermetic: avoid obvious I/O initializers in conftest.
    banned_rx = [
        r"\\bcreate_engine\\s*\\(",
        r"\\bcreate_async_engine\\s*\\(",
        r"\\bsessionmaker\\s*\\(",
        r"\\basync_sessionmaker\\s*\\(",
        r"\\bconnect\\s*\\(",
        r"\\bos\\.getenv\\b",
        r"\\bopen\\(",
        r"\\brequests\\.",
        r"\\bhttpx\\.(Client|AsyncClient)\\(",
    ]
    for rx in banned_rx:
        if re.search(rx, txt):
            errs.append(f"conftest contains banned side-effect/I-O pattern: /{rx}/")

    return errs


def _build_prompt_override_repair(
    *,
    runtime_contracts: Dict[str, Any],
    stub_signatures: Optional[Dict[str, Any]],
    requirements_bundle: Dict[str, str],
    pytest_output: str,
    current_tests: Dict[str, str],
) -> str:
    allow_pkgs = _extract_allowed_packages(requirements_bundle.get("requirements.txt", "")) + _extract_allowed_packages(
        requirements_bundle.get("requirements-dev.txt", "")
    )
    allow_pkgs = sorted(set(allow_pkgs))

    # Limit size: only include conftest + the specific hermetic test if present
    files = []
    for p in ("tests/conftest.py", "tests/test_endpoints_hermetic.py"):
        if p in current_tests and isinstance(current_tests[p], str):
            files.append({"path": p, "content": current_tests[p]})

    return f"""
TAREA
Genera o repara SOLO el archivo `tests/conftest.py` para que los tests sean herméticos y los overrides sean correctos.

OBJETIVO
- Evitar cualquier acceso a integraciones externas (DB real, red, credenciales, SDKs cloud).
- Arreglar un patrón frecuente: `app.dependency_overrides[dep] = MagicMock()` (esto es INCORRECTO).
  FastAPI requiere que el override sea un callable/generator/async generator.

INPUTS
- runtime_contracts: fuente de verdad para:
  - tests_style ("sync" con TestClient o "async" con httpx.AsyncClient)
  - allowed_dependency_overrides (FQNs permitidos)
  - endpoints[*].depends_imports (deps reales usadas)
- stub_signatures (si existe): awaited/sync y chains, para stubs coherentes
- tests actuales (solo algunos archivos para contexto)

ALLOWLIST IMPORTS
- Solo stdlib, `app.*` y paquetes en requirements
- Paquetes permitidos (lowercase): {json.dumps(allow_pkgs, ensure_ascii=False)}

RUNTIME_CONTRACTS
{json.dumps(runtime_contracts, ensure_ascii=False)}

STUB_SIGNATURES
{json.dumps(stub_signatures or {{}}, ensure_ascii=False)}

PYTEST OUTPUT (truncado)
{(pytest_output or "")[:8000]}

TESTS (context)
{json.dumps(files, ensure_ascii=False)}

REGLAS OBLIGATORIAS DE OVERRIDES
1) `app.dependency_overrides[dep_callable] = override_fn` donde override_fn es callable.
   - PROHIBIDO asignar instancias: MagicMock()/dict/list/None.
2) get_db típicamente es generator/async generator:
   - Preferir `def override_get_db(): yield MagicMock()`
3) Stubs stateful:
   - Si hay CRUD (POST->GET/PUT/DELETE), el override debe devolver la MISMA instancia durante el scope del client fixture.
4) Limpieza:
   - usar `with TestClient(app) as c: yield c` y al final `app.dependency_overrides.clear()`

SALIDA (SOLO JSON)
{{"path":"tests/conftest.py","content":"..."}}
""".strip()


def generate_or_repair_conftest_overrides_with_llm(
    *,
    runtime_contracts: Dict[str, Any],
    stub_signatures: Optional[Dict[str, Any]],
    estructura: Dict[str, str],
    pytest_output: str,
    current_tests: Dict[str, str],
) -> OverrideRepairResult:
    req_bundle = _parse_requirements_bundle(estructura)
    prompt = _build_prompt_override_repair(
        runtime_contracts=runtime_contracts or {},
        stub_signatures=stub_signatures,
        requirements_bundle=req_bundle,
        pytest_output=pytest_output or "",
        current_tests=current_tests or {},
    )

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
        patch = _normalize_llm_out(raw)
        if not patch.get("tests/conftest.py"):
            return OverrideRepairResult(ok=False, patched_files={}, errors=["LLM did not return tests/conftest.py"], raw_llm=raw)

        verrs = verify_overrides_conftest(patch["tests/conftest.py"])
        if verrs:
            return OverrideRepairResult(ok=False, patched_files=patch, errors=verrs, raw_llm=raw)

        return OverrideRepairResult(ok=True, patched_files=patch, errors=[], raw_llm=raw)
    except Exception as exc:
        return OverrideRepairResult(ok=False, patched_files={}, errors=[str(exc)], raw_llm=raw)
