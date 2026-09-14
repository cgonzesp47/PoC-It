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

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from poc_it.analisis.normalizador_contexto import (
    canonicalize_integration_technology_refs,
    canonicalize_technology_signals,
    stable_identifier,
)
from poc_it.generador.path_utils import has_path_param


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
RequirementSource = Literal["explicit", "inferred", "default", "unknown"]
ImplementationLevel = Literal[
    "fully_local",
    "integration_skeleton",
    "mocked",
    "documentation_only",
]
IntegrationKind = Literal[
    "external_api",
    "database",
    "queue",
    "cache",
    "object_storage",
    "email",
    "auth",
    "observability",
    "runtime",
    "other",
]
ActionKind = Literal[
    "internal_processing",
    "persistence",
    "external_call",
    "validation",
    "transformation",
    "notification",
    "other",
]
CapabilityCoverageStatus = Literal[
    "covered",
    "partially_covered",
    "uncovered",
    "not_api_applicable",
]


@dataclass(frozen=True)
class AuthenticationIR:
    mechanism: str = ""
    credential_source: str = "unknown"
    allows_embedded_secret: bool = False
    allows_static_credential_file: bool = True
    source: RequirementSource = "unknown"
    evidence: str = ""
    assumption: str = ""


@dataclass(frozen=True)
class ConfigurationIR:
    key: str
    purpose: str = ""
    required: bool = False
    secret: bool = False
    source: RequirementSource = "unknown"
    evidence: str = ""
    assumption: str = ""
    delivery: str = "env"


@dataclass(frozen=True)
class IntegrationIR:
    id: str
    name: str
    kind: IntegrationKind = "other"
    role: str = ""
    required: bool = True
    implementation_level: ImplementationLevel = "integration_skeleton"
    authentication: AuthenticationIR = field(default_factory=AuthenticationIR)
    technology_refs: List[str] = field(default_factory=list)
    configuration_refs: List[str] = field(default_factory=list)
    packages: List[str] = field(default_factory=list)
    source: RequirementSource = "unknown"
    evidence: str = ""
    assumption: str = ""


@dataclass(frozen=True)
class ActionIR:
    id: str
    kind: ActionKind
    description: str
    required: bool = True
    integration_ref: Optional[str] = None
    source: RequirementSource = "unknown"
    evidence: str = ""
    assumption: str = ""


@dataclass(frozen=True)
class ErrorIR:
    status_code: Optional[int]
    code: str
    description: str = ""
    required: bool = True
    source: RequirementSource = "unknown"
    evidence: str = ""
    assumption: str = ""


@dataclass(frozen=True)
class ApiContractIR:
    method: str
    path: str
    description: str = ""
    request_type: RequestType = "none"
    request_schema_hint: Dict[str, Any] = field(default_factory=dict)
    response_example: Any = field(default_factory=dict)
    evidence: str = ""
    assumption: str = ""
    actions: List[ActionIR] = field(default_factory=list)
    errors: List[ErrorIR] = field(default_factory=list)
    integration_refs: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class TechnologySignalIR:
    id: str
    name: str
    category: str = "unknown"
    packages: tuple[str, ...] = ()
    import_roots: tuple[str, ...] = ()
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
class CapabilityCoverageIR:
    capability_id: str = ""
    capability: str = ""
    contract_refs: List[str] = field(default_factory=list)
    action_refs: List[str] = field(default_factory=list)
    integration_refs: List[str] = field(default_factory=list)
    status: CapabilityCoverageStatus = "uncovered"
    source: str = "unknown"
    evidence: str = ""
    assumption: str = ""


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
    integrations: List[IntegrationIR] = field(default_factory=list)
    configuration: List[ConfigurationIR] = field(default_factory=list)
    capability_coverage: List[CapabilityCoverageIR] = field(default_factory=list)


