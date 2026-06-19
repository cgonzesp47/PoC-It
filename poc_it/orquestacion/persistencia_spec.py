from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


def persist_spec_json(nombre_proyecto: str, resultado: Dict[str, Any], archivos_creados: list) -> None:
    """Persiste spec como artefacto del proyecto.

    Política del repo:
    - NO persistir en `output/_debug` (carpeta de debug global).
    - Persistir solo dentro de la PoC, bajo `.poc_it/`, para trazabilidad y repairs posteriores.

    Si falla, no rompe el flujo (solo log informativo).
    """
    try:
        spec = resultado.get("spec")
        if isinstance(spec, dict) and spec:
            # Nota: `materializar_proyecto()` escribe bajo ./output/<nombre_proyecto>/...
            spec_path = os.path.join("output", nombre_proyecto, ".poc_it", "spec.json")
            os.makedirs(os.path.dirname(spec_path), exist_ok=True)
            with open(spec_path, "w", encoding="utf-8") as f:
                import json as _json

                f.write(_json.dumps(spec, ensure_ascii=False, indent=2))
            archivos_creados.append(spec_path)
    except Exception as exc:
        logger.info("[DEBUG] No se pudo persistir spec.json: %s", exc)
