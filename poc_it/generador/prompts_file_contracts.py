from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _safe_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _spec_summary(spec: dict) -> dict:
    if not isinstance(spec, dict):
        return {}
    return {
        "schema_version": spec.get("schema_version"),
        "entrypoint": spec.get("entrypoint"),
        "run_command": spec.get("run_command"),
        "imports_policy": spec.get("imports_policy"),
        "dependencies": spec.get("dependencies", []),
        "dev_dependencies": spec.get("dev_dependencies", []),
        "technology_signals": spec.get("technology_signals", []),
        "integrations": spec.get("integrations", []),
        "configuration": spec.get("configuration", []),
        "implementation_files": spec.get("implementation_files", []),
        "endpoints": [
            {
                "method": e.get("method"),
                "path": e.get("path"),
                "file": e.get("file"),
                "func": e.get("func"),
                "actions": e.get("actions", []),
                "errors": e.get("errors", []),
                "integration_refs": e.get("integration_refs", []),
            }
            for e in (spec.get("endpoints") or [])
            if isinstance(e, dict)
        ],
        "restrictions": spec.get("restrictions", []),
        "files": spec.get("files", []),
    }


def _kind_specific_rules(file_contract: Dict[str, Any]) -> List[str]:
    kind = str(file_contract.get("kind") or "").strip()
    if kind == "endpoint":
        return [
            "No construyas directamente clientes externos si existe un File Contract de integración relacionado.",
            "Invoca el módulo interno asignado.",
            "Traduce errores declarados a respuestas HTTP.",
            "No dupliques autenticación o configuración del proveedor.",
        ]
    if kind == "integration":
        return [
            "Construye el cliente externamente de forma lazy.",
            "Lee configuración mediante app.core.config.",
            "Implementa una operación llamable por cada acción externa asignada.",
            "No importes FastAPI salvo que el contrato lo permita expresamente.",
            "No traduzcas excepciones a HTTPException.",
        ]
    if kind == "config":
        return [
            "Declara toda la configuración asignada.",
            "No exige valores en import-time.",
            "No almacenes valores reales.",
        ]
    if kind == "requirements":
        return [
            "Cada línea debe ser un package instalable declarado en dependencies.",
            "No escribas nombres de módulos de importación salvo que coincidan con el package declarado.",
        ]
    return []


