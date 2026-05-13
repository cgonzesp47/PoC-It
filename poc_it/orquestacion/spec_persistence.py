from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


def persist_spec_json(nombre_proyecto: str, resultado: Dict[str, Any], archivos_creados: list) -> None:
    """Persiste SPEC.json como artefacto del proyecto.

    Mantiene el mismo comportamiento que el helper del orquestador:
    - Si falla, no rompe el flujo (solo log informativo).
    """
    try:
        spec = resultado.get("spec")
        if isinstance(spec, dict) and spec:
            spec_path = os.path.join("output", nombre_proyecto, "SPEC.json")
            os.makedirs(os.path.dirname(spec_path), exist_ok=True)
            with open(spec_path, "w", encoding="utf-8") as f:
                import json as _json

                f.write(_json.dumps(spec, ensure_ascii=False, indent=2))
            archivos_creados.append(spec_path)
    except Exception as exc:
        logger.info("[DEBUG] No se pudo persistir SPEC.json: %s", exc)
