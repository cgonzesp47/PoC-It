"""
ScopeGuardian - Estimador de Esfuerzo basado en LLM

Nueva estrategia:
- El LLM infiere el esfuerzo humano (Junior / Senior).
- Se solicita salida estructurada en JSON.
- Se mantiene control limitando rango y ahorro máximo.
- Se usa tiempo real medido para ScopeGuardian.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any, Mapping, Optional

from poc_it.infraestructura.llm_client import chat_completion_json


# ==========================================================
# MODELO DE RESULTADO
# ==========================================================

@dataclass
class EstimacionEsfuerzo:
    junior_min: float
    junior_max: float
    senior_min: float
    senior_max: float
    horas_scopeguardian: float
    ahorro_vs_junior: float
    ahorro_vs_senior: float


# ==========================================================
# PROMPT LLM
# ==========================================================


PROMPT_ESTIMACION_ESTRUCTURADA = """
Actúa como un arquitecto software senior pragmático.

Debes estimar el tiempo TOTAL necesario para implementar una **PoC mínima funcional (MVP)**.

Fuente de verdad:
- Si se proporciona un SPEC, debes basarte principalmente en el SPEC.
- Si NO hay SPEC (p.ej. modo ASESOR), debes basarte en el CONTEXTO_NORMALIZADO.
- Si faltan datos, utiliza la descripción textual solo como apoyo (sin inventar requisitos).

Principios obligatorios:
- Asume implementación directa, sin burocracia corporativa.
- No asumas configuración avanzada de red, VPC, IAM granular ni hardening.
- No incluyas optimizaciones enterprise ni arquitectura futura.

Referencia orientativa realista (Senior):
- 1 endpoint simple: 2–3h
- Integración externa simple (SDK oficial): 3–6h
- Deploy cloud básico: 1–3h
- CRUD con base de datos simple: 6–10h

Devuelve ÚNICAMENTE JSON válido con esta estructura:

{
  "junior_horas": number,
  "senior_horas": number,
  "complejidad": "BAJA | MEDIA | ALTA | CRITICA",
  "justificacion": "breve explicación técnica"
}

