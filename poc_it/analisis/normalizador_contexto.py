"""
PoC-it - Normalizador de Contexto

Responsabilidad:
- Recibir una PlantillaUsuario rellenada.
- Convertirla en un contexto técnico estructurado y normalizado.
- Reducir ambigüedad narrativa.
- Servir como única fuente formal de contexto para el sistema.

Este módulo NO genera código.
Solo estructura y formaliza el contexto.

Cambio clave (refactor):
- Separar estrictamente contratos API explícitos vs propuestos:
  - contratos_api_explicitos: requieren evidence literal.
  - contratos_api_propuestos: requieren assumption (no evidence literal).
- Evitar que endpoints propuestos se consuman como fuente de verdad.
- Mantener compatibilidad temporal con contratos_api legacy (deprecated):
  - Se deriva SOLO de contratos_api_explicitos.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from poc_it.infraestructura.llm_client import chat_completion_json
from poc_it.modulos.models import PlantillaUsuario


PROMPT_NORMALIZACION = """
Actúa como arquitecto software senior. Convierte una plantilla de PoC en un contexto técnico estructurado.

Devuelve ÚNICAMENTE JSON válido con la siguiente estructura:

{
  "objetivo_tecnico": "string",
  "actores_principales": ["string"],
  "funcionalidades_clave": ["string"],
  "integraciones_externas": ["string"],
  "restricciones_tecnicas": ["string"],
  "requisitos_no_funcionales": ["string"],
  "riesgos_inherentes": ["string"],
  "complejidad_inferida": "BAJA | MEDIA | ALTA | CRITICA",

  "contratos_api_explicitos": [
    {
      "method": "GET | POST | PUT | PATCH | DELETE",
      "path": "/ruta",
      "request": {"type": "json | multipart | query | none", "schema_hint": {}, "evidence": "cita literal"},
      "response": {"json_example": {}, "evidence": "cita literal"},
      "notes": "string opcional"
    }
  ],
  "contratos_api_propuestos": [
    {
      "method": "GET | POST | PUT | PATCH | DELETE",
      "path": "/ruta",
      "request": {"type": "json | multipart | query | none", "schema_hint": {}, "assumption": "por qué se propone"},
      "response": {"json_example": {}},
      "notes": "string opcional"
    }
  ],

  "persistence": {
    "required": false,
    "kind": null,
    "durable_state": false,
    "business_entities": [],
    "evidence": [],
    "uncertainty": ""
  },

  "technology_signals": [
    {
      "name": "string",
      "category": "framework | persistence | cache | queue | object_storage | search | external_api | auth | observability | runtime | library | unknown",
      "role": "string",
      "evidence": "cita literal",
      "confidence": "explicit | inferred | unknown"
    }
  ],

  "domain_entities": [
    {
      "name": "string",
      "singular": "string",
      "plural": "string",
      "evidence": "cita literal",
      "confidence": "explicit | inferred | unknown"
    }
  ],

  "operation_groups": [
    {
      "type": "crud",
      "entity": "string",
      "evidence": "cita literal",
      "confidence": "explicit | inferred | unknown"
    }
  ],

  "state_requirements": {
    "durable": false,
    "entities": [],
    "evidence": []
  },

  "assumptions": ["string"],
  "evidence": ["string"]
}

Reglas:
- No inventes endpoints explícitos si no hay evidencia literal.
- No inventar no significa dejar campos vacíos: extrae todas las funcionalidades, restricciones, tecnologías, persistencia y necesidades de estado que estén evidenciadas en la plantilla.
- Extrae domain_entities y operation_groups cuando exista evidencia literal; si no, deja vacío.
- Todo endpoint explícito debe incluir evidence (request.evidence y/o response.evidence) con cita literal.
- Todo endpoint propuesto debe incluir request.assumption.

Reglas persistence/state:
- persistence.required=true SOLO si el usuario pide conservar estado de negocio durable entre peticiones/sesiones.
- No activar persistence.required solo por mencionar una tecnología.
- kind NO es vendor-specific. Valores: relational | document | key_value | object_storage | event_log | unknown | null.
- Si hay duda: required=false y usar uncertainty/assumptions.

