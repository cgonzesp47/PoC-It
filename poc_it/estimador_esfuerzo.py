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
from typing import Any, Mapping, Optional

from poc_it.llm_client import chat_completion_json


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

PROMPT_EXTRACCION_METRICAS = """
Actúa como un arquitecto backend.

Analiza la descripción de la PoC y extrae SOLO métricas estructurales objetivas.

Devuelve ÚNICAMENTE JSON válido con esta estructura:

{
  "num_endpoints": number,
  "num_integraciones_externas": number,
  "requiere_autenticacion_compleja": boolean,
  "requiere_persistencia": boolean,
  "requiere_despliegue_cloud": boolean,
  "complejidad_global": "BAJA | MEDIA | ALTA | CRITICA"
}

No añadas texto fuera del JSON.
"""

PROMPT_ESTIMACION_ESTRUCTURADA = """
Actúa como un arquitecto software senior pragmático.

Debes estimar el tiempo TOTAL necesario para implementar una **PoC mínima funcional (MVP)**.

Fuente de verdad:
- Si se proporciona un SPEC o un CONTEXTO_NORMALIZADO, debes basarte principalmente en esos datos estructurados.
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

_METRICAS_FALLBACK: dict[str, Any] = {
    "num_endpoints": 1,
    "num_integraciones_externas": 1,
    "requiere_autenticacion_compleja": False,
    "requiere_persistencia": False,
    "requiere_despliegue_cloud": False,
    "complejidad_global": PARAMETROS_ESTIMACION.fallback_complejidad,
}

_ESTIMACION_JSON_FALLBACK: dict[str, Any] = {
    "junior_horas": PARAMETROS_ESTIMACION.fallback_junior_horas,
    "senior_horas": PARAMETROS_ESTIMACION.fallback_senior_horas,
    "complejidad": PARAMETROS_ESTIMACION.fallback_complejidad,
}


def _parse_json_or_fallback(raw: Any, fallback: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return dict(fallback)
    try:
        parsed = json.loads(raw)
    except Exception:
        return dict(fallback)
    return parsed if isinstance(parsed, dict) else dict(fallback)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalizar_metricas(metricas: Mapping[str, Any]) -> dict[str, Any]:
    num_endpoints = _safe_int(metricas.get("num_endpoints"))
    num_integraciones = _safe_int(metricas.get("num_integraciones_externas"))

    requiere_auth = bool(metricas.get("requiere_autenticacion_compleja", False))
    requiere_persistencia = bool(metricas.get("requiere_persistencia", False))
    requiere_cloud = bool(metricas.get("requiere_despliegue_cloud", False))

    complejidad_global = metricas.get("complejidad_global")
    if not isinstance(complejidad_global, str):
        complejidad_global = "MEDIA"

    return {
        "num_endpoints": num_endpoints,
        "num_integraciones_externas": num_integraciones,
        "requiere_autenticacion_compleja": requiere_auth,
        "requiere_persistencia": requiere_persistencia,
        "requiere_despliegue_cloud": requiere_cloud,
        "complejidad_global": complejidad_global,
    }


def _extraer_metricas(descripcion_proyecto: str) -> dict[str, Any]:
    """
    Fallback legacy: extracción de métricas desde texto libre mediante LLM.
    Se mantiene para compatibilidad cuando no haya inputs estructurados.
    """
    prompt_metricas = f"""
{PROMPT_EXTRACCION_METRICAS}

Descripción técnica de la PoC:

{descripcion_proyecto}
"""

    contenido_metricas = chat_completion_json(
        prompt=prompt_metricas,
        system="Responde únicamente con JSON válido.",
        temperature=0.0,
        max_tokens=300,
        fase="estimacion",
    )

    raw_metricas = _parse_json_or_fallback(contenido_metricas, _METRICAS_FALLBACK)
    return _normalizar_metricas(raw_metricas)


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
            "Modo: PARCIAL. Puede haber alcance parcial. Estima el esfuerzo humano para implementar el SPEC "
            "y completar los elementos típicamente necesarios para una PoC funcional (sin suposiciones enterprise)."
        )
    if modo_upper == "COMPLETO":
        return (
            "Modo: COMPLETO. Se pretende implementar el SPEC completo como PoC funcional. Estima el esfuerzo humano "
            "para construir lo definido en el SPEC."
        )

    return f"Modo: {modo_upper}. Estima en base al alcance descrito/estructurado."


def _estimar_horas_desde_inputs(
    *,
    descripcion_proyecto: str,
    modo: str | None,
    metricas: Optional[Mapping[str, Any]] = None,
    spec: Optional[Mapping[str, Any]] = None,
    contexto_normalizado: Optional[Mapping[str, Any]] = None,
) -> tuple[float, float, str]:
    """
    Estima horas con UNA llamada LLM, usando inputs estructurados cuando existan.
    - Si hay spec/contexto_normalizado, se priorizan como fuente de verdad.
    - `metricas` se usa como fallback estructurado si se aporta.
    """
    def _safe_json(obj: Optional[Mapping[str, Any]]) -> str:
        if not obj:
            return ""
        try:
            return json.dumps(obj, ensure_ascii=False)
        except Exception:
            return str(obj)

    spec_json = _safe_json(spec)
    contexto_json = _safe_json(contexto_normalizado)

    metricas_block = ""
    if metricas:
        metricas_block = f"""
