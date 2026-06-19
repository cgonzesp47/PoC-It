from __future__ import annotations

import re
from typing import Set, List, Tuple


_IMPORT_RE_FROM = re.compile(r"^\s*from\s+([a-zA-Z0-9_\.]+)\s+import\s+", re.MULTILINE)
_IMPORT_RE_IMPORT = re.compile(r"^\s*import\s+([a-zA-Z0-9_\.]+)", re.MULTILINE)
_IMPORT_RE_FROM_SYMBOLS = re.compile(
    r"^\s*from\s+([a-zA-Z0-9_\.]+)\s+import\s+(.+)$",
    re.MULTILINE,
)


def extraer_imports(modulo_src: str) -> Set[str]:
    """
    Extrae módulos importados vía:
      - from x.y import ...
      - import x.y

    Devuelve conjunto de módulos (strings).
    """
    mods: Set[str] = set()
    for m in _IMPORT_RE_FROM.findall(modulo_src):
        mods.add(m)
    for m in _IMPORT_RE_IMPORT.findall(modulo_src):
        # puede haber import a, b; nos quedamos con el primer token
        mods.add(m.split(",")[0].strip())
    return mods


def extraer_from_imports(modulo_src: str) -> List[Tuple[str, str]]:
    """
    Extrae pares (module, symbol) para:
      from module import symbol

    Soporta:
      - múltiples símbolos
      - alias (a as b)
      - ignora wildcard '*'
    """
    out: List[Tuple[str, str]] = []
    for mod, symbols_raw in _IMPORT_RE_FROM_SYMBOLS.findall(modulo_src):
        # eliminar comentarios inline
        symbols_raw = symbols_raw.split("#", 1)[0].strip()

        # rompe en coma (puede ser "a as b, c")
        parts = [p.strip() for p in symbols_raw.split(",") if p.strip()]
        for part in parts:
            if part == "*":
                continue
            sym = part.split(" as ", 1)[0].strip()
            if sym:
                out.append((mod.strip(), sym))
    return out