def build_request_ir_from_context(
    contexto_normalizado: Dict[str, Any] | None,
    descripcion_global: str,
) -> RequestIR:
    ctx = dict(contexto_normalizado) if isinstance(contexto_normalizado, dict) else {}
    technology_signals_raw, _ = canonicalize_technology_signals(ctx.get("technology_signals") or [])
    integrations_raw, _ = canonicalize_integration_technology_refs(
        ctx.get("integrations") or [],
        technology_signals_raw,
    )
    ctx["technology_signals"] = technology_signals_raw
    ctx["integrations"] = integrations_raw

    product_name = _coalesce_str(ctx.get("nombre_proyecto"), ctx.get("nombre"), "") or "PoC"
    objective = _coalesce_str(ctx.get("objetivo_tecnico"), descripcion_global, "") or ""

    product_capabilities = _list_of_str(ctx.get("funcionalidades_clave"))
    external_integrations = _list_of_str(ctx.get("integraciones_externas"))
    technology_constraints = _list_of_str(ctx.get("restricciones_tecnicas"))
    user_constraints = list(technology_constraints)

    user_facts: List[str] = []
    user_facts.extend(_prefix_list("Actor", _list_of_str(ctx.get("actores_principales"))))
    user_facts.extend(_prefix_list("Riesgo", _list_of_str(ctx.get("riesgos_inherentes"))))
    user_facts.extend(_prefix_list("NFR", _list_of_str(ctx.get("requisitos_no_funcionales"))))

    explicit_api_contracts: List[ApiContractIR] = []
    proposed_api_contracts: List[ApiContractIR] = []

    explicit_raw = ctx.get("contratos_api_explicitos")
    proposed_raw = ctx.get("contratos_api_propuestos")
    legacy_raw = ctx.get("contratos_api")

    explicit_parsed = _parse_contracts(explicit_raw)
    proposed_parsed = _parse_contracts(proposed_raw)
    legacy_parsed = _parse_contracts(legacy_raw)

    if isinstance(explicit_raw, list) or isinstance(proposed_raw, list):
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
                            response_example=c.response_example,
                            evidence="",
                            assumption=c.assumption,
                            actions=list(c.actions or []),
                            errors=list(c.errors or []),
                            integration_refs=list(c.integration_refs or []),
                        )
                    )
                )

        proposed_api_contracts.extend([_ensure_proposed_has_assumption(c) for c in proposed_parsed])

        for c in legacy_parsed:
            if _has_literal_evidence(c):
                explicit_api_contracts.append(c)
            else:
                proposed_api_contracts.append(_ensure_proposed_has_assumption(c))
    else:
        for c in legacy_parsed:
            if _has_literal_evidence(c):
                explicit_api_contracts.append(c)
            else:
                proposed_api_contracts.append(_ensure_proposed_has_assumption(c))

    proposed_api_contracts = [_ensure_proposed_has_assumption(c) for c in proposed_api_contracts]

    technology_signals = _parse_technology_signals(ctx.get("technology_signals"))
    domain_entities = _parse_domain_entities(ctx.get("domain_entities"))
    operation_groups = _parse_operation_groups(ctx.get("operation_groups"))
    integrations = _resolve_integrations(
        structured_integrations=ctx.get("integrations"),
        technology_signals=ctx.get("technology_signals"),
    )
    configuration = _parse_configuration(ctx.get("configuration"))
    persistence = build_persistence_ir_from_context(ctx)

    assumptions: List[str] = []
    assumptions.extend(_list_of_str(ctx.get("assumptions")))
    open_questions: List[str] = _list_of_str(ctx.get("open_questions"))

    if not explicit_api_contracts and proposed_api_contracts:
        assumptions.append(
            "Se han propuesto endpoints sin evidencia literal; deben confirmarse con el usuario antes de tratarlos como contrato explícito."
        )

    if persistence.required and not persistence.evidence:
        open_questions.append(
            "Se requiere persistencia según el contexto estructurado, pero falta evidencia textual/citas en el campo persistence.evidence."
        )

    if persistence.required and persistence.kind is None:
        persistence = PersistenceIR(
            required=True,
            kind="unknown",
            durable_state=persistence.durable_state,
            business_entities=list(persistence.business_entities),
            evidence=list(persistence.evidence),
            uncertainty=persistence.uncertainty,
        )
        open_questions.append(
            "Persistencia requerida pero el tipo (kind) no está especificado; se ha normalizado a 'unknown'."
        )

    capability_coverage = _build_capability_coverage(
        ctx=ctx,
        product_capabilities=product_capabilities,
        open_questions_sink=open_questions,
    )

    ir = RequestIR(
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
        integrations=_dedupe_integrations(integrations),
        configuration=_dedupe_configuration(configuration),
        capability_coverage=_dedupe_capability_coverage(capability_coverage),
    )

    ir = reconcile_request_ir_capability_coverage(ir)
    ensure_valid_request_ir_capability_coverage(ir)

    return ir


def request_ir_to_dict(ir: RequestIR) -> Dict[str, Any]:
    return asdict(ir)


