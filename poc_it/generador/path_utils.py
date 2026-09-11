"""Utilidades deterministas sobre paths de API (compartidas por SPEC builder/validation/RequestIR)."""

from __future__ import annotations

import re
from typing import List

_PATH_PARAM_RE = re.compile(r"\{([^{}/]+)\}")


def extract_path_param_names(path: str) -> List[str]:
    """Nombres de los parámetros de path, en orden de aparición (p.ej. ["product_id"])."""
    return _PATH_PARAM_RE.findall(path or "")


def has_path_param(path: str) -> bool:
    """True si el path contiene algún segmento de parámetro `{...}`.

    No asume que el parámetro se llame literalmente "id": los paths idiomáticos casi siempre
    usan nombres descriptivos (`{product_id}`, `{user_id}`, `{order_id}`...), y un chequeo
    literal de `"{id}" in path` no los detecta, tratando por error un endpoint de recurso único
    (`GET /products/{product_id}`) como si fuera de colección.
    """
    return bool(_PATH_PARAM_RE.search(path or ""))
