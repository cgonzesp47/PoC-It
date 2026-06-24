from __future__ import annotations

"""
Request IR canónico (previo al SPEC).

Responsabilidad de este módulo (determinista, sin LLM):
- Convertir un contexto normalizado YA estructurado a RequestIR.
- Preservar evidencia (strings) sin interpretar lenguaje natural libre.
- Separar estrictamente explícito/propuesto en contratos API según evidencia literal.
- Validar invariantes del IR.

No pertenece a este módulo:
- Inferir semántica de negocio desde texto libre.
- Detectar persistencia desde descripcion_global.
- Mantener listas de vendors, entidades, verbos o patrones.

Persistencia:
- Solo se construye desde campos estructurados del contexto normalizado:
  - persistence, persistencia, persistence_requirement, requisitos_persistencia,
    data_lifecycle, state_requirements
- Si no existen, persistence.required=False, kind=None, y no se añaden open_questions por defecto.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple


RequestType = Literal["json", "multipart", "query", "none"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
PersistenceKind = Literal[
    "relational",
    "document",
    "key_value",
    "object_storage",
    "event_log",
    "unknown",
]


@dataclass(frozen=True)
class ApiContractIR:
    method: str
    path: str
    description: str = ""
    request_type: RequestType = "none"
    request_schema_hint: Dict[str, Any] = field(default_factory=dict)
    response_example: Dict[str, Any] = field(default_factory=dict)
    evidence: str = ""
    assumption: str = ""


@dataclass(frozen=True)
class TechnologySignalIR:
    name: str
    category: str = "unknown"
    role: str = ""
    evidence: str = ""
    confidence: str = "unknown"


@dataclass(frozen=True)
class PersistenceIR:
    required: bool = False
    kind: Optional[PersistenceKind] = None
    durable_state: bool = False
    business_entities: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    uncertainty: str = ""


@dataclass(frozen=True)
class DomainEntityIR:
    name: str
    singular: str = ""
    plural: str = ""
    slug: str = ""
    evidence: str = ""
    confidence: str = "unknown"


@dataclass(frozen=True)
class OperationGroupIR:
    type: str
    entity: str
    evidence: str = ""
    confidence: str = "unknown"


@dataclass(frozen=True)
class RequestIR:
    product_name: str
    objective: str
    user_facts: List[str] = field(default_factory=list)
    user_constraints: List[str] = field(default_factory=list)
    product_capabilities: List[str] = field(default_factory=list)
    explicit_api_contracts: List[ApiContractIR] = field(default_factory=list)
    proposed_api_contracts: List[ApiContractIR] = field(default_factory=list)
    external_integrations: List[str] = field(default_factory=list)
    technology_constraints: List[str] = field(default_factory=list)
    technology_signals: List[TechnologySignalIR] = field(default_factory=list)
    domain_entities: List[DomainEntityIR] = field(default_factory=list)
    operation_groups: List[OperationGroupIR] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    persistence: PersistenceIR = field(default_factory=PersistenceIR)


# ---------------------------------------------------------------------
# Builders / serializers / validators
# ---------------------------------------------------------------------


def build_request_ir_from_context(
    contexto_normalizado: Dict[str, Any] | None,
    descripcion_global: str,
) -> RequestIR:
    ctx = contexto_normalizado if isinstance(contexto_normalizado, dict) else {}

    product_name = _coalesce_str(ctx.get("nombre_proyecto"), ctx.get("nombre"), "") or "PoC"
    objective = _coalesce_str(ctx.get("objetivo_tecnico"), descripcion_global, "") or ""

    product_capabilities = _list_of_str(ctx.get("funcionalidades_clave"))
    external_integrations = _list_of_str(ctx.get("integraciones_externas"))
    technology_constraints = _list_of_str(ctx.get("restricciones_tecnicas"))
    user_constraints = list(technology_constraints)

    # hechos explícitos (solo si vienen como campos estructurados)
    user_facts: List[str] = []
    user_facts.extend(_prefix_list("Actor", _list_of_str(ctx.get("actores_principales"))))
    user_facts.extend(_prefix_list("Riesgo", _list_of_str(ctx.get("riesgos_inherentes"))))
    user_facts.extend(_prefix_list("NFR", _list_of_str(ctx.get("requisitos_no_funcionales"))))

    explicit_api_contracts: List[ApiContractIR] = []
    proposed_api_contracts: List[ApiContractIR] = []

    # Nuevo formato preferente: contratos_api_explicitos / contratos_api_propuestos
    explicit_raw = ctx.get("contratos_api_explicitos")
    proposed_raw = ctx.get("contratos_api_propuestos")

    if isinstance(explicit_raw, list) or isinstance(proposed_raw, list):
        # 1) Parse explícitos/propuestos
        explicit_parsed = _parse_contracts(explicit_raw)
        proposed_parsed = _parse_contracts(proposed_raw)

        # 2) Enforce invariantes:
        # - explícitos requieren evidence; si no, se mueven a propuestos con assumption
        for c in explicit_parsed:
            if _has_literal_evidence(c):
                explicit_api_contracts.append(c)
            else:
                proposed_api_contracts.append(
                    _ensure_proposed_has_assumption(
                        ApiContractIR(
                            method=c.method,
                            path=c.path,
                            description=c.description,
                            request_type=c.request_type,
                            request_schema_hint=dict(c.request_schema_hint or {}),
                            response_example=dict(c.response_example or {}),
                            evidence="",
                            assumption=c.assumption,
                        )
                    )
                )

        # - propuestos requieren assumption
        proposed_api_contracts.extend([_ensure_proposed_has_assumption(c) for c in proposed_parsed])
    else:
        # Legacy: contratos_api. Se separa por evidence literal.
        contracts_raw = ctx.get("contratos_api")
        contracts = _parse_contracts(contracts_raw)

        for c in contracts:
            if _has_literal_evidence(c):
                explicit_api_contracts.append(c)
            else:
                proposed_api_contracts.append(_ensure_proposed_has_assumption(c))

    proposed_api_contracts = [_ensure_proposed_has_assumption(c) for c in proposed_api_contracts]

    technology_signals = _parse_technology_signals(ctx.get("technology_signals"))
    domain_entities = _parse_domain_entities(ctx.get("domain_entities"))
    operation_groups = _parse_operation_groups(ctx.get("operation_groups"))

    persistence = build_persistence_ir_from_context(ctx)

    assumptions: List[str] = []
    open_questions: List[str] = []

    # gaps estructurales
    if not explicit_api_contracts and proposed_api_contracts:
        assumptions.append(
            "Se han propuesto endpoints sin evidencia literal; deben confirmarse con el usuario antes de tratarlos como contrato explícito."
        )

    # si required=True pero sin evidence estructurada => el validador lo marcará, pero añadimos pregunta
    if persistence.required and not persistence.evidence:
        open_questions.append("Se requiere persistencia según el contexto estructurado, pero falta evidencia textual/citas en el campo persistence.evidence.")

    # si required=True y kind no viene, normalizamos a unknown
    if persistence.required and persistence.kind is None:
        persistence = PersistenceIR(
            required=True,
            kind="unknown",
            durable_state=persistence.durable_state,
            business_entities=list(persistence.business_entities),
            evidence=list(persistence.evidence),
            uncertainty=persistence.uncertainty,
        )
        open_questions.append("Persistencia requerida pero el tipo (kind) no está especificado; se ha normalizado a 'unknown'.")

    return RequestIR(
        product_name=product_name,
        objective=objective,
        user_facts=_dedupe_stable(user_facts),
        user_constraints=_dedupe_stable(user_constraints),
        product_capabilities=_dedupe_stable(product_capabilities),
        explicit_api_contracts=_dedupe_contracts(explicit_api_contracts),
        proposed_api_contracts=_dedupe_contracts(proposed_api_contracts),
        external_integrations=_dedupe_stable(external_integrations),
        technology_constraints=_dedupe_stable(technology_constraints),
        technology_signals=_dedupe_tech_signals(technology_signals),
        domain_entities=_dedupe_domain_entities(domain_entities),
        operation_groups=_dedupe_operation_groups(operation_groups),
        assumptions=_dedupe_stable(assumptions),
        open_questions=_dedupe_stable(open_questions),
        persistence=persistence,
    )


def request_ir_to_dict(ir: RequestIR) -> Dict[str, Any]:
    # dataclasses.asdict convierte anidados
    return asdict(ir)


def validate_request_ir(ir: RequestIR) -> List[str]:
    errors: List[str] = []

    if not isinstance(ir.product_name, str) or not ir.product_name.strip():
        errors.append("product_name requerido")
    if not isinstance(ir.objective, str) or not ir.objective.strip():
        errors.append("objective requerido")

    # Contratos: method/path mínimos
    for c in (ir.explicit_api_contracts or []) + (ir.proposed_api_contracts or []):
        if not str(c.method).strip():
            errors.append("ApiContractIR.method vacío")
        if not str(c.path).strip() or not str(c.path).startswith("/"):
            errors.append(f"ApiContractIR.path inválido: {c.path!r}")
        if c.request_type not in ("json", "multipart", "query", "none"):
            errors.append(f"ApiContractIR.request_type inválido: {c.request_type!r}")

    # Regla fuerte: explícitos requieren evidencia literal
    for c in ir.explicit_api_contracts or []:
        if not _coalesce_str(c.evidence, "").strip():
            errors.append(f"explicit_api_contracts sin evidence: {c.method} {c.path}")

    # persistencia: required False => kind None y durable_state False
    if not ir.persistence.required:
        if ir.persistence.kind is not None:
            errors.append("persistence.kind no debe informarse si persistence.required=False")
        if ir.persistence.durable_state:
            errors.append("persistence.durable_state no debe ser True si persistence.required=False")
        if ir.persistence.business_entities:
            errors.append("persistence.business_entities no debe informarse si persistence.required=False")
        if ir.persistence.evidence:
            errors.append("persistence.evidence no debe informarse si persistence.required=False")

    # persistencia: required True => kind no None (se permite unknown) y evidence presente
    if ir.persistence.required:
        if ir.persistence.kind is None:
            errors.append("persistence.kind debe ser 'unknown' o un tipo concreto si persistence.required=True")
        if not ir.persistence.evidence:
            errors.append("persistence.required=True pero falta evidence estructurada (lista de strings)")

        if ir.persistence.kind not in ("relational", "document", "key_value", "object_storage", "event_log", "unknown"):
            errors.append(f"persistence.kind inválido: {ir.persistence.kind!r}")

    return errors


# ---------------------------------------------------------------------
# Persistencia (solo desde contexto estructurado)
# ---------------------------------------------------------------------


def build_persistence_ir_from_context(ctx: Dict[str, Any]) -> PersistenceIR:
    """
    Construye PersistenceIR SOLO desde campos estructurados del contexto normalizado.

    Nota:
    - No interpreta lenguaje natural.
    - No inspecciona descripcion_global.
    - No aplica heurísticas por keywords.
    """
    # 1) Preferir ctx["persistence"]/ctx["persistencia"] si existe.
    for key in ("persistence", "persistencia", "persistence_requirement", "data_lifecycle"):
        v = ctx.get(key)
        if isinstance(v, dict):
            p = _parse_persistence_dict(v, source_key=key)
            # Si el dict existe pero está "vacío" (required=False, sin evidence), NO cortar:
            # permitimos fallback desde state_requirements.
            if p is not None and (p.required or p.evidence or p.business_entities):
                return p

    # 2) Fallback permitido: state_requirements durable con evidence -> persistence requerida
    v_state = ctx.get("state_requirements")
    if isinstance(v_state, dict):
        durable = bool(v_state.get("durable")) if "durable" in v_state else False
        evidence = _list_of_str(v_state.get("evidence"))
        if durable and evidence:
            p = _parse_persistence_dict(v_state, source_key="state_requirements")
            if p is not None:
                return p

    # Formato 3: ctx["requisitos_persistencia"] (lista)
    v = ctx.get("requisitos_persistencia")
    if isinstance(v, list) and v:
        p = _parse_persistence_requirements_list(v)
        if p is not None:
            return p

    return PersistenceIR(required=False, kind=None, durable_state=False, business_entities=[], evidence=[], uncertainty="")


def _parse_persistence_dict(d: Dict[str, Any], *, source_key: str) -> Optional[PersistenceIR]:
    """
    Soporta formatos:
    1) persistence: {required, kind, durable_state, business_entities, evidence, uncertainty}
    2) state_requirements: {durable, entities, evidence}
    """
    # Mapeo state_requirements -> persistence
    if source_key == "state_requirements":
        durable = bool(d.get("durable")) if "durable" in d else False
        entities = d.get("entities")
        evidence = d.get("evidence")
        required = bool(durable)  # regla: durable => persistence requerida
        return PersistenceIR(
            required=required,
            kind="unknown" if required else None,
            durable_state=durable,
            business_entities=_list_of_str(entities),
            evidence=_list_of_str(evidence),
            uncertainty="",
        )

    required = bool(d.get("required")) if "required" in d else False
    durable_state = bool(d.get("durable_state")) if "durable_state" in d else False
    # normalización: si durable_state True, required debe ser True
    if durable_state:
        required = True

    kind_raw = d.get("kind")
    kind = _normalize_persistence_kind(kind_raw) if required else None

    business_entities = _list_of_str(d.get("business_entities"))
    evidence = _list_of_str(d.get("evidence"))
    uncertainty = _coalesce_str(d.get("uncertainty"), "")

    return PersistenceIR(
        required=required,
        kind=kind,
        durable_state=durable_state,
        business_entities=business_entities,
        evidence=evidence,
        uncertainty=uncertainty,
    )


def _parse_persistence_requirements_list(items: List[Any]) -> Optional[PersistenceIR]:
    """
    Formato 3:
    requisitos_persistencia: [{required, entity, evidence}, ...]
    """
    required = False
    entities: List[str] = []
    evidence: List[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if bool(it.get("required")):
            required = True
        ent = _coalesce_str(it.get("entity"), "")
        if ent:
            entities.append(ent)
        ev = _coalesce_str(it.get("evidence"), "")
        if ev:
            evidence.append(ev)

    if not required and not entities and not evidence:
        return None

    if not required:
        # si vienen datos pero required no está explicitado, no asumimos
        return PersistenceIR(required=False, kind=None, durable_state=False, business_entities=[], evidence=[], uncertainty="")

    return PersistenceIR(
        required=True,
        kind="unknown",
        durable_state=True if entities else False,
        business_entities=_dedupe_stable(entities),
        evidence=_dedupe_stable(evidence),
        uncertainty="",
    )


def _normalize_persistence_kind(v: Any) -> Optional[PersistenceKind]:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s in ("relational", "document", "key_value", "object_storage", "event_log", "unknown"):
            return s  # type: ignore[return-value]
    return None


# ---------------------------------------------------------------------
# Helpers (deterministas)
# ---------------------------------------------------------------------


def _coalesce_str(*vals: Any) -> str:
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _list_of_str(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for x in v:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _prefix_list(prefix: str, items: Sequence[str]) -> List[str]:
    return [f"{prefix}: {x}" for x in items if isinstance(x, str) and x.strip()]


def _dedupe_stable(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _contract_key(c: ApiContractIR) -> Tuple[str, str]:
    return (str(c.method or "").upper().strip(), str(c.path or "").strip())


def _dedupe_contracts(items: Sequence[ApiContractIR]) -> List[ApiContractIR]:
    out: List[ApiContractIR] = []
    seen: Dict[Tuple[str, str], int] = {}
    for c in items:
        k = _contract_key(c)
        if k in seen:
            out[seen[k]] = c
        else:
            seen[k] = len(out)
            out.append(c)
    return out


def _parse_contracts(raw: Any) -> List[ApiContractIR]:
    if not isinstance(raw, list):
        return []
    out: List[ApiContractIR] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        method = str(c.get("method") or "").upper().strip()
        path = str(c.get("path") or "").strip()
        if not path:
            continue
        if not path.startswith("/"):
            path = "/" + path

        req = c.get("request") if isinstance(c.get("request"), dict) else {}
        resp = c.get("response") if isinstance(c.get("response"), dict) else {}

        request_type = str(req.get("type") or "none").strip()
        if request_type not in ("json", "multipart", "query", "none"):
            request_type = "none"

        # Normalización defensiva: path params no son query params.
        # Si el path contiene {id}, GET/DELETE no deben marcarse como query.
        if "{id}" in path and request_type == "query" and method in ("GET", "DELETE"):
            request_type = "none"

        schema_hint = req.get("schema_hint")
        request_schema_hint = schema_hint if isinstance(schema_hint, dict) else {}

        response_example = resp.get("json_example")
        response_example_dict = response_example if isinstance(response_example, dict) else {}

        evidence_parts: List[str] = []
        ev_req = req.get("evidence")
        ev_resp = resp.get("evidence")
        if isinstance(ev_req, str) and ev_req.strip():
            evidence_parts.append(ev_req.strip())
        if isinstance(ev_resp, str) and ev_resp.strip():
            evidence_parts.append(ev_resp.strip())
        evidence = "\n".join(evidence_parts)

        assumption = str(req.get("assumption") or "").strip()

        out.append(
            ApiContractIR(
                method=method or "GET",
                path=path,
                description=str(c.get("notes") or "").strip(),
                request_type=request_type,  # type: ignore[arg-type]
                request_schema_hint=request_schema_hint,
                response_example=response_example_dict,
                evidence=evidence,
                assumption=assumption,
            )
        )
    return out


def _ensure_proposed_has_assumption(c: ApiContractIR) -> ApiContractIR:
    if isinstance(c.assumption, str) and c.assumption.strip():
        return c
    return ApiContractIR(
        method=c.method,
        path=c.path,
        description=c.description,
        request_type=c.request_type,
        request_schema_hint=dict(c.request_schema_hint or {}),
        response_example=dict(c.response_example or {}),
        evidence=c.evidence,
        assumption="Endpoint propuesto sin evidencia literal; requiere confirmación del usuario.",
    )


def _has_literal_evidence(c: ApiContractIR) -> bool:
    return bool(str(c.evidence or "").strip())


def _parse_technology_signals(raw: Any) -> List[TechnologySignalIR]:
    if not isinstance(raw, list):
        return []
    out: List[TechnologySignalIR] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        name = _coalesce_str(it.get("name"), "")
        if not name:
            continue
        out.append(
            TechnologySignalIR(
                name=name,
                category=_coalesce_str(it.get("category"), "unknown") or "unknown",
                role=_coalesce_str(it.get("role"), ""),
                evidence=_coalesce_str(it.get("evidence"), ""),
                confidence=_coalesce_str(it.get("confidence"), "unknown") or "unknown",
            )
        )
    return out


def _parse_domain_entities(raw: Any) -> List[DomainEntityIR]:
    if not isinstance(raw, list):
        return []
    out: List[DomainEntityIR] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        evidence = _coalesce_str(it.get("evidence"), "")
        if not evidence:
            continue
        out.append(
            DomainEntityIR(
                name=_coalesce_str(it.get("name"), ""),
                singular=_coalesce_str(it.get("singular"), ""),
                plural=_coalesce_str(it.get("plural"), ""),
                slug=_coalesce_str(it.get("slug"), ""),
                evidence=evidence,
                confidence=_coalesce_str(it.get("confidence"), "unknown") or "unknown",
            )
        )
    return out


def _parse_operation_groups(raw: Any) -> List[OperationGroupIR]:
    if not isinstance(raw, list):
        return []
    out: List[OperationGroupIR] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        evidence = _coalesce_str(it.get("evidence"), "")
        if not evidence:
            continue
        t = _coalesce_str(it.get("type"), "")
        entity = _coalesce_str(it.get("entity"), "")
        if not t or not entity:
            continue
        out.append(
            OperationGroupIR(
                type=t,
                entity=entity,
                evidence=evidence,
                confidence=_coalesce_str(it.get("confidence"), "unknown") or "unknown",
            )
        )
    return out


def _dedupe_domain_entities(items: Sequence[DomainEntityIR]) -> List[DomainEntityIR]:
    out: List[DomainEntityIR] = []
    seen = set()
    for e in items:
        k = (e.slug.strip().lower() or e.name.strip().lower())
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def _dedupe_operation_groups(items: Sequence[OperationGroupIR]) -> List[OperationGroupIR]:
    out: List[OperationGroupIR] = []
    seen = set()
    for g in items:
        k = (g.type.strip().lower(), g.entity.strip().lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(g)
    return out


def _dedupe_tech_signals(items: Sequence[TechnologySignalIR]) -> List[TechnologySignalIR]:
    out: List[TechnologySignalIR] = []
    seen = set()
    for s in items:
        k = (s.name.strip().lower(), s.category.strip().lower(), s.role.strip().lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out