def validate_request_ir(ir: RequestIR) -> List[str]:
    errors: List[str] = []

    if not isinstance(ir.product_name, str) or not ir.product_name.strip():
        errors.append("product_name requerido")
    if not isinstance(ir.objective, str) or not ir.objective.strip():
        errors.append("objective requerido")

    technology_signal_ids = {
        s.id.strip() for s in ir.technology_signals or [] if s.id.strip()
    }
    integration_ids = {i.id.strip().lower() for i in ir.integrations or [] if i.id.strip()}
    configuration_keys = {c.key.strip().lower() for c in ir.configuration or [] if c.key.strip()}

    for integration in ir.integrations or []:
        if not integration.id.strip():
            errors.append("integration.id requerido")
        if not integration.name.strip():
            errors.append("integration.name requerido")
        if integration.source == "explicit" and not integration.evidence.strip():
            errors.append(
                f"integration.source=explicit requiere evidence: {integration.id or integration.name}"
            )
        if (
            integration.authentication.source == "explicit"
            and not integration.authentication.evidence.strip()
        ):
            errors.append(
                f"authentication.source=explicit requiere evidence en integración: {integration.id or integration.name}"
            )
        for configuration_ref in integration.configuration_refs or []:
            if configuration_ref.strip().lower() not in configuration_keys:
                errors.append(
                    f"configuration_ref inexistente en integración {integration.id}: {configuration_ref}"
                )
        for technology_ref in integration.technology_refs or []:
            if technology_ref.strip() not in technology_signal_ids:
                errors.append(
                    f"technology_ref inexistente en integración {integration.id}: {technology_ref}"
                )

    for config in ir.configuration or []:
        if not config.key.strip():
            errors.append("configuration.key requerido")
        if config.source == "explicit" and not config.evidence.strip():
            errors.append(f"configuration.source=explicit requiere evidence: {config.key}")

    for contract in (ir.explicit_api_contracts or []) + (ir.proposed_api_contracts or []):
        if not str(contract.method).strip():
            errors.append("ApiContractIR.method vacío")
        if not str(contract.path).strip() or not str(contract.path).startswith("/"):
            errors.append(f"ApiContractIR.path inválido: {contract.path!r}")
        if contract.request_type not in ("json", "multipart", "query", "none"):
            errors.append(f"ApiContractIR.request_type inválido: {contract.request_type!r}")

        for integration_ref in contract.integration_refs or []:
            if integration_ref.strip().lower() not in integration_ids:
                errors.append(
                    f"integration_ref inexistente en contrato {contract.method} {contract.path}: {integration_ref}"
                )

        for action in contract.actions or []:
            if not action.id.strip():
                errors.append(f"action.id requerido en contrato {contract.method} {contract.path}")
            if not action.description.strip():
                errors.append(
                    f"action.description requerida en contrato {contract.method} {contract.path}: {action.id or '<sin_id>'}"
                )
            if action.source == "explicit" and not action.evidence.strip():
                errors.append(
                    f"action.source=explicit requiere evidence en contrato {contract.method} {contract.path}: {action.id or '<sin_id>'}"
                )
            if action.kind == "external_call" and not _coalesce_str(action.integration_ref, "").strip():
                errors.append(
                    f"external_call requiere integration_ref en contrato {contract.method} {contract.path}: {action.id or '<sin_id>'}"
                )
            if (
                _coalesce_str(action.integration_ref, "").strip()
                and action.integration_ref.strip().lower() not in integration_ids
            ):
                errors.append(
                    f"integration_ref inexistente en acción {action.id} de {contract.method} {contract.path}: {action.integration_ref}"
                )

        for error_item in contract.errors or []:
            if not error_item.code.strip():
                errors.append(f"error.code requerido en contrato {contract.method} {contract.path}")
            if error_item.source == "explicit" and not error_item.evidence.strip():
                errors.append(
                    f"error.source=explicit requiere evidence en contrato {contract.method} {contract.path}: {error_item.code or '<sin_code>'}"
                )

    for c in ir.explicit_api_contracts or []:
        if not _coalesce_str(c.evidence, "").strip():
            errors.append(f"explicit_api_contracts sin evidence: {c.method} {c.path}")

    if not ir.persistence.required:
        if ir.persistence.kind is not None:
            errors.append("persistence.kind no debe informarse si persistence.required=False")
        if ir.persistence.durable_state:
            errors.append("persistence.durable_state no debe ser True si persistence.required=False")
        if ir.persistence.business_entities:
            errors.append("persistence.business_entities no debe informarse si persistence.required=False")
        if ir.persistence.evidence:
            errors.append("persistence.evidence no debe informarse si persistence.required=False")

    if ir.persistence.required:
        if ir.persistence.kind is None:
            errors.append(
                "persistence.kind debe ser 'unknown' o un tipo concreto si persistence.required=True"
            )
        if not ir.persistence.evidence:
            errors.append("persistence.required=True pero falta evidence estructurada (lista de strings)")

        if ir.persistence.kind not in (
            "relational",
            "document",
            "key_value",
            "object_storage",
            "event_log",
            "unknown",
        ):
            errors.append(f"persistence.kind inválido: {ir.persistence.kind!r}")

    errors.extend(validate_capability_coverage(ir))
    return errors


