from __future__ import annotations

import json
import re
from typing import Dict, List


def build_repair_prompt_por_restriccion(
    *,
    spec: dict,
    full_errors: List[str],
    target_error_path: str,
    target_error_msg: str,
    repair_paths: List[str],
    files_generados: List[Dict[str, str]],
) -> str:
    """
    Prompt de repair "atómico": atacar 1 error/restricción a la vez.

    Importante:
    - Función pura (sin side effects).
    - Mantener el texto igual (sin cambios semánticos) para no afectar comportamiento.
    """
    by_path = {
        (f.get("path") or "").replace("\\", "/"): (f.get("content") or "")
        for f in files_generados
        if f.get("path")
    }
    target_src = by_path.get(target_error_path, "")

    # limitar tamaño del source para no quemar tokens (pero mantener suficiente contexto)
    target_lines = target_src.splitlines()
    if len(target_lines) > 260:
        target_src = "\n".join(target_lines[:260]) + "\n# ... (truncado)"

    # Acotar a archivos implicados: el target + cualquier otro que guardrails haya marcado
    # (pero manteniendo el repair atómico en el target como objetivo principal)
    scoped_paths = [
        p
        for p in dict.fromkeys([target_error_path] + (repair_paths or [])).keys()
        if p
    ]

    # Extraer la restriction relevante del SPEC (best-effort, por id textual en el error)
    restrictions = spec.get("restrictions", [])
    rid = None
    m = re.search(r"viola restriction '([^']+)'", target_error_msg or "")
    if m:
        rid = m.group(1).strip()

    restriction_obj = None
    if rid and isinstance(restrictions, list):
        for r in restrictions:
            if isinstance(r, dict) and str(r.get("id") or "").strip() == rid:
                restriction_obj = r
                break

    restriction_block = (
        json.dumps(restriction_obj, ensure_ascii=False)
        if restriction_obj
        else "(no disponible)"
    )

    return f"""
TAREA
Corrige UN ÚNICO incumplimiento bloqueante de guardrails del proyecto, con cambios mínimos y verificables.

ERROR OBJETIVO (PRIORITARIO)
- Archivo: {target_error_path}
- Error: {target_error_msg}

RESTRICCIÓN (si está disponible en SPEC.restrictions)
{restriction_block}

CONTEXTO
- No re-arquitectures el proyecto.
- No añadas endpoints ni cambies rutas/métodos del SPEC.
- No añadas dependencias nuevas salvo que el propio SPEC lo exija.
- Enfócate en eliminar el patrón prohibido o cumplir el patrón requerido de ESTA restricción.
- Si la restricción es must_not_contain: el/los patrones NO deben aparecer en el archivo tras el cambio.

CÓDIGO ACTUAL (fragmento) de {target_error_path}:
```python
{target_src}
```

ARCHIVOS QUE PUEDES MODIFICAR (paths exactos; devuelve SOLO de esta lista):
{json.dumps(scoped_paths, ensure_ascii=False)}

TODOS LOS ERRORES DE GUARDRAILS (para contexto; NO intentes arreglarlos todos a la vez):
- {chr(10).join(full_errors)}

SALIDA (EXCLUSIVAMENTE JSON válido):
{{ "files": [{{"path":"...", "content":"..."}}] }}

REGLAS
- Devuelve SOLO archivos dentro de la lista permitida.
- El contenido debe ser completo (no parcial).
- No incluyas texto fuera del JSON.
""".strip()