No añadas texto fuera del JSON.
"""


# (Eliminado: ahora usamos estimación estructurada en ambos casos)


# ==========================================================
# CONFIGURACIÓN (evitar números mágicos)
# ==========================================================


@dataclass(frozen=True)
class ParametrosEstimacion:
    """Parámetros de tuning para mantener estabilidad y mantenibilidad.

    Nota: Aunque el LLM estime, estos parámetros actúan como guardrails.
    """

    # LLM / parsing
    max_tokens_estimacion: int = 450

    # Guardrails / ajustes
    factor_escenario_simple: float = 0.9

    # Descuento por modo PARCIAL: el repo generado puede contener placeholders y pasos manuales.
    factor_modo_parcial: float = 0.6

    # Descuento adicional si el flujo terminó degradado a "contract-lite" (suite mínima).
    factor_degrade_contract_lite: float = 0.4

    # Fallbacks (si el LLM falla o devuelve valores no válidos)
    fallback_junior_horas: float = 24.0
    fallback_senior_horas: float = 12.0
    fallback_complejidad: str = "MEDIA"

    # Guardrails mínimos
    min_junior_horas: float = 8.0
    min_senior_horas: float = 4.0

    # Márgenes de incertidumbre
    margen_generable: float = 0.15
    margen_no_generable: float = 0.25

    # Porcentaje máximo de ahorro mostrado
    max_ahorro_pct: float = 90.0

    # Límites máximos senior por complejidad (para evitar outliers)
    limites_senior_por_complejidad: Mapping[str, int] = field(
        default_factory=lambda: {
            "BAJA": 14,
            "MEDIA": 32,
            "ALTA": 70,
            "CRITICA": 140,
        }
    )


PARAMETROS_ESTIMACION = ParametrosEstimacion()

logger = logging.getLogger(__name__)


_ESTIMACION_JSON_FALLBACK: dict[str, Any] = {
    "junior_horas": PARAMETROS_ESTIMACION.fallback_junior_horas,
    "senior_horas": PARAMETROS_ESTIMACION.fallback_senior_horas,
    "complejidad": PARAMETROS_ESTIMACION.fallback_complejidad,
}


def _extraer_json_objeto(texto: str) -> str:
    """Extrae el primer objeto JSON de un texto.

    Robustece el parseo ante respuestas del LLM con:
    - fences ```json ... ```
    - texto antes/después del JSON
    """
    t = texto.strip()

    # Eliminar fences simples
    if t.startswith("```"):
        lines = t.splitlines()
        # quita primera y última línea si parecen fences
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()

    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return texto
    return t[start : end + 1]


def _parse_json_or_fallback(raw: Any, fallback: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return dict(fallback)

    candidato = _extraer_json_objeto(raw)
    try:
        parsed = json.loads(candidato)
    except Exception:
        return dict(fallback)
    return parsed if isinstance(parsed, dict) else dict(fallback)




def _modo_a_instruccion(modo: str | None) -> str:
    """
    Añade contexto operativo al prompt para reducir ambigüedad sin meter heurísticas duras.
    """
    if not modo:
        return "Modo: (no especificado). Estima en base al alcance descrito/estructurado."

    modo_upper = modo.upper()
    if modo_upper == "ASESOR":
        return (
            "Modo: ASESOR. NO hay código generado. Estima el esfuerzo humano para implementar la PoC "
            "descrita por el usuario, usando el contexto/spec como fuente de verdad."
        )
    if modo_upper == "PARCIAL":
        return (
            "Modo: PARCIAL. IMPORTANTE: el sistema puede no tener acceso a integraciones externas reales "
            "(p.ej., DB, APIs, cloud). Estima el esfuerzo humano para implementar SOLO lo descrito en el SPEC "
            "como PoC base ejecutable, permitiendo stubs/mocks/placeholders para integraciones externas y "
            "dejando pasos manuales para completar la integración real. NO incluyas configurar la integración "
            "real fuera del repo (infra, credenciales, cuentas cloud) salvo un setup local mínimo."
        )
    if modo_upper == "COMPLETO":
        return (
            "Modo: COMPLETO. Se pretende implementar el SPEC completo como PoC funcional. Estima el esfuerzo humano "
            "para construir lo definido en el SPEC."
        )

    return f"Modo: {modo_upper}. Estima en base al alcance descrito/estructurado."


def _to_json_block(label: str, data: Optional[Mapping[str, Any]]) -> str:
    if not data:
        return f"{label}:\n(no disponible)\n"
    try:
        payload = json.dumps(data, ensure_ascii=False)
    except Exception:
        payload = str(data)
    return f"{label}:\n{payload}\n"


def _metricas_desde_spec(spec: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """
    Deriva métricas estrictamente a partir del SPEC (sin heurísticas por keywords).

    Nota: No asumimos semántica “DB/persistencia/auth” por strings.
    Solo contamos elementos estructurados presentes en el spec.
    """
    if not spec or not isinstance(spec, Mapping):
        return {
            "num_endpoints": 0,
            "num_files": 0,
            "num_dependencies": 0,
            "num_env_vars": 0,
            "num_contract_rules": 0,
            "num_restrictions": 0,
        }

    endpoints = spec.get("endpoints") or []
    files = spec.get("files") or []
    deps = spec.get("dependencies") or []
    env = spec.get("env") or []
    contracts = spec.get("contracts") or []
    restrictions = spec.get("restrictions") or []

    num_contract_rules = 0
    if isinstance(contracts, list):
        for c in contracts:
            if isinstance(c, Mapping):
                rules = c.get("rules") or []
                if isinstance(rules, list):
                    num_contract_rules += len(rules)

    return {
        "num_endpoints": len(endpoints) if isinstance(endpoints, list) else 0,
        "num_files": len(files) if isinstance(files, list) else 0,
        "num_dependencies": len(deps) if isinstance(deps, list) else 0,
        "num_env_vars": len(env) if isinstance(env, list) else 0,
        "num_contract_rules": num_contract_rules,
        "num_restrictions": len(restrictions) if isinstance(restrictions, list) else 0,
    }


def _build_prompt_estimacion(
    *,
    descripcion_proyecto: str,
    modo: str | None,
    metricas: Optional[Mapping[str, Any]],
    spec: Optional[Mapping[str, Any]],
    contexto_normalizado: Optional[Mapping[str, Any]],
) -> str:
    # Política:
    # - Si hay SPEC: estimar basado SOLO en SPEC + métricas derivadas del SPEC.
    # - Si NO hay SPEC (modo ASESOR): estimar basado SOLO en CONTEXTO_NORMALIZADO.
    if spec and isinstance(spec, Mapping):
        metricas_spec = _metricas_desde_spec(spec)
        metricas_block = _to_json_block("MÉTRICAS ESTRUCTURALES DERIVADAS DEL SPEC", metricas_spec)

        return f"""
{PROMPT_ESTIMACION_ESTRUCTURADA}

INSTRUCCIÓN OPERATIVA:
{_modo_a_instruccion(modo)}

{_to_json_block("SPEC (FUENTE DE VERDAD)", spec)}
{metricas_block}
DESCRIPCIÓN (solo apoyo si el SPEC está incompleto o vacío):
{descripcion_proyecto}
""".strip()

    # Fallback estructurado para ASESOR: contexto_normalizado
    return f"""
{PROMPT_ESTIMACION_ESTRUCTURADA}