def validate_capability_coverage(ir: RequestIR) -> List[str]:
    errors: List[str] = []
    contract_index = _build_contract_index(ir)
    action_index = _build_action_index(ir)
    integration_ids = {i.id.strip().lower() for i in ir.integrations or [] if i.id.strip()}
    integration_kind_by_id = {
        i.id.strip().lower(): i.kind for i in ir.integrations or [] if i.id.strip()
    }

    if ir.product_capabilities and not ir.capability_coverage:
        errors.append(
            "Existen capacidades funcionales, pero el normalizador no produjo capability_coverage."
        )
        return errors

    matched_capability_ids = {
        coverage.capability_id.strip().lower()
        for coverage in ir.capability_coverage or []
        if coverage.capability_id.strip()
    }

    for capability_index, capability in enumerate(ir.product_capabilities or []):
        expected_id = _stable_capability_id(capability, capability_index)
        coverage = _find_coverage(ir.capability_coverage, capability, expected_id)
        if coverage is None:
            if matched_capability_ids:
                errors.append(
                    "No se pudo vincular inequívocamente capability_coverage con funcionalidades_clave."
                )
                errors.append(f"Capacidad obligatoria sin cobertura vinculada: {capability}")
            else:
                errors.append(f"Capacidad obligatoria sin cobertura: {capability}")
            continue

        contract_refs = [ref for ref in coverage.contract_refs or [] if ref.strip()]
        action_refs = [ref for ref in coverage.action_refs or [] if ref.strip()]
        integration_refs = [ref for ref in coverage.integration_refs or [] if ref.strip()]

        valid_contract_refs: List[str] = []
        valid_action_refs: List[str] = []
        valid_integration_refs: List[str] = []

        for ref in contract_refs:
            if ref not in contract_index:
                errors.append(f"Capability coverage referencia contrato inexistente: {ref}")
            else:
                valid_contract_refs.append(ref)

        for ref in action_refs:
            if ref not in action_index:
                errors.append(f"Capability coverage referencia acción inexistente: {ref}")
            else:
                valid_action_refs.append(ref)

        for ref in integration_refs:
            if ref.strip().lower() not in integration_ids:
                errors.append(f"Capability coverage referencia integración inexistente: {ref}")
            else:
                valid_integration_refs.append(ref)

        if coverage.status == "uncovered":
            errors.append(f"Capacidad obligatoria sin cobertura: {capability}")

        if (
            coverage.status == "covered"
            and coverage.status != "not_api_applicable"
            and not (valid_contract_refs or valid_action_refs or valid_integration_refs)
        ):
            errors.append(f"Capacidad marcada como covered sin referencias: {capability}")

        if valid_integration_refs:
            ref_kinds = {
                integration_kind_by_id.get(ref.strip().lower(), "other")
                for ref in valid_integration_refs
            }
            # Una integración de tipo "database" se materializa como acceso a datos propio
            # (ORM/driver local), no como una llamada de red a un tercero: exigirle una acción
            # "external_call" es un falso positivo para cualquier PoC con persistencia (p.ej.
            # PostgreSQL). Solo las integraciones que sí son servicios externos genuinos
            # (external_api, queue, email, auth, ...) requieren esa acción.
            if ref_kinds == {"database"}:
                if not _coverage_has_action_kind(coverage, action_index, "persistence"):
                    errors.append(
                        f"Capacidad con integración de base de datos pero sin acción de persistencia: {capability}"
                    )
            elif not _coverage_has_external_action(coverage, action_index):
                errors.append(
                    f"Capacidad con integración externa pero sin acción external_call: {capability}"
                )

        for action_ref in valid_action_refs:
            _, action = action_index[action_ref]
            if action.kind != "external_call":
                continue
            if not _coalesce_str(action.integration_ref, "").strip():
                errors.append(
                    f"Capacidad con acción external_call pero sin integración asociada: {capability}"
                )
                continue
            if action.integration_ref.strip().lower() not in integration_ids:
                errors.append(
                    f"Capacidad con acción external_call pero sin integración asociada: {capability}"
                )
                continue
            if action.integration_ref not in valid_integration_refs:
                errors.append(
                    f"Capacidad con acción external_call pero sin integración asociada: {capability}"
                )

    return _dedupe_stable(errors)


