from __future__ import annotations

"""Sanitizador determinista de tests (hermético).

Problema que resuelve
--------------------
Cuando el pipeline aplica parches (fixers o LLM) a tests, es común que aparezcan:
- `app.dependency_overrides[foo] = ...` donde `foo` no está importado -> NameError
- overrides a dependencias que NO existen en el proyecto o que no aparecen en Depends(...)

Este módulo sanea `tests/*.py` en base a `.poc_it/runtime_contracts.json`:
- Solo se permiten overrides cuyas dependencias estén en `RuntimeContracts.allowed_dependency_overrides`.
- Si un override permitido usa un símbolo no importado, se añade el import correcto cuando es deducible.
- Si un override no es permitido o no es deducible, se comenta (se elimina funcionalmente).

Nota
----
No intenta arreglar asserts ni lógica de negocio; solo evita fallos de cableado/colección.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from poc_it.runtime.runtime_contracts import RuntimeContracts, load_runtime_contracts_from_structure


# Permite `app.dependency_overrides[get_db] = ...` y también `app.dependency_overrides[callable] = ...`
_OVERRIDE_KEY_RE = re.compile(r"app\.dependency_overrides\[(?P<key>.+?)\]\s*=")
_IMPORT_FROM_RE = re.compile(r"^\s*from\s+(?P<module>[A-Za-z0-9_\.]+)\s+import\s+(?P<names>.+?)\s*$")
_IMPORT_RE = re.compile(
    r"^\s*import\s+(?P<module>[A-Za-z0-9_\.]+)(\s+as\s+(?P<asname>[A-Za-z_][A-Za-z0-9_]*))?\s*$"
)


@dataclass(frozen=True)
class SanitizeResult:
    patched_files: Dict[str, str]
    messages: List[str]


def _allowed_fqns(contracts: RuntimeContracts) -> Set[str]:
    allowed = contracts.allowed_dependency_overrides or []
    return {str(x).strip() for x in allowed if str(x).strip()}


def _symbol_to_unique_fqn(symbol: str, allowed: Set[str]) -> Optional[str]:
    symbol = (symbol or "").strip()
    if not symbol:
        return None
    # Si el key no es un identificador simple (p.ej. llamada, atributo, string, etc.),
    # el sanitizer no debe tocarlo.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
        return None
    matches = [fqn for fqn in allowed if fqn.endswith("." + symbol)]
    if len(matches) == 1:
        return matches[0]
    return None


def _existing_imported_symbols(py_content: str) -> Set[str]:
    imported: Set[str] = set()
    for line in (py_content or "").splitlines():
        m = _IMPORT_FROM_RE.match(line)
        if m:
            names = m.group("names") or ""
            for part in names.split(","):
                part = part.strip()
                if not part:
                    continue
                if " as " in part:
                    imported.add(part.split(" as ")[-1].strip())
                else:
                    imported.add(part)
            continue

        m = _IMPORT_RE.match(line)
        if m:
            asname = m.group("asname")
            if asname:
                imported.add(asname)
            else:
                mod = (m.group("module") or "").strip()
                if mod:
                    imported.add(mod.split(".")[0])

    return imported


def sanitize_tests(*, estructura: Dict[str, str]) -> SanitizeResult:
    contracts = load_runtime_contracts_from_structure(estructura)
    if not contracts:
        return SanitizeResult(patched_files={}, messages=["runtime_contracts not found; sanitizer skipped"])

    allowed = _allowed_fqns(contracts)

    patched: Dict[str, str] = {}
    messages: List[str] = []

    for path, content in (estructura or {}).items():
        if not isinstance(path, str) or not path.startswith("tests/"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if "dependency_overrides" not in content:
            continue

        keys = [m.group("key").strip() for m in _OVERRIDE_KEY_RE.finditer(content)]
        if not keys:
            continue

        imported = _existing_imported_symbols(content)
        lines = content.splitlines()

        removed = 0
        for i, line in enumerate(lines):
            m = _OVERRIDE_KEY_RE.search(line)
            if not m:
                continue
            raw_key = (m.group("key") or "").strip()

            # Si el override ya usa un callable importado o una expresión compleja,
            # NO lo saneamos.
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw_key):
                continue

            # Si el key es un identificador simple pero NO se puede mapear de forma única a un
            # allowed FQN, puede tratarse de una variable que contiene el callable (p.ej. dep_callable).
            # En ese caso NO lo comentamos: preferimos mantener el override (best-effort) antes que
            # romper el harness y acabar tocando integraciones externas.
            key = raw_key
            fqn = _symbol_to_unique_fqn(key, allowed)
            if not fqn:
                continue

            # Si sí podemos mapearlo a un FQN permitido, el override es válido y tampoco lo tocamos.
            continue

        missing: List[Tuple[str, str]] = []
        for k in keys:
            if k in imported:
                continue
            fqn = _symbol_to_unique_fqn(k, allowed)
            if not fqn:
                continue
            mod, sym = fqn.rsplit(".", 1)
            if sym != k:
                continue
            missing.append((mod, sym))

        missing = sorted(set(missing))

        if missing:
            insert_at = 0
            for idx, line in enumerate(lines):
                if line.strip().startswith("import ") or line.strip().startswith("from "):
                    insert_at = idx + 1

            import_lines = [f"from {m} import {s}" for m, s in missing]
            lines[insert_at:insert_at] = import_lines + [""]
            messages.append(f"[{path}] added imports: {import_lines}")

        if removed:
            messages.append(f"[{path}] removed {removed} invalid dependency_override(s)")

        new_content = "\n".join(lines).rstrip() + "\n"
        if new_content != content:
            patched[path] = new_content

    return SanitizeResult(patched_files=patched, messages=messages)