INSTRUCCIÓN OPERATIVA:
{_modo_a_instruccion(modo)}

{_to_json_block("CONTEXTO_NORMALIZADO (FUENTE DE VERDAD)", contexto_normalizado)}
DESCRIPCIÓN (solo apoyo si el CONTEXTO_NORMALIZADO está incompleto o vacío):
{descripcion_proyecto}
""".strip()


def _estimar_horas_desde_inputs(
    *,
    descripcion_proyecto: str,
    modo: str | None,
    metricas: Optional[Mapping[str, Any]] = None,
    spec: Optional[Mapping[str, Any]] = None,
    contexto_normalizado: Optional[Mapping[str, Any]] = None,
) -> tuple[float, float, str]:
    """
    Estima horas con UNA llamada LLM.

    Política:
    - Si hay SPEC: basar estimación en SPEC (fuente de verdad) + métricas derivadas del SPEC.
    - Si NO hay SPEC (modo ASESOR): basar estimación en CONTEXTO_NORMALIZADO.
    """
    prompt_estimacion = _build_prompt_estimacion(
        descripcion_proyecto=descripcion_proyecto,
        modo=modo,
        metricas=None,
        spec=spec,
        contexto_normalizado=contexto_normalizado,
    )

    try:
        contenido_estimacion = chat_completion_json(
            prompt=prompt_estimacion,
            system="Responde únicamente con JSON válido.",
            temperature=0.0,
            max_tokens=PARAMETROS_ESTIMACION.max_tokens_estimacion,
            fase="estimacion",
        )
    except Exception:
        # La estimación es best-effort: no debe romper el pipeline si el alias/proveedor falla.
        contenido_estimacion = ""

    data = _parse_json_or_fallback(contenido_estimacion, _ESTIMACION_JSON_FALLBACK)

    try:
        junior = float(data.get("junior_horas", 0))
    except Exception:
        junior = PARAMETROS_ESTIMACION.fallback_junior_horas
    try:
        senior = float(data.get("senior_horas", 0))
    except Exception:
        senior = PARAMETROS_ESTIMACION.fallback_senior_horas

    complejidad = data.get("complejidad", "MEDIA")
    if not isinstance(complejidad, str):
        complejidad = PARAMETROS_ESTIMACION.fallback_complejidad

    return junior, senior, complejidad


def _sanitizar_horas_estimadas(*, junior: float, senior: float, num_integraciones: int) -> tuple[float, float]:
    if junior <= 0:
        junior = max(PARAMETROS_ESTIMACION.min_junior_horas, 2.5 + num_integraciones * 4.0)

    if senior <= 0:
        senior = max(PARAMETROS_ESTIMACION.min_senior_horas, 1.5 + num_integraciones * 3.0)

    return junior, senior


def _aplicar_clamps_por_complejidad(*, junior: float, senior: float, complejidad: str) -> tuple[float, float]:
    limite = PARAMETROS_ESTIMACION.limites_senior_por_complejidad.get(
        complejidad, PARAMETROS_ESTIMACION.limites_senior_por_complejidad["MEDIA"]
    )

    senior = min(senior, limite)
    junior = min(junior, limite * 2)
    return junior, senior


def _aplicar_ajustes_escenario_simple(
    *, junior: float, senior: float, requiere_persistencia: bool, requiere_auth: bool, num_integraciones: int
) -> tuple[float, float]:
    if not requiere_persistencia and not requiere_auth and num_integraciones <= 1:
        junior *= PARAMETROS_ESTIMACION.factor_escenario_simple
        senior *= PARAMETROS_ESTIMACION.factor_escenario_simple
    return junior, senior


def _calcular_rangos(*, junior: float, senior: float, generable: bool) -> tuple[float, float, float, float]:
    margen = PARAMETROS_ESTIMACION.margen_generable if generable else PARAMETROS_ESTIMACION.margen_no_generable
    return (
        junior * (1 - margen),
        junior * (1 + margen),
        senior * (1 - margen),
        senior * (1 + margen),
    )


def _aplicar_limites_y_margen(
    *,
    junior: float,
    senior: float,
    complejidad: str,
    requiere_persistencia: bool,
    requiere_auth: bool,
    num_integraciones: int,
    generable: bool,
) -> tuple[float, float, float, float]:
    junior, senior = _sanitizar_horas_estimadas(junior=junior, senior=senior, num_integraciones=num_integraciones)
    junior, senior = _aplicar_clamps_por_complejidad(junior=junior, senior=senior, complejidad=complejidad)
    junior, senior = _aplicar_ajustes_escenario_simple(
        junior=junior,
        senior=senior,
        requiere_persistencia=requiere_persistencia,
        requiere_auth=requiere_auth,
        num_integraciones=num_integraciones,
    )
    return _calcular_rangos(junior=junior, senior=senior, generable=generable)


def _calcular_ahorro(*, estimado: float, real: float) -> float:
    if estimado <= 0:
        return 0.0
    return min(
        PARAMETROS_ESTIMACION.max_ahorro_pct,
        max(0.0, (estimado - real) / estimado * 100),
    )


def calcular_estimacion_esfuerzo(
    descripcion_proyecto: str,
    modo: str | None,
    tiempo_real_scopeguardian_horas: float,
    generable: bool = True,
    *,
    spec: Optional[Mapping[str, Any]] = None,
    contexto_normalizado: Optional[Mapping[str, Any]] = None,
) -> EstimacionEsfuerzo:
    # Invariante del pipeline: en esta fase siempre hay input estructurado.
    # Se conserva `descripcion_proyecto` solo como apoyo por si faltan detalles finos.
    junior, senior, complejidad = _estimar_horas_desde_inputs(
        descripcion_proyecto=descripcion_proyecto,
        modo=modo,
        metricas=None,
        spec=spec,
        contexto_normalizado=contexto_normalizado,
    )

    # Guardrails sin heurísticas por keywords:
    # - Si hay SPEC: calibrar por tamaño del SPEC (endpoints/archivos/deps/env/contracts/restrictions).
    # - Si NO hay SPEC (ASESOR): no inventar métricas; dejar integraciones=0 y no aplicar ajustes extra.
    num_integraciones = 0
    requiere_auth = False
    requiere_persistencia = False

    degraded = False
    degrade_type = None
    spec_modo = None

    if spec and isinstance(spec, Mapping):
        spec_modo = spec.get("modo")
        m = _metricas_desde_spec(spec)
        num_endpoints = int(m.get("num_endpoints", 0) or 0)
        num_dependencies = int(m.get("num_dependencies", 0) or 0)
        num_env_vars = int(m.get("num_env_vars", 0) or 0)
        num_contract_rules = int(m.get("num_contract_rules", 0) or 0)

        # Nota importante:
        # - NO usamos dependencies/env como proxy directo de "integraciones externas".
        # - Aun así, lo mantenemos como una señal débil para clamps mínimos si el LLM devuelve 0.
        num_integraciones = max(0, num_dependencies + num_env_vars)

        # Ajuste adicional determinista: si hay muchos endpoints/reglas, sube ligeramente el senior/junior.
        senior += max(0.0, (num_endpoints - 3) * 0.75) + max(0.0, (num_contract_rules - 3) * 0.25)
        junior += max(0.0, (num_endpoints - 3) * 1.0) + max(0.0, (num_contract_rules - 3) * 0.35)

        # Señales opcionales (si existen en el SPEC): degradación de alcance real.
        pocit_meta = spec.get("pocit")
        if isinstance(pocit_meta, Mapping):
            degraded = bool(pocit_meta.get("degraded", False))
            degrade_type = pocit_meta.get("degrade_type")

    # Calibración pragmática por modo (scope realmente generado)
    modo_eff = (modo or spec_modo or "").upper()
    if modo_eff == "PARCIAL":
        junior *= PARAMETROS_ESTIMACION.factor_modo_parcial
        senior *= PARAMETROS_ESTIMACION.factor_modo_parcial

    if degraded and str(degrade_type).lower() == "contract-lite":
        junior *= PARAMETROS_ESTIMACION.factor_degrade_contract_lite
        senior *= PARAMETROS_ESTIMACION.factor_degrade_contract_lite

    logger.info(
        "[ESTIMACION] modo=%s spec_modo=%s degraded=%s degrade_type=%s junior_llm=%s senior_llm=%s",
        str(modo),
        str(spec_modo),
        str(degraded),
        str(degrade_type),
        str(junior),
        str(senior),
    )

    junior_min, junior_max, senior_min, senior_max = _aplicar_limites_y_margen(
        junior=junior,
        senior=senior,
        complejidad=complejidad,
        requiere_persistencia=requiere_persistencia,
        requiere_auth=requiere_auth,
        num_integraciones=num_integraciones,
        generable=generable,
    )

    ahorro_vs_junior = _calcular_ahorro(estimado=junior, real=tiempo_real_scopeguardian_horas)
    ahorro_vs_senior = _calcular_ahorro(estimado=senior, real=tiempo_real_scopeguardian_horas)

    return EstimacionEsfuerzo(
        junior_min=round(junior_min, 1),
        junior_max=round(junior_max, 1),
        senior_min=round(senior_min, 1),
        senior_max=round(senior_max, 1),
        horas_scopeguardian=round(tiempo_real_scopeguardian_horas, 2),
        ahorro_vs_junior=round(ahorro_vs_junior, 1),
        ahorro_vs_senior=round(ahorro_vs_senior, 1),
    )