def ensure_valid_request_ir_capability_coverage(ir: RequestIR) -> None:
    coverage_errors = validate_capability_coverage(ir)
    if coverage_errors:
        raise ValueError("; ".join(coverage_errors))


def reconcile_request_ir_capability_coverage(ir: RequestIR) -> RequestIR:
    contract_index = _build_contract_index(ir)
    action_index = _build_action_index(ir)

    reconciled: List[CapabilityCoverageIR] = []

    for coverage in ir.capability_coverage or []:
        contract_refs = list(dict.fromkeys(coverage.contract_refs or []))
        action_refs = list(dict.fromkeys(coverage.action_refs or []))
        integration_refs = list(dict.fromkeys(coverage.integration_refs or []))

        for action_ref in action_refs:
            indexed = action_index.get(action_ref)
            if indexed is None:
                continue
            _, action = indexed
            if (
                action.kind == "external_call"
                and action.integration_ref
                and action.integration_ref not in integration_refs
            ):
                integration_refs.append(action.integration_ref)

        if integration_refs:
            for contract_ref in contract_refs:
                contract = contract_index.get(contract_ref)
                if contract is None:
                    continue

                matching_actions: List[ActionIR] = []

                for action in contract.actions or []:
                    if action.kind != "external_call":
                        continue
                    if action.integration_ref not in integration_refs:
                        continue
                    matching_actions.append(action)

                if len(matching_actions) == 1:
                    action = matching_actions[0]
                    action_ref = f"{contract_ref}#{action.id}"
                    if action_ref not in action_refs:
                        action_refs.append(action_ref)

        reconciled.append(
            replace(
                coverage,
                contract_refs=contract_refs,
                action_refs=action_refs,
                integration_refs=integration_refs,
            )
        )

    return replace(ir, capability_coverage=reconciled)


def build_persistence_ir_from_context(ctx: Dict[str, Any]) -> PersistenceIR:
    for key in ("persistence", "persistencia", "persistence_requirement", "data_lifecycle"):
        v = ctx.get(key)
        if isinstance(v, dict):
            p = _parse_persistence_dict(v, source_key=key)
            if p is not None and (p.required or p.evidence or p.business_entities):
                return p

    v_state = ctx.get("state_requirements")
    if isinstance(v_state, dict):
        durable = bool(v_state.get("durable")) if "durable" in v_state else False
        evidence = _list_of_str(v_state.get("evidence"))
        if durable and evidence:
            p = _parse_persistence_dict(v_state, source_key="state_requirements")
            if p is not None:
                return p

    v = ctx.get("requisitos_persistencia")
    if isinstance(v, list) and v:
        p = _parse_persistence_requirements_list(v)
        if p is not None:
            return p

    return PersistenceIR(
        required=False,
        kind=None,
        durable_state=False,
        business_entities=[],
        evidence=[],
        uncertainty="",
    )


def _parse_persistence_dict(d: Dict[str, Any], *, source_key: str) -> Optional[PersistenceIR]:
    if source_key == "state_requirements":
        durable = bool(d.get("durable")) if "durable" in d else False
        entities = d.get("entities")
        evidence = d.get("evidence")
        required = bool(durable)
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
        return PersistenceIR(
            required=False,
            kind=None,
            durable_state=False,
            business_entities=[],
            evidence=[],
            uncertainty="",
        )

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


def _build_capability_coverage(
    *,
    ctx: Dict[str, Any],
    product_capabilities: List[str],
    open_questions_sink: List[str],
) -> List[CapabilityCoverageIR]:
    del open_questions_sink
    raw = ctx.get("capability_coverage")
    items = _parse_capability_coverage(raw)

    if product_capabilities and not items:
        return []

    return items


def _parse_capability_coverage(raw: Any) -> List[CapabilityCoverageIR]:
    if not isinstance(raw, list):
        return []
    out: List[CapabilityCoverageIR] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        capability = _coalesce_str(item.get("capability"), "")
        if not capability:
            continue
        status = str(item.get("status") or "uncovered").strip()
        if status not in ("covered", "partially_covered", "uncovered", "not_api_applicable"):
            status = "uncovered"
        source = str(item.get("source") or "unknown").strip()
        if source not in ("explicit", "inferred", "unknown"):
            source = "unknown"
        out.append(
            CapabilityCoverageIR(
                capability_id=_coalesce_str(item.get("capability_id"), ""),
                capability=capability,
                contract_refs=_dedupe_stable(_list_of_str(item.get("contract_refs"))),
                action_refs=_dedupe_stable(_list_of_str(item.get("action_refs"))),
                integration_refs=_dedupe_stable(_list_of_str(item.get("integration_refs"))),
                status=status,  # type: ignore[arg-type]
                source=source,
                evidence=_coalesce_str(item.get("evidence"), ""),
                assumption=_coalesce_str(item.get("assumption"), ""),
            )
        )
    return _dedupe_capability_coverage(out)


