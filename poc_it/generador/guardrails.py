from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Set, Tuple

from poc_it.generador.restriction_models import (
    RestrictionEnforcement,
    migrate_legacy_enforcement,
    parse_restriction_enforcement,
)
from poc_it.materializacion.codegen.incomplete_code import detect_incomplete_code


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


def seleccionar_error_bloqueante(
    errores: List[str],
    *,
    file_contracts: List[Dict[str, Any]] | None = None,
) -> Tuple[str, str] | None:
    """
    Selecciona un único error "prioritario" para reparación atómica.
    Devuelve (path, mensaje) si puede; si no, None.

    La prioridad usa FileContract.kind cuando está disponible.
    No deduce tipos de archivo a partir de prefijos/path.
    """
    by_file = extraer_errores_por_archivo(errores)
    if not by_file:
        return None

    contract_by_path = {
        str(contract.get("path") or "").replace("\\", "/").strip(): contract
        for contract in (file_contracts or [])
        if isinstance(contract, dict) and str(contract.get("path") or "").strip()
    }
    repair_priority_by_kind = {
        "config": 10,
        "endpoint": 20,
        "router": 30,
        "service": 40,
        "integration": 50,
    }

    ordered_paths = sorted(
        by_file.keys(),
        key=lambda path: (
            repair_priority_by_kind.get(
                str((contract_by_path.get(path) or {}).get("kind") or "").strip(),
                999,
            ),
            path,
        ),
    )
    for path in ordered_paths:
        messages = by_file.get(path) or []
        if messages:
            return path, messages[0]

    return None


_HIGH_CONFIDENCE_SECRET_PATTERNS = (
    "-----begin private key-----",
    '"type": "service_account"',
)
_SECRET_NAME_HINTS = {
    "private_key",
    "client_secret",
    "api_secret",
    "secret_key",
}


def _literal_string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _looks_like_secret_name(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    return normalized in _SECRET_NAME_HINTS or normalized.endswith("_secret")


def _contains_sensitive_literal_assignment(
    src: str,
    *,
    secret_config_names: Set[str] | None = None,
) -> bool:
    normalized_secret_names = {str(name).strip() for name in (secret_config_names or set()) if str(name).strip()}

    try:
        tree = ast.parse(src)
    except SyntaxError:
        lowered_src = src.lower()
        return any(pattern in lowered_src for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            literal = _literal_string_value(node.value)
            if literal is None:
                continue

            lowered_literal = literal.lower()
            if any(pattern in lowered_literal for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS):
                return True

            target_names = {
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            if normalized_secret_names and target_names & normalized_secret_names:
                return True
            if any(_looks_like_secret_name(name) for name in target_names):
                return True

        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            literal = _literal_string_value(node.value) if node.value is not None else None
            if literal is None:
                continue

            lowered_literal = literal.lower()
            if any(pattern in lowered_literal for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS):
                return True

            target_name = node.target.id
            if target_name in normalized_secret_names or _looks_like_secret_name(target_name):
                return True

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered_value = node.value.lower()
            if any(pattern in lowered_value for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS):
                return True

    return False


def _secret_config_names_from_spec(spec: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()

    for restriction in spec.get("restrictions") or []:
        if not isinstance(restriction, dict):
            continue
        for key in ("secret_config_names", "secret_names", "config_secret_names"):
            values = restriction.get(key) or []
            if not isinstance(values, list):
                values = [values]
            for value in values:
                normalized = str(value or "").strip()
                if normalized:
                    names.add(normalized)

    for file_contract in spec.get("file_contracts") or []:
        if not isinstance(file_contract, dict):
            continue
        for item in file_contract.get("config_keys") or []:
            if not isinstance(item, dict):
                continue
            if not item.get("secret"):
                continue
            name = str(item.get("name") or item.get("key") or "").strip()
            if name:
                names.add(name)

    return names


def guardrails_por_spec(spec: dict, files_generados: List[Dict[str, str]]) -> GuardrailsResult:
    """
    Guardrails genéricos basados en SPEC (sin conocimiento de dominio específico).

    Reglas:
    - No endpoints extra importados explícitamente en main.py.
    - Detecta placeholder/incomplete code de alta confianza.
    - Detecta secretos literales embebidos de alta confianza.
    - Enforce restricciones declaradas TEXTUALES con enforcement=text.

    No valida:
    - request transport
    - logging contractual
    - response contracts
    - auth/config semantics
    - external preconditions
    """
    errores: List[str] = []
    warnings: List[str] = []
    reparar: Set[str] = set()

    by_path: Dict[str, str] = {
        (f.get("path") or "").replace("\\", "/"): (f.get("content") or "")
        for f in files_generados
        if isinstance(f, dict) and f.get("path")
    }

    spec_eps = spec.get("endpoints", [])
    expected_ep_files: Set[str] = set()

    if isinstance(spec_eps, list):
        for ep in spec_eps:
            if not isinstance(ep, dict):
                continue
            ep_file = str(ep.get("file") or "").replace("\\", "/")
            if ep_file:
                expected_ep_files.add(ep_file)

    main_src = by_path.get("app/main.py", "")
    if main_src:
        for m in re.findall(r"from\s+app\.api\.endpoints\.([a-zA-Z0-9_]+)\s+import\s+router", main_src):
            f = f"app/api/endpoints/{m}.py"
            if expected_ep_files and f not in expected_ep_files:
                errores.append(f"Endpoint extra no listado en SPEC (importado en main.py): {f}")
                reparar.add("app/main.py")

        for m in re.findall(
            r"from\s+app\.endpoints\.([a-zA-Z0-9_]+)\s+import\s+router",
            main_src,
        ):
            f = f"app/endpoints/{m}.py"
            if expected_ep_files and f not in expected_ep_files:
                errores.append(f"Endpoint extra no listado en SPEC (importado en main.py): {f}")
                reparar.add("app/main.py")

    secret_config_names = _secret_config_names_from_spec(spec)
    for p, src in by_path.items():
        if not p.endswith(".py"):
            continue
        if detect_incomplete_code(p, src):
            errores.append(f"{p}: CODEGEN_PLACEHOLDER_CONTENT")
            reparar.add(p)
        if _contains_sensitive_literal_assignment(
            src,
            secret_config_names=secret_config_names,
        ):
            errores.append(f"{p}: HARDCODED_SECRET_LITERAL")
            reparar.add(p)

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

            enforcement = parse_restriction_enforcement(r.get("enforcement"))
            if enforcement is None:
                migrated_enforcement = migrate_legacy_enforcement(r)
                warnings.append(
                    "restriction "
                    f"'{rid}': RESTRICTION_ENFORCEMENT_UNKNOWN "
                    f"(raw={r.get('enforcement')!r}, normalized={migrated_enforcement.value})"
                )
                enforcement = migrated_enforcement

            if enforcement is not RestrictionEnforcement.TEXT:
                continue

            must_not = r.get("must_not_contain") or []
            must_any = r.get("must_contain_any") or []

            if not isinstance(must_not, list):
                must_not = [str(must_not)]
            if not isinstance(must_any, list):
                must_any = [str(must_any)]

            for p, src in by_path.items():
                if not _applies(p, applies_to):
                    continue

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