Reglas technology_signals:
- Extrae tecnologías mencionadas explícitamente por el usuario (no inventar).
- Cada señal debe incluir evidence literal y confidence.
- Si no puedes clasificar con seguridad: category="unknown", confidence="unknown".

No añadas texto fuera del JSON.
"""


def normalizar_plantilla(plantilla: PlantillaUsuario) -> Dict[str, Any]:
    """
    Normaliza la plantilla del usuario en un contexto técnico estructurado.

    Importante:
    - Este normalizador NO debe mezclar propuestas con contratos explícitos.
    - Si el LLM falla o responde parcialmente, el resultado debe seguir siendo seguro:
      no inventar endpoints explícitos.
    """
    prompt = f"""
{PROMPT_NORMALIZACION}

Plantilla proporcionada por el usuario:

Nombre: {plantilla.nombre}
Problema: {plantilla.problema}
Usuarios: {plantilla.usuarios}
Funcionalidades: {plantilla.funcionalidades}
Límites: {plantilla.limites}
Tecnologías declaradas: {plantilla.tecnologias}
"""

    respuesta = chat_completion_json(
        prompt=prompt,
        system="Responde exclusivamente con JSON válido.",
        temperature=0.0,
        max_tokens=2000,
        fase="normalizacion_contexto",
    )

    try:
        data = json.loads(respuesta)
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    data = _ensure_normalized_shape(data, plantilla)

    _apply_deterministic_fallbacks(data, plantilla)

    # Sanitización fuerte para invariantes explícito/propuesto
    _sanitize_contracts_inplace(data)

    # Compatibilidad temporal (deprecated): contratos_api legacy SOLO explícitos
    data["contratos_api"] = list(data.get("contratos_api_explicitos") or [])
    data["_deprecated"] = {"contratos_api": "Use contratos_api_explicitos/contratos_api_propuestos"}

    # enriquecer trazabilidad global sin mezclar con contratos
    data["evidence"] = _dedupe_str_list(
        list(data.get("evidence") or []) + _template_evidence_lines(plantilla)
    )
    data["assumptions"] = _dedupe_str_list(list(data.get("assumptions") or []))

    _persist_debug_context(plantilla, data)
    return data


# ---------------------------------------------------------------------
# Helpers de fallback determinista (genéricos, sin heurísticas de dominio)
# ---------------------------------------------------------------------


def _apply_deterministic_fallbacks(data: Dict[str, Any], plantilla: PlantillaUsuario) -> None:
    """
    Fallback determinista mínimo para cuando el LLM devuelve JSON pobre/empty.

    Principios:
    - NO inferir desde texto libre (sin keywords, sin heurísticas de dominio).
    - Solo estructurar campos explícitos de la plantilla (funcionalidades/limites/tecnologias).
    - No activar persistencia ni proponer endpoints si no hay evidencia estructurada en el JSON del LLM.
    """
    if not isinstance(data, dict):
        return

    data["funcionalidades_clave"] = _extract_functionalities_from_template(
        plantilla, existing=data.get("funcionalidades_clave")
    )
    data["restricciones_tecnicas"] = _extract_constraints_from_template(
        plantilla, existing=data.get("restricciones_tecnicas")
    )

    if not (isinstance(data.get("technology_signals"), list) and data.get("technology_signals")):
        data["technology_signals"] = _extract_technology_signals_from_template(plantilla)

    # Persistencia: NO inferir por texto libre; si no vino estructurada, mantener required=false y marcar uncertainty.
    data["persistence"] = _extract_persistence_from_template(
        plantilla, existing=data.get("persistence"), technology_signals=data.get("technology_signals")
    )

    # Endpoints: solo si hay evidencia estructurada (domain_entities + operation_groups).
    _propose_api_contracts_from_crud_if_evidenced(data)


def _extract_functionalities_from_template(plantilla: PlantillaUsuario, existing: Any) -> List[str]:
    if isinstance(existing, list) and any(isinstance(x, str) and x.strip() for x in existing):
        return _ensure_list_of_str(existing)

    txt = (plantilla.funcionalidades or "").strip()
    return [txt] if txt else []


def _extract_constraints_from_template(plantilla: PlantillaUsuario, existing: Any) -> List[str]:
    if isinstance(existing, list) and any(isinstance(x, str) and x.strip() for x in existing):
        return _ensure_list_of_str(existing)

    txt = (plantilla.limites or "").strip()
    return [txt] if txt else []


def _extract_technology_signals_from_template(plantilla: PlantillaUsuario) -> List[Dict[str, Any]]:
    raw = (plantilla.tecnologias or "").strip()
    if not raw:
        return []

    evidence = f"Tecnologías declaradas: {raw}"
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[Dict[str, Any]] = []
    for name in parts:
        out.append(
            {
                "name": name,
                "category": "unknown",
                "role": "",
                "evidence": evidence,
                "confidence": "explicit",
            }
        )
    return _normalize_technology_signals(out)


def _extract_persistence_from_template(
    plantilla: PlantillaUsuario, existing: Any, technology_signals: Any
) -> Dict[str, Any]:
    # Fallback genérico: NO inferir persistencia desde texto libre.
    # Solo respetamos persistencia si ya venía marcada como required con evidence.
    if isinstance(existing, dict) and existing.get("required") is True:
        ev = _ensure_list_of_str(existing.get("evidence"))
        if ev:
            return existing

    base_uncertainty = ""
    if isinstance(existing, dict) and isinstance(existing.get("uncertainty"), str):
        base_uncertainty = existing.get("uncertainty", "").strip()

    return {
        "required": False,
        "kind": None,
        "durable_state": False,
        "business_entities": [],
        "evidence": [],
        "uncertainty": base_uncertainty or "persistence_not_structured_from_normalizer",
    }


def _propose_api_contracts_from_crud_if_evidenced(data: Dict[str, Any]) -> None:
    """
    Propuesta determinista y genérica de endpoints CRUD:

    - NO usar texto libre.
    - Requiere:
      - operation_groups con type="crud" + evidence
      - domain_entities con plural/name + evidence
    """
    if not isinstance(data.get("contratos_api_explicitos"), list):
        data["contratos_api_explicitos"] = []
    if not isinstance(data.get("contratos_api_propuestos"), list):
        data["contratos_api_propuestos"] = []

    if data["contratos_api_explicitos"] or data["contratos_api_propuestos"]:
        return

    ops = data.get("operation_groups")
    entities = data.get("domain_entities")
    if not isinstance(ops, list) or not isinstance(entities, list):
        return

    crud_ops = [
        op
        for op in ops
        if isinstance(op, dict)
        and str(op.get("type", "")).strip().lower() == "crud"
        and isinstance(op.get("entity"), str)
        and op.get("entity", "").strip()
        and isinstance(op.get("evidence"), str)
        and op.get("evidence", "").strip()
    ]
    if not crud_ops:
        return

    ent_by_key: Dict[str, Dict[str, Any]] = {}
    for e in entities:
        if not isinstance(e, dict):
            continue
        ev = e.get("evidence") if isinstance(e.get("evidence"), str) else ""
        if not ev.strip():
            continue
        name = e.get("name") if isinstance(e.get("name"), str) else ""
        singular = e.get("singular") if isinstance(e.get("singular"), str) else ""
        plural = e.get("plural") if isinstance(e.get("plural"), str) else ""
        key = (plural or name or singular).strip()
        if not key:
            continue
        ent_by_key[key.lower()] = e

    if not ent_by_key:
        return

    assumptions = data.get("assumptions")
    if not isinstance(assumptions, list):
        assumptions = []
        data["assumptions"] = assumptions

    for op in crud_ops:
        ent_key = op.get("entity", "").strip().lower()
        ent = ent_by_key.get(ent_key)
        if not ent:
            continue

        slug_source = (ent.get("plural") or ent.get("name") or ent.get("singular") or "").strip()
        slug = _slugify_path_segment(slug_source)
        if not slug:
            continue

        assumption = f"Endpoints propuestos derivados de operation_groups(type=crud) para entidad '{slug_source}'."
        assumptions.append(assumption)

        # CRUD completo:
        # - Mantenerlos como propuestos (nunca explícitos)
        # - Si el path contiene {id}, NO convertir a query params:
        #   - GET/DELETE {id} => request.type="none" (+ opcional path_params via schema_hint)
        #   - PUT/PATCH {id} => request.type="json"
        data["contratos_api_propuestos"] = [
            {
                "method": "POST",
                "path": f"/{slug}",
                "request": {"type": "json", "schema_hint": {}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
            },
            {
                "method": "GET",
                "path": f"/{slug}",
                "request": {"type": "none", "schema_hint": {}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
            },
            {
                "method": "GET",
                "path": f"/{slug}" + "/{id}",
                "request": {"type": "none", "schema_hint": {"path_params": {"id": "integer"}}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
            },
            {
                "method": "PUT",
                "path": f"/{slug}" + "/{id}",
                "request": {"type": "json", "schema_hint": {}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
            },
            {
                "method": "PATCH",
                "path": f"/{slug}" + "/{id}",
                "request": {"type": "json", "schema_hint": {}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
            },
            {
                "method": "DELETE",
                "path": f"/{slug}" + "/{id}",
                "request": {"type": "none", "schema_hint": {"path_params": {"id": "integer"}}, "assumption": assumption},
                "response": {"json_example": {}},
                "notes": "",
            },
        ]
        return


# ---------------------------------------------------------------------
# Helpers de forma (robustos a LLM parcial)
# ---------------------------------------------------------------------


def _ensure_normalized_shape(data: Dict[str, Any], plantilla: PlantillaUsuario) -> Dict[str, Any]:
    """
    Garantiza shape mínimo aunque el LLM devuelva JSON parcial o con claves legacy.
    """
    base: Dict[str, Any] = {
        "objetivo_tecnico": plantilla.problema,
        "actores_principales": [],
        "funcionalidades_clave": [],
        "integraciones_externas": [],
        "restricciones_tecnicas": [],
        "requisitos_no_funcionales": [],
        "riesgos_inherentes": [],
        "complejidad_inferida": "MEDIA",
        "contratos_api_explicitos": [],
        "contratos_api_propuestos": [],
        "persistence": {
            "required": False,
            "kind": None,
            "durable_state": False,
            "business_entities": [],
            "evidence": [],
            "uncertainty": "",
        },
        "technology_signals": [],
        "domain_entities": [],
        "operation_groups": [],
        "state_requirements": {"durable": False, "entities": [], "evidence": []},
        "assumptions": [],
        "evidence": [],
    }

    out: Dict[str, Any] = dict(base)
    out.update({k: v for k, v in data.items() if k in out})

    # tolerar legacy "contratos_api": se clasifica, pero NUNCA se trata como explícito por defecto
    legacy_contracts = data.get("contratos_api")
    if isinstance(legacy_contracts, list) and legacy_contracts:
        exp, prop, extra_assumptions, extra_evidence = _split_contracts_legacy(legacy_contracts)
        out["contratos_api_explicitos"] = list(out.get("contratos_api_explicitos") or []) + exp
        out["contratos_api_propuestos"] = list(out.get("contratos_api_propuestos") or []) + prop
        out["assumptions"] = list(out.get("assumptions") or []) + extra_assumptions
        out["evidence"] = list(out.get("evidence") or []) + extra_evidence

    # normalizar listas de strings simples
    for k in (
        "actores_principales",
        "funcionalidades_clave",
        "integraciones_externas",
        "restricciones_tecnicas",
        "requisitos_no_funcionales",
        "riesgos_inherentes",
        "assumptions",
        "evidence",
    ):
        out[k] = _ensure_list_of_str(out.get(k))

    # asegurar listas de contratos
    out["contratos_api_explicitos"] = (
        out.get("contratos_api_explicitos") if isinstance(out.get("contratos_api_explicitos"), list) else []
    )
    out["contratos_api_propuestos"] = (
        out.get("contratos_api_propuestos") if isinstance(out.get("contratos_api_propuestos"), list) else []
    )

    out["persistence"] = _normalize_persistence(out.get("persistence"))
    out["technology_signals"] = _normalize_technology_signals(out.get("technology_signals"))
    out["domain_entities"] = _normalize_domain_entities(out.get("domain_entities"))
    out["operation_groups"] = _normalize_operation_groups(out.get("operation_groups"))
    out["state_requirements"] = _normalize_state_requirements(out.get("state_requirements"))

    return out


def _split_contracts_legacy(
    items: List[Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], List[str]]:
    """
    Toma legacy contratos_api y los separa por invariantes:
    - explícito: evidence presente (en request.evidence o response.evidence)
    - propuesto: sin evidence => debe llevar assumption; si no lo trae, se añade.
    """
    explicit: List[Dict[str, Any]] = []
    proposed: List[Dict[str, Any]] = []
    assumptions: List[str] = []
    evidence: List[str] = []

    for it in items:
        if not isinstance(it, dict):
            continue
        req = it.get("request") if isinstance(it.get("request"), dict) else {}
        resp = it.get("response") if isinstance(it.get("response"), dict) else {}

        ev_req = req.get("evidence") if isinstance(req.get("evidence"), str) else ""
        ev_resp = resp.get("evidence") if isinstance(resp.get("evidence"), str) else ""
        has_evidence = bool((ev_req or "").strip() or (ev_resp or "").strip())

        if has_evidence:
            explicit.append(it)
            if (ev_req or "").strip():
                evidence.append(ev_req.strip())
            if (ev_resp or "").strip():
                evidence.append(ev_resp.strip())
        else:
            # propuesto: exigir assumption
            if not isinstance(req.get("assumption"), str) or not req.get("assumption", "").strip():
                req = dict(req)
                req["assumption"] = (
                    "Endpoint propuesto (legacy) sin evidencia literal; requiere confirmación del usuario."
                )
                it = dict(it)
                it["request"] = req
                assumptions.append(req["assumption"])
            proposed.append(it)

    return explicit, proposed, assumptions, evidence


def _sanitize_contracts_inplace(data: Dict[str, Any]) -> None:
    """
    Enforce invariantes:
    - explícitos: deben tener evidence literal (request.evidence y/o response.evidence)
    - propuestos: deben tener assumption (request.assumption)
    - nunca mezclar (si algo está mal clasificado, se mueve a propuestos)
    """
    explicitos = data.get("contratos_api_explicitos")
    propuestos = data.get("contratos_api_propuestos")

    if not isinstance(explicitos, list):
        explicitos = []
    if not isinstance(propuestos, list):
        propuestos = []

    fixed_explicitos: List[Dict[str, Any]] = []
    fixed_propuestos: List[Dict[str, Any]] = list(propuestos)
    assumptions: List[str] = list(data.get("assumptions") or [])
    evidence: List[str] = list(data.get("evidence") or [])

    for it in explicitos:
        if not isinstance(it, dict):
            continue
        req = it.get("request") if isinstance(it.get("request"), dict) else {}
        resp = it.get("response") if isinstance(it.get("response"), dict) else {}

        ev_req = req.get("evidence") if isinstance(req.get("evidence"), str) else ""
        ev_resp = resp.get("evidence") if isinstance(resp.get("evidence"), str) else ""
        has_evidence = bool((ev_req or "").strip() or (ev_resp or "").strip())

        if has_evidence:
            fixed_explicitos.append(it)
            if (ev_req or "").strip():
                evidence.append(ev_req.strip())
            if (ev_resp or "").strip():
                evidence.append(ev_resp.strip())
        else:
            # estaba mal clasificado: mover a propuestos y exigir assumption
            req2 = dict(req)
            if not isinstance(req2.get("assumption"), str) or not req2.get("assumption", "").strip():
                req2["assumption"] = (
                    "Endpoint sin evidencia literal; se trató como propuesto y requiere confirmación del usuario."
                )
            it2 = dict(it)
            it2["request"] = req2
            fixed_propuestos.append(it2)
            assumptions.append(req2["assumption"])

    # propuestos: asegurar assumption
    fixed_propuestos2: List[Dict[str, Any]] = []
    for it in fixed_propuestos:
        if not isinstance(it, dict):
            continue
        req = it.get("request") if isinstance(it.get("request"), dict) else {}
        req2 = dict(req)
        if not isinstance(req2.get("assumption"), str) or not req2.get("assumption", "").strip():
            req2["assumption"] = "Endpoint propuesto sin evidence literal; requiere confirmación del usuario."
            assumptions.append(req2["assumption"])
        it2 = dict(it)
        it2["request"] = req2
        fixed_propuestos2.append(it2)

    data["contratos_api_explicitos"] = fixed_explicitos
    data["contratos_api_propuestos"] = fixed_propuestos2
    data["assumptions"] = _dedupe_str_list(assumptions)
    data["evidence"] = _dedupe_str_list(evidence)

    # persistence / technology / state sanitization
    data["persistence"] = _normalize_persistence(
        data.get("persistence"), assumptions_sink=data["assumptions"]
    )
    data["assumptions"] = _dedupe_str_list(list(data.get("assumptions") or []))
    data["technology_signals"] = _normalize_technology_signals(data.get("technology_signals"))
    data["domain_entities"] = _normalize_domain_entities(data.get("domain_entities"))
    data["operation_groups"] = _normalize_operation_groups(data.get("operation_groups"))
    data["state_requirements"] = _normalize_state_requirements(data.get("state_requirements"))

    _reconcile_persistence_and_state_requirements(data)
    data["assumptions"] = _dedupe_str_list(list(data.get("assumptions") or []))


def _ensure_list_of_str(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for x in v:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _dedupe_str_list(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    out: List[str] = []
    seen = set()
    for x in items:
        if not isinstance(x, str):
            continue
        s = x.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _template_evidence_lines(plantilla: PlantillaUsuario) -> List[str]:
    """
    Evidence global trazable a campos del usuario. No infiere, solo serializa.
    """
    out: List[str] = []
    if plantilla.problema:
        out.append(f"Problema: {plantilla.problema}")
    if plantilla.usuarios:
        out.append(f"Usuarios: {plantilla.usuarios}")
    if plantilla.funcionalidades:
        out.append(f"Funcionalidades: {plantilla.funcionalidades}")
    if plantilla.limites:
        out.append(f"Límites: {plantilla.limites}")
    if plantilla.tecnologias:
        out.append(f"Tecnologías declaradas: {plantilla.tecnologias}")
    return out


def _normalize_persistence(p: Any, assumptions_sink: List[str] | None = None) -> Dict[str, Any]:
    sink = assumptions_sink if isinstance(assumptions_sink, list) else []

    if not isinstance(p, dict):
        p = {}

    required = bool(p.get("required")) if "required" in p else False
    durable_state = bool(p.get("durable_state")) if "durable_state" in p else False

    kind = p.get("kind")
    if kind is None:
        kind_norm = None
    elif isinstance(kind, str) and kind.strip() in (
        "relational",
        "document",
        "key_value",
        "object_storage",
        "event_log",
        "unknown",
    ):
        kind_norm = kind.strip()
    else:
        # si llega vendor/valor no permitido, degradar a unknown si required, sino None
        kind_norm = "unknown" if required else None
        if required and sink is not None:
            sink.append("persistence.kind no era válido/no permitido; se degradó a 'unknown'.")

    business_entities = _ensure_list_of_str(p.get("business_entities"))
    evidence = _ensure_list_of_str(p.get("evidence"))
    uncertainty = p.get("uncertainty") if isinstance(p.get("uncertainty"), str) else ""
    uncertainty = uncertainty.strip()

    # durable_state=True sin evidence NO debe activar persistencia
    if durable_state and not evidence:
        if sink is not None:
            sink.append("durable_state_without_evidence")
            sink.append(
                "durable_state_without_evidence: Se ignoró persistence.durable_state=True por falta de evidence."
            )
        if not uncertainty:
            uncertainty = "durable_state_without_evidence"
        return {
            "required": False,
            "kind": None,
            "durable_state": False,
            "business_entities": [],
            "evidence": [],
            "uncertainty": uncertainty,
        }

    if not required:
        return {
            "required": False,
            "kind": None,
            "durable_state": False,
            "business_entities": [],
            "evidence": [],
            "uncertainty": uncertainty,
        }

    # required=True
    if not evidence and sink is not None:
        sink.append(
            "Persistencia marcada como requerida pero falta evidence; revisar/confirmar necesidad de estado durable."
        )
        if not uncertainty:
            uncertainty = "required_true_without_evidence"

    return {
        "required": True,
        "kind": kind_norm if kind_norm is not None else "unknown",
        "durable_state": bool(durable_state),
        "business_entities": business_entities,
        "evidence": evidence,
        "uncertainty": uncertainty,
    }


def _normalize_technology_signals(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    allowed_categories = {
        "framework",
        "persistence",
        "cache",
        "queue",
        "object_storage",
        "search",
        "external_api",
        "auth",
        "observability",
        "runtime",
        "library",
        "unknown",
    }
    allowed_confidence = {"explicit", "inferred", "unknown"}

    out: List[Dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        name = it.get("name") if isinstance(it.get("name"), str) else ""
        evidence = it.get("evidence") if isinstance(it.get("evidence"), str) else ""
        if not name.strip():
            continue
        if not evidence.strip():
            # sin evidencia, degradar: no es señal confiable
            continue

        category = it.get("category") if isinstance(it.get("category"), str) else "unknown"
        category = category.strip() if category.strip() in allowed_categories else "unknown"

        role = it.get("role") if isinstance(it.get("role"), str) else ""
        role = role.strip()

        confidence = it.get("confidence") if isinstance(it.get("confidence"), str) else "unknown"
        confidence = confidence.strip() if confidence.strip() in allowed_confidence else "unknown"

        out.append(
            {
                "name": name.strip(),
                "category": category,
                "role": role,
                "evidence": evidence.strip(),
                "confidence": confidence,
            }
        )

    # dedupe estable por (name, category, role)
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in out:
        k = (it["name"].lower(), it["category"].lower(), it["role"].lower())
        if k in seen:
            continue
        seen.add(k)
        deduped.append(it)

    return deduped


def _normalize_domain_entities(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    allowed_confidence = {"explicit", "inferred", "unknown"}

    out: List[Dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue

        evidence = it.get("evidence") if isinstance(it.get("evidence"), str) else ""
        if not evidence.strip():
            continue

        name = it.get("name") if isinstance(it.get("name"), str) else ""
        singular = it.get("singular") if isinstance(it.get("singular"), str) else ""
        plural = it.get("plural") if isinstance(it.get("plural"), str) else ""
        confidence = it.get("confidence") if isinstance(it.get("confidence"), str) else "unknown"
        confidence = confidence.strip() if confidence.strip() in allowed_confidence else "unknown"

        slug_source = (plural or name or singular).strip()
        slug = _slugify_path_segment(slug_source)

        out.append(
            {
                "name": name.strip() or slug_source,
                "singular": singular.strip(),
                "plural": plural.strip(),
                "slug": slug,
                "evidence": evidence.strip(),
                "confidence": confidence,
            }
        )

    # dedupe estable por slug/name
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in out:
        k = (it.get("slug") or it.get("name") or "").lower()
        if not k or k in seen:
            continue
        seen.add(k)
        deduped.append(it)

    return deduped


def _normalize_operation_groups(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    allowed_confidence = {"explicit", "inferred", "unknown"}

    out: List[Dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        t = it.get("type") if isinstance(it.get("type"), str) else ""
        entity = it.get("entity") if isinstance(it.get("entity"), str) else ""
        evidence = it.get("evidence") if isinstance(it.get("evidence"), str) else ""
        if not t.strip() or not entity.strip() or not evidence.strip():
            continue

        confidence = it.get("confidence") if isinstance(it.get("confidence"), str) else "unknown"
        confidence = confidence.strip() if confidence.strip() in allowed_confidence else "unknown"

        out.append(
            {
                "type": t.strip().lower(),
                "entity": entity.strip(),
                "evidence": evidence.strip(),
                "confidence": confidence,
            }
        )

    return out


def _slugify_path_segment(value: str) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    out: List[str] = []
    prev_dash = False
    for ch in v:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        else:
            if not prev_dash:
                out.append("-")
                prev_dash = True
    return "".join(out).strip("-")


def _normalize_state_requirements(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    durable = bool(raw.get("durable")) if "durable" in raw else False
    entities = _ensure_list_of_str(raw.get("entities"))
    evidence = _ensure_list_of_str(raw.get("evidence"))
    return {"durable": bool(durable), "entities": entities, "evidence": evidence}


def _reconcile_persistence_and_state_requirements(data: Dict[str, Any]) -> None:
    """
    Reconciliación conservadora basada en evidence.

    Preferencias:
    - Si hay contradicción sin evidence suficiente, degradar a required=False (stateless por defecto).
    - Nunca activar persistencia sólo por flags (ej. durable_state) sin evidence.
    """
    if not isinstance(data, dict):
        return

    p = data.get("persistence") if isinstance(data.get("persistence"), dict) else {}
    s = data.get("state_requirements") if isinstance(data.get("state_requirements"), dict) else {}

    assumptions = data.get("assumptions")
    if not isinstance(assumptions, list):
        assumptions = []
        data["assumptions"] = assumptions

    p_required = bool(p.get("required")) if "required" in p else False
    p_evidence = _ensure_list_of_str(p.get("evidence"))

    s_durable = bool(s.get("durable")) if "durable" in s else False
    s_evidence = _ensure_list_of_str(s.get("evidence"))

    # 1) persistence.required=True pero state.durable=False
    if p_required and not s_durable:
        if p_evidence:
            s["durable"] = True
            if not s.get("entities"):
                s["entities"] = _ensure_list_of_str(p.get("business_entities"))
            if not s_evidence:
                s["evidence"] = list(p_evidence)
        else:
            assumptions.append(
                "Contradicción: persistence.required=True pero sin evidence; se degradó a required=False."
            )
            data["persistence"] = {
                "required": False,
                "kind": None,
                "durable_state": False,
                "business_entities": [],
                "evidence": [],
                "uncertainty": p.get("uncertainty") or "contradiction_without_evidence",
            }

    # refrescar p/s tras posible degradación
    p = data.get("persistence") if isinstance(data.get("persistence"), dict) else p
    p_required = bool(p.get("required")) if "required" in p else False

    # 2) state.durable=True pero persistence.required=False
    if s_durable and not p_required:
        if s_evidence:
            data["persistence"] = {
                "required": True,
                "kind": "unknown",
                "durable_state": True,
                "business_entities": _ensure_list_of_str(s.get("entities")),
                "evidence": list(s_evidence),
                "uncertainty": p.get("uncertainty") if isinstance(p.get("uncertainty"), str) else "",
            }
        else:
            assumptions.append(
                "state_requirements.durable=True sin evidence; se mantuvo persistence.required=False (conservador)."
            )
            s["durable"] = False
            s["entities"] = []
            s["evidence"] = []

    # 3) ambos true => dedupe
    p = data.get("persistence") if isinstance(data.get("persistence"), dict) else p
    s = data.get("state_requirements") if isinstance(data.get("state_requirements"), dict) else s
    if bool(p.get("required")) and bool(s.get("durable")):
        p["evidence"] = _dedupe_str_list(_ensure_list_of_str(p.get("evidence")))
        p["business_entities"] = _dedupe_str_list(_ensure_list_of_str(p.get("business_entities")))
        s["evidence"] = _dedupe_str_list(_ensure_list_of_str(s.get("evidence")))
        s["entities"] = _dedupe_str_list(_ensure_list_of_str(s.get("entities")))

    data["persistence"] = p
    data["state_requirements"] = s


def _persist_debug_context(plantilla: PlantillaUsuario, data: Dict[str, Any]) -> None:
    try:
        debug_dir = Path("output/_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (debug_dir / f"contexto_normalizado_{ts}.json").write_text(
            json.dumps(
                {
                    "timestamp": ts,
                    "plantilla": {
                        "nombre": plantilla.nombre,
                        "problema": plantilla.problema,
                        "usuarios": plantilla.usuarios,
                        "funcionalidades": plantilla.funcionalidades,
                        "limites": plantilla.limites,
                        "tecnologias": plantilla.tecnologias,
                    },
                    "contexto_normalizado": data,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