def _find_coverage(
    items: Sequence[CapabilityCoverageIR], capability: str, capability_id: str = ""
) -> Optional[CapabilityCoverageIR]:
    capability_id_key = capability_id.strip().lower()
    if capability_id_key:
        for item in items:
            if item.capability_id.strip().lower() == capability_id_key:
                return item

    key = capability.strip().lower()
    for item in items:
        if item.capability.strip().lower() == key:
            return item
    return None


def _normalize_contract_ref(method: str, path: str) -> str:
    return f"{method.upper().strip()} {path.strip()}"


def _build_contract_index(ir: RequestIR) -> Dict[str, ApiContractIR]:
    index: Dict[str, ApiContractIR] = {}
    for contract in [*(ir.explicit_api_contracts or []), *(ir.proposed_api_contracts or [])]:
        index[_normalize_contract_ref(contract.method, contract.path)] = contract
    return index


def _build_action_index(ir: RequestIR) -> Dict[str, Tuple[ApiContractIR, ActionIR]]:
    index: Dict[str, Tuple[ApiContractIR, ActionIR]] = {}
    for contract in [*(ir.explicit_api_contracts or []), *(ir.proposed_api_contracts or [])]:
        contract_ref = _normalize_contract_ref(contract.method, contract.path)
        for action in contract.actions or []:
            index[f"{contract_ref}#{action.id}"] = (contract, action)
    return index


def _coverage_has_external_action(
    coverage: CapabilityCoverageIR,
    action_index: Dict[str, Tuple[ApiContractIR, ActionIR]],
) -> bool:
    return _coverage_has_action_kind(coverage, action_index, "external_call")


def _coverage_has_action_kind(
    coverage: CapabilityCoverageIR,
    action_index: Dict[str, Tuple[ApiContractIR, ActionIR]],
    kind: ActionKind,
) -> bool:
    for action_ref in coverage.action_refs:
        indexed = action_index.get(action_ref)
        if indexed is None:
            continue
        _, action = indexed
        if action.kind == kind:
            return True
    return False


def _stable_capability_id(capability: str, index: int) -> str:
    normalized = str(capability or "").strip().lower()
    normalized = "".join(ch if ch.isalnum() else "_" for ch in normalized)
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    normalized = normalized.strip("_")
    if not normalized:
        normalized = f"capability_{index + 1}"
    return normalized[:80]


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


def _dedupe_last_by_key(items: Sequence[Any], key_fn: Any) -> List[Any]:
    out: List[Any] = []
    seen: Dict[Any, int] = {}
    for item in items:
        key = key_fn(item)
        if key in seen:
            out[seen[key]] = item
        else:
            seen[key] = len(out)
            out.append(item)
    return out


def _normalize_source(v: Any) -> RequirementSource:
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("explicit", "inferred", "default", "unknown"):
            return s  # type: ignore[return-value]
    return "unknown"


def _normalize_implementation_level(v: Any) -> ImplementationLevel:
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("fully_local", "integration_skeleton", "mocked", "documentation_only"):
            return s  # type: ignore[return-value]
    return "integration_skeleton"


def _normalize_integration_kind(v: Any) -> IntegrationKind:
    if isinstance(v, str):
        s = v.strip().lower()
        if s in (
            "external_api",
            "database",
            "queue",
            "cache",
            "object_storage",
            "email",
            "auth",
            "observability",
            "runtime",
            "other",
        ):
            return s  # type: ignore[return-value]
    return "other"


def _normalize_action_kind(v: Any) -> ActionKind:
    if isinstance(v, str):
        s = v.strip().lower()
        if s in (
            "internal_processing",
            "persistence",
            "external_call",
            "validation",
            "transformation",
            "notification",
            "other",
        ):
            return s  # type: ignore[return-value]
    return "other"


def _normalize_configuration_delivery(v: Any) -> str:
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("env", "file", "argument", "runtime", "unknown"):
            return s
    return "env"


def _normalize_credential_source(v: Any) -> str:
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("runtime", "file", "environment", "request", "unknown"):
            return s
    return "unknown"