def _render_bullets(values: List[Any]) -> str:
    items = []
    for value in values or []:
        items.append(f"- {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(items) if items else "- []"


def _render_section(name: str, payload: Any) -> str:
    if isinstance(payload, list):
        body = _render_bullets(payload)
    elif isinstance(payload, dict):
        body = _safe_json(payload)
    else:
        body = f"- {payload}"
    return f"{name}\n{body}"


def _priority_rules() -> List[str]:
    return [
        "ORDEN DE PRIORIDAD",
        "1. must_not",
        "2. must_implement",
        "3. implementation_plan",
        "4. actions, errors e integrations",
        "5. responsibilities",
        "6. SPEC resumido",
        "7. contexto normalizado",
        "",
        "- Debes implementar TODAS las entradas de must_implement.",
        "- Está prohibido vulnerar CUALQUIER entrada de must_not.",
        "- Una acción required=true no puede omitirse ni sustituirse por TODO, pass, placeholder o respuesta simulada.",
        "- implementation_level=\"integration_skeleton\" exige cliente, autenticación, configuración y operación llamable; no exige credenciales reales.",
        "- No devuelvas una respuesta de éxito si no se ejecutaron las acciones requeridas.",
        "- Las llamadas externas deben ser lazy y mockeables.",
        "- Los tests herméticos no deben llamar a servicios reales.",
    ]


def build_prompt_file_contract(
    *,
    spec: dict,
    file_contract: dict,
    related_contracts: List[dict],
    descripcion_global: str,
    contexto_normalizado: Optional[dict] = None,
) -> str:
    contexto_normalizado = (
        contexto_normalizado if isinstance(contexto_normalizado, dict) else {}
    )
    fc_path = (file_contract.get("path") or "").replace("\\", "/")

    prompt_lines = [
        "TAREA",
        "Genera EXACTAMENTE UN (1) archivo para un proyecto FastAPI, cumpliendo estrictamente el FileContract.",
        "",
        "REGLAS DE SALIDA (OBLIGATORIAS)",
        "- Devuelve SOLO JSON válido, sin texto extra.",
        "- PROHIBIDO usar ``` o markdown.",
        "- Formato preferido (devuelve EXACTAMENTE estas claves):",
        "{",
        f'  "path": "{fc_path}",',
        '  "content": "..."',
        "}",
        '- NO devuelvas "files" con varios archivos.',
        "- NO generes otros archivos.",
        '- El campo "content" debe ser el archivo COMPLETO (no fragmentos).',
        "",
        "REGLAS CONTRACT-FIRST (NO NEGOCIABLE)",
        "- FileContract es la fuente de verdad para ESTE archivo.",
        "- No crear símbolos públicos fuera de required_symbols.",
        "- No mover símbolos a otros archivos.",
        "",
        "INVARIANTES DE CÓDIGO (si es .py)",
        "- Debe ser Python parseable (sin SyntaxError).",
        "- Imports internos deben ser absolutos desde `app.*`.",
        "- Prohibido ejecutar conexiones externas en import-time (DB, APIs, etc.).",
        "- No validar variables de entorno obligatorias en import-time.",
        "",
        *_priority_rules(),
        "",
        "REGLAS ESPECÍFICAS DEL KIND",
        *_kind_specific_rules(file_contract),
        "",
        "DESCRIPCIÓN GLOBAL",
        descripcion_global,
        "",
        _render_section("OBLIGACIONES", file_contract.get("must_implement") or []),
        "",
        _render_section("PROHIBICIONES", file_contract.get("must_not") or []),
        "",
        _render_section(
            "PLAN DE IMPLEMENTACIÓN", file_contract.get("implementation_plan") or []
        ),
        "",
        _render_section("ACCIONES", file_contract.get("actions") or []),
        "",
        _render_section("ERRORES", file_contract.get("errors") or []),
        "",
        _render_section(
            "INTEGRACIONES",
            {
                "integration_refs": file_contract.get("integration_refs", []),
                "external_dependencies": file_contract.get(
                    "external_dependencies", []
                ),
                "implementation_levels": file_contract.get(
                    "implementation_levels", []
                ),
            },
        ),
        "",
        _render_section("CONFIGURACIÓN", file_contract.get("configuration") or []),
        "",
        "FILE CONTRACT (ACTUAL)",
        _safe_json(file_contract),
        "",
        "CONTRATOS RELACIONADOS (SOLO CONTEXTO, NO GENERAR ARCHIVOS)",
        _safe_json(related_contracts),
        "",
        "SPEC (RESUMEN; REFERENCIA SECUNDARIA)",
        _safe_json(_spec_summary(spec)),
        "",
        "CONTEXTO NORMALIZADO (RESUMEN; REFERENCIA)",
        _safe_json(
            {
                "objetivo_tecnico": contexto_normalizado.get("objetivo_tecnico"),
                "restricciones_tecnicas": contexto_normalizado.get(
                    "restricciones_tecnicas", []
                ),
                "integraciones_externas": contexto_normalizado.get(
                    "integraciones_externas", []
                ),
                "persistence": contexto_normalizado.get("persistence"),
            }
        ),
    ]
    return "\n".join(prompt_lines).strip()


def build_prompt_file_contract_fix(
    *,
    spec: dict,
    file_contract: dict,
    previous_content: str,
    errors: List[str],
    related_contracts: List[dict],
) -> str:
    fc_path = (file_contract.get("path") or "").replace("\\", "/")
    prompt_lines = [
        "TAREA",
        "Repara EXACTAMENTE UN (1) archivo según el FileContract. Corrige errores y devuelve el archivo completo.",
        "",
        "REGLAS DE SALIDA (OBLIGATORIAS)",
        "- Devuelve SOLO JSON válido, sin texto extra.",
        "- PROHIBIDO usar ``` o markdown.",
        "- Devuelve EXACTAMENTE:",
        "{",
        f'  "path": "{fc_path}",',
        '  "content": "..."',
        "}",
        "- PROHIBIDO cambiar el path.",
        "- PROHIBIDO generar otros archivos.",
        "",
        "REGLAS DE REPARACIÓN",
        "- No elimines obligaciones ya implementadas para resolver un error aislado.",
        "- No conviertas una integración real en stub o fake.",
        "- Mantén todas las acciones required=true.",
        "- Corrige únicamente el archivo actual.",
        "",
        *_priority_rules(),
        "",
        "REGLAS ESPECÍFICAS DEL KIND",
        *_kind_specific_rules(file_contract),
        "",
        _render_section("OBLIGACIONES", file_contract.get("must_implement") or []),
        "",
        _render_section("PROHIBICIONES", file_contract.get("must_not") or []),
        "",
        _render_section(
            "PLAN DE IMPLEMENTACIÓN", file_contract.get("implementation_plan") or []
        ),
        "",
        _render_section("ACCIONES", file_contract.get("actions") or []),
        "",
        _render_section("ERRORES", file_contract.get("errors") or []),
        "",
        _render_section(
            "INTEGRACIONES",
            {
                "integration_refs": file_contract.get("integration_refs", []),
                "external_dependencies": file_contract.get(
                    "external_dependencies", []
                ),
                "implementation_levels": file_contract.get(
                    "implementation_levels", []
                ),
            },
        ),
        "",
        _render_section("CONFIGURACIÓN", file_contract.get("configuration") or []),
        "",
        "FILE CONTRACT (ACTUAL, FUENTE DE VERDAD)",
        _safe_json(file_contract),
        "",
        "CONTRATOS RELACIONADOS (SOLO CONTEXTO)",
        _safe_json(related_contracts),
        "",
        "ERRORES A CORREGIR",
        "- " + "\n- ".join(errors or ["repair_file"]),
        "",
        "CONTENIDO ANTERIOR (REFERENCIA)",
        previous_content,
        "",
        "SPEC (RESUMEN; REFERENCIA SECUNDARIA)",
        _safe_json(_spec_summary(spec)),
    ]
    return "\n".join(prompt_lines).strip()
