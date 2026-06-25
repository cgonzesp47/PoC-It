from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _safe_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _spec_summary(spec: dict) -> dict:
    """
    Extrae un resumen pequeño del SPEC para minimizar tokens.

    Importante: el SPEC completo NO es la fuente de verdad en esta fase; lo es FileContracts.
    """
    if not isinstance(spec, dict):
        return {}
    return {
        "schema_version": spec.get("schema_version"),
        "entrypoint": spec.get("entrypoint"),
        "run_command": spec.get("run_command"),
        "imports_policy": spec.get("imports_policy"),
        "dependencies": spec.get("dependencies", []),
        "dev_dependencies": spec.get("dev_dependencies", []),
        "endpoints": [
            {
                "method": e.get("method"),
                "path": e.get("path"),
                "file": e.get("file"),
                "func": e.get("func"),
            }
            for e in (spec.get("endpoints") or [])
            if isinstance(e, dict)
        ],
        "restrictions": spec.get("restrictions", []),
        "files": spec.get("files", []),
    }


def build_prompt_file_contract(
    *,
    spec: dict,
    file_contract: dict,
    related_contracts: List[dict],
    descripcion_global: str,
    contexto_normalizado: Optional[dict] = None,
) -> str:
    """
    Prompt contract-first por archivo.

    Reglas:
    - Debe generar UN SOLO archivo, el indicado por file_contract["path"].
    - Debe devolver JSON estricto SIN markdown, SIN texto extra.
    - Formato preferido:
      { "path": "...", "content": "..." }
    """
    contexto_normalizado = contexto_normalizado if isinstance(contexto_normalizado, dict) else {}
    fc_path = (file_contract.get("path") or "").replace("\\", "/")

    return f"""
TAREA
Genera EXACTAMENTE UN (1) archivo para un proyecto FastAPI, cumpliendo estrictamente el FileContract.

REGLAS DE SALIDA (OBLIGATORIAS)
- Devuelve SOLO JSON válido, sin texto extra.
- PROHIBIDO usar ``` o markdown.
- Formato preferido (devuelve EXACTAMENTE estas claves):
  {{
    "path": "{fc_path}",
    "content": "..."
  }}
- NO devuelvas "files" con varios archivos.
- NO generes otros archivos.
- El campo "content" debe ser el archivo COMPLETO (no fragmentos).

REGLAS CONTRACT-FIRST (NO NEGOCIABLE)
- FileContract es la fuente de verdad para ESTE archivo.
- No crear símbolos públicos fuera de required_symbols.
- No mover símbolos a otros archivos.

INVARIANTES DE CÓDIGO (si es .py)
- Debe ser Python parseable (sin SyntaxError).
- Imports internos deben ser absolutos desde `app.*`.
- Prohibido ejecutar conexiones externas en import-time (DB, APIs, etc.).
- No validar variables de entorno obligatorias en import-time.

DESCRIPCIÓN GLOBAL
{descripcion_global}

FILE CONTRACT (ACTUAL)
{_safe_json(file_contract)}

CONTRATOS RELACIONADOS (SOLO CONTEXTO, NO GENERAR ARCHIVOS)
{_safe_json(related_contracts)}

SPEC (RESUMEN; REFERENCIA SECUNDARIA)
{_safe_json(_spec_summary(spec))}

CONTEXTO NORMALIZADO (RESUMEN; REFERENCIA)
{_safe_json({
  "objetivo_tecnico": contexto_normalizado.get("objetivo_tecnico"),
  "restricciones_tecnicas": contexto_normalizado.get("restricciones_tecnicas", []),
  "integraciones_externas": contexto_normalizado.get("integraciones_externas", []),
  "persistence": contexto_normalizado.get("persistence"),
})}
""".strip()


def build_prompt_file_contract_fix(
    *,
    spec: dict,
    file_contract: dict,
    previous_content: str,
    errors: List[str],
    related_contracts: List[dict],
) -> str:
    """
    Prompt de reparación por archivo.

    Reglas:
    - Reparar SOLO el archivo del contract.
    - Mantener el mismo path.
    - JSON estricto sin markdown.
    """
    fc_path = (file_contract.get("path") or "").replace("\\", "/")
    return f"""
TAREA
Repara EXACTAMENTE UN (1) archivo según el FileContract. Corrige errores y devuelve el archivo completo.

REGLAS DE SALIDA (OBLIGATORIAS)
- Devuelve SOLO JSON válido, sin texto extra.
- PROHIBIDO usar ``` o markdown.
- Devuelve EXACTAMENTE:
  {{
    "path": "{fc_path}",
    "content": "..."
  }}
- PROHIBIDO cambiar el path.
- PROHIBIDO generar otros archivos.

FILE CONTRACT (ACTUAL, FUENTE DE VERDAD)
{_safe_json(file_contract)}

CONTRATOS RELACIONADOS (SOLO CONTEXTO)
{_safe_json(related_contracts)}

ERRORES A CORREGIR
- {chr(10).join(errors)}

CONTENIDO ANTERIOR (REFERENCIA)
{previous_content}

SPEC (RESUMEN; REFERENCIA SECUNDARIA)
{_safe_json(_spec_summary(spec))}
""".strip()