def _contract_key(c: ApiContractIR) -> Tuple[str, str]:
    return (str(c.method or "").upper().strip(), str(c.path or "").strip())


def _dedupe_contracts(items: Sequence[ApiContractIR]) -> List[ApiContractIR]:
    return _dedupe_last_by_key(items, _contract_key)


def _dedupe_integrations(items: Sequence[IntegrationIR]) -> List[IntegrationIR]:
    return _dedupe_last_by_key(items, lambda i: i.id.strip().lower())


def _dedupe_configuration(items: Sequence[ConfigurationIR]) -> List[ConfigurationIR]:
    return _dedupe_last_by_key(items, lambda c: c.key.strip().lower())


def _dedupe_actions(items: Sequence[ActionIR]) -> List[ActionIR]:
    return _dedupe_last_by_key(items, lambda a: a.id.strip().lower())


def _dedupe_errors(items: Sequence[ErrorIR]) -> List[ErrorIR]:
    return _dedupe_last_by_key(items, lambda e: (e.status_code, e.code.strip().lower()))


def _dedupe_capability_coverage(
    items: Sequence[CapabilityCoverageIR],
) -> List[CapabilityCoverageIR]:
    deduped: List[CapabilityCoverageIR] = []
    seen = set()
    for index, item in enumerate(items):
        key = item.capability_id.strip().lower() or f"{item.capability.strip().lower()}::{index}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


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

        if has_path_param(path) and request_type == "query" and method in ("GET", "DELETE"):
            request_type = "none"

        schema_hint = req.get("schema_hint")
        request_schema_hint = schema_hint if isinstance(schema_hint, dict) else {}

        response_example = resp.get("json_example")
        if isinstance(response_example, (dict, list)):
            response_example_norm: Any = response_example
        else:
            response_example_norm = {}

        evidence_parts: List[str] = []
        ev_req = req.get("evidence")
        ev_resp = resp.get("evidence")
        if isinstance(ev_req, str) and ev_req.strip():
            evidence_parts.append(ev_req.strip())
        if isinstance(ev_resp, str) and ev_resp.strip():
            evidence_parts.append(ev_resp.strip())
        evidence = "\n".join(evidence_parts)

        assumption = _coalesce_str(req.get("assumption"), "")

        out.append(
            ApiContractIR(
                method=method or "GET",
                path=path,
                description=str(c.get("notes") or "").strip(),
                request_type=request_type,  # type: ignore[arg-type]
                request_schema_hint=request_schema_hint,
                response_example=response_example_norm,
                evidence=evidence,
                assumption=assumption,
                actions=_parse_actions(c.get("actions")),
                errors=_parse_errors(c.get("errors")),
                integration_refs=_dedupe_stable(_list_of_str(c.get("integration_refs"))),
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
        response_example=c.response_example,
        evidence=c.evidence,
        assumption="Endpoint propuesto sin evidencia literal; requiere confirmación del usuario.",
        actions=list(c.actions or []),
        errors=list(c.errors or []),
        integration_refs=list(c.integration_refs or []),
    )


def _has_literal_evidence(c: ApiContractIR) -> bool:
    return bool(str(c.evidence or "").strip())


def _parse_authentication(raw: dict | None) -> AuthenticationIR | None:
    if not isinstance(raw, dict):
        return None

    return AuthenticationIR(
        mechanism=str(raw.get("mechanism") or "").strip(),
        credential_source=str(raw.get("credential_source") or "unknown").strip(),
        allows_embedded_secret=bool(raw.get("allows_embedded_secret", False)),
        allows_static_credential_file=bool(raw.get("allows_static_credential_file", True)),
        source=_normalize_source(raw.get("source")),
        evidence=str(raw.get("evidence") or "").strip(),
        assumption=str(raw.get("assumption") or "").strip(),
    )


def _parse_integrations(raw: Any) -> List[IntegrationIR]:
    if not isinstance(raw, list):
        return []
    out: List[IntegrationIR] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        integration_id = _coalesce_str(item.get("id"), "")
        name = _coalesce_str(item.get("name"), "")
        if not integration_id or not name:
            continue
        auth = _parse_authentication(item.get("authentication")) or AuthenticationIR()
        packages = _dedupe_stable(_list_of_str(item.get("packages")))

        out.append(
            IntegrationIR(
                id=integration_id,
                name=name,
                kind=_normalize_integration_kind(item.get("kind")),
                role=_coalesce_str(item.get("role"), ""),
                required=bool(item.get("required")) if "required" in item else True,
                implementation_level=_normalize_implementation_level(
                    item.get("implementation_level")
                ),
                authentication=auth,
                technology_refs=_dedupe_stable(_list_of_str(item.get("technology_refs"))),
                configuration_refs=_dedupe_stable(_list_of_str(item.get("configuration_refs"))),
                packages=packages,
                source=_normalize_source(item.get("source")),
                evidence=_coalesce_str(item.get("evidence"), ""),
                assumption=_coalesce_str(item.get("assumption"), ""),
            )
        )
    return _dedupe_integrations(out)


def _resolve_integrations(
    *,
    structured_integrations: Any,
    technology_signals: Any,
) -> List[IntegrationIR]:
    parsed_structured = _parse_integrations(structured_integrations)
    if parsed_structured:
        return parsed_structured
    return _infer_integrations_from_context(technology_signals=technology_signals)


def _infer_integrations_from_context(
    *,
    technology_signals: Any,
) -> List[IntegrationIR]:
    del technology_signals
    return []


def _parse_configuration(raw: Any) -> List[ConfigurationIR]:
    if not isinstance(raw, list):
        return []
    out: List[ConfigurationIR] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = _coalesce_str(item.get("key"), "")
        if not key:
            continue
        out.append(
            ConfigurationIR(
                key=key,
                purpose=_coalesce_str(item.get("purpose"), ""),
                required=bool(item.get("required")) if "required" in item else False,
                secret=bool(item.get("secret")) if "secret" in item else False,
                source=_normalize_source(item.get("source")),
                evidence=_coalesce_str(item.get("evidence"), ""),
                assumption=_coalesce_str(item.get("assumption"), ""),
                delivery=_normalize_configuration_delivery(item.get("delivery")),
            )
        )
    return _dedupe_configuration(out)


def _parse_actions(raw: Any) -> List[ActionIR]:
    if not isinstance(raw, list):
        return []
    out: List[ActionIR] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action_id = _coalesce_str(item.get("id"), "")
        description = _coalesce_str(item.get("description"), "")
        if not action_id or not description:
            continue
        integration_ref = _coalesce_str(item.get("integration_ref"), "")
        out.append(
            ActionIR(
                id=action_id,
                kind=_normalize_action_kind(item.get("kind")),
                description=description,
                required=bool(item.get("required")) if "required" in item else True,
                integration_ref=integration_ref or None,
                source=_normalize_source(item.get("source")),
                evidence=_coalesce_str(item.get("evidence"), ""),
                assumption=_coalesce_str(item.get("assumption"), ""),
            )
        )
    return _dedupe_actions(out)


def _parse_errors(raw: Any) -> List[ErrorIR]:
    if not isinstance(raw, list):
        return []
    out: List[ErrorIR] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = _coalesce_str(item.get("code"), "")
        if not code:
            continue
        status_code_raw = item.get("status_code")
        if isinstance(status_code_raw, bool):
            status_code: Optional[int] = None
        elif isinstance(status_code_raw, int):
            status_code = status_code_raw
        elif isinstance(status_code_raw, float) and status_code_raw.is_integer():
            status_code = int(status_code_raw)
        else:
            status_code = None

        out.append(
            ErrorIR(
                status_code=status_code,
                code=code,
                description=_coalesce_str(item.get("description"), ""),
                required=bool(item.get("required")) if "required" in item else True,
                source=_normalize_source(item.get("source")),
                evidence=_coalesce_str(item.get("evidence"), ""),
                assumption=_coalesce_str(item.get("assumption"), ""),
            )
        )
    return _dedupe_errors(out)


def _parse_technology_signal(item: dict) -> TechnologySignalIR:
    name = str(item.get("name") or "").strip()

    technology_id = str(item.get("id") or "").strip()
    if not technology_id:
        technology_id = stable_identifier(name)

    return TechnologySignalIR(
        id=technology_id,
        name=name,
        category=str(item.get("category") or "unknown").strip(),
        packages=tuple(
            str(value).strip()
            for value in (
                item.get("packages")
                or ([item.get("package")] if str(item.get("package") or "").strip() else [])
            )
            if str(value).strip()
        ),
        import_roots=tuple(
            str(value).strip()
            for value in (item.get("import_roots") or [])
            if str(value).strip()
        ),
        role=str(item.get("role") or "").strip(),
        evidence=str(item.get("evidence") or "").strip(),
        confidence=str(item.get("confidence") or "unknown").strip(),
    )


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
        out.append(_parse_technology_signal(it))
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
        k = e.slug.strip().lower() or e.name.strip().lower()
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
        k = s.id.strip().lower() or (
            s.name.strip().lower(),
            s.category.strip().lower(),
            s.role.strip().lower(),
        )
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out