MÉTRICAS ESTRUCTURALES (si están presentes, úsalas como resumen):
{_safe_json(metricas)}
"""

    prompt_estimacion = f"""
{PROMPT_ESTIMACION_ESTRUCTURADA}

INSTRUCCIÓN OPERATIVA:
{_modo_a_instruccion(modo)}

SPEC (FUENTE DE VERDAD, si está presente):
{spec_json or "(no disponible)"}

CONTEXTO_NORMALIZADO (FUENTE DE VERDAD, si está presente):
{contexto_json or "(no disponible)"}
{metricas_block}
DESCRIPCIÓN (solo apoyo si falta detalle en spec/contexto):
{descripcion_proyecto}
"""

    contenido_estimacion = chat_completion_json(
        prompt=prompt_estimacion,
        system="Responde únicamente con JSON válido.",
        temperature=0.0,
        max_tokens=PARAMETROS_ESTIMACION.max_tokens_estimacion,
        fase="estimacion",
    )

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
    if junior <= 0:
        junior = max(PARAMETROS_ESTIMACION.min_junior_horas, 2.5 + num_integraciones * 4.0)

    if senior <= 0:
        senior = max(PARAMETROS_ESTIMACION.min_senior_horas, 1.5 + num_integraciones * 3.0)

    limite = PARAMETROS_ESTIMACION.limites_senior_por_complejidad.get(
        complejidad, PARAMETROS_ESTIMACION.limites_senior_por_complejidad["MEDIA"]
    )

    senior = min(senior, limite)
    junior = min(junior, limite * 2)

    if not requiere_persistencia and not requiere_auth and num_integraciones <= 1:
        senior *= 0.9
        junior *= 0.9

    margen = PARAMETROS_ESTIMACION.margen_generable if generable else PARAMETROS_ESTIMACION.margen_no_generable

    return (
        junior * (1 - margen),
        junior * (1 + margen),
        senior * (1 - margen),
        senior * (1 + margen),
    )


def _calcular_ahorro(*, estimado: float, real: float) -> float:
    if estimado <= 0:
        return 0.0
    return min(
        PARAMETROS_ESTIMACION.max_ahorro_pct,
        max(0.0, (estimado - real) / estimado * 100),
    )


def calcular_estimacion_llm(
    descripcion_proyecto: str,
    modo: str | None,
    tiempo_real_scopeguardian_horas: float,
    generable: bool = True,
    *,
    spec: Optional[Mapping[str, Any]] = None,
    contexto_normalizado: Optional[Mapping[str, Any]] = None,
) -> EstimacionEsfuerzo:
    # Si tenemos inputs estructurados, evitamos la llamada extra del “paso 1” (extracción de métricas).
    metricas: Optional[dict[str, Any]]
    if spec or contexto_normalizado:
        metricas = None
    else:
        metricas = _extraer_metricas(descripcion_proyecto)

    junior, senior, complejidad = _estimar_horas_desde_inputs(
        descripcion_proyecto=descripcion_proyecto,
        modo=modo,
        metricas=metricas,
        spec=spec,
        contexto_normalizado=contexto_normalizado,
    )

    # Para aplicar guardrails, si no había métricas las derivamos con fallback (sin LLM).
    if metricas is None:
        # fallback conservador: sin datos estructurales finos, asumimos al menos 1 endpoint y 1 integración
        metricas = _METRICAS_FALLBACK

    num_integraciones = int(metricas["num_integraciones_externas"])
    requiere_auth = bool(metricas["requiere_autenticacion_compleja"])
    requiere_persistencia = bool(metricas["requiere_persistencia"])

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


# ==========================================================
# MARKDOWN
# ==========================================================

def generar_bloque_markdown(estimacion: EstimacionEsfuerzo) -> str:
    # ------------------------------------------------------
    # Formateo inteligente del tiempo de PoC-it
    # ------------------------------------------------------
    horas = estimacion.horas_scopeguardian

    if horas < 1:
        total_segundos = int(horas * 3600)
        minutos = total_segundos // 60
        segundos = total_segundos % 60
        tiempo_pocit = f"{minutos} min {segundos} s (medido)"
    else:
        tiempo_pocit = f"{horas} horas (medido)"

    return f"""
## Estimación comparativa de esfuerzo

| Perfil | Tiempo estimado |
|--------|-----------------|
| Junior | {estimacion.junior_min} – {estimacion.junior_max} horas |
| Senior | {estimacion.senior_min} – {estimacion.senior_max} horas |
| PoC-it | {tiempo_pocit} |

### Ahorro estimado

- Reducción frente a Junior: {estimacion.ahorro_vs_junior}%  
- Reducción frente a Senior: {estimacion.ahorro_vs_senior}%  
""".strip()
