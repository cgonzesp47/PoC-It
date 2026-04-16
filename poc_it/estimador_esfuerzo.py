"""
ScopeGuardian - Estimador de Esfuerzo basado en LLM

Nueva estrategia:
- El LLM infiere el esfuerzo humano (Junior / Senior).
- Se solicita salida estructurada en JSON.
- Se mantiene control limitando rango y ahorro máximo.
- Se usa tiempo real medido para ScopeGuardian.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
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

Debes estimar el tiempo TOTAL necesario para implementar una **PoC mínima funcional (MVP)** basada únicamente en las métricas estructurales proporcionadas.

Principios obligatorios:
- Asume implementación directa, sin burocracia corporativa.
- No asumas configuración avanzada de red, VPC, IAM granular ni hardening.
- Cloud Run básico con despliegue estándar NO debe considerarse arquitectura compleja.
- ADC con Service Account estándar NO debe considerarse autenticación compleja.
- No incluyas optimizaciones enterprise ni arquitectura futura.

Referencia orientativa realista (Senior):
- 1 endpoint simple: 2–3h
- Integración externa simple (SDK oficial): 3–6h
- Deploy cloud básico: 1–3h
- CRUD con base de datos simple: 6–10h

Si no hay persistencia ni autenticación compleja, el total Senior raramente debería superar 16–20h.

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
# FUNCIÓN PRINCIPAL
# ==========================================================

def calcular_estimacion_llm(
    descripcion_proyecto: str,
    modo: str | None,
    tiempo_real_scopeguardian_horas: float,
    generable: bool = True,
) -> EstimacionEsfuerzo:

    # ======================================================
    # PASO 1: EXTRACCIÓN DE MÉTRICAS ESTRUCTURALES (LLM)
    # ======================================================

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
    )

    try:
        metricas = json.loads(contenido_metricas)
    except Exception:
        metricas = {
            "num_endpoints": 1,
            "num_integraciones_externas": 1,
            "requiere_autenticacion_compleja": False,
            "requiere_persistencia": False,
            "requiere_despliegue_cloud": False,
            "complejidad_global": "MEDIA",
        }

    # ======================================================
    # PASO 2: ESTIMACIÓN BASADA EN MÉTRICAS (LLM)
    # ======================================================

    # NORMALIZACIÓN EXPLÍCITA DE MÉTRICAS (VERSIÓN ELEGANTE)

    def _safe_int(value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    num_endpoints = _safe_int(metricas.get("num_endpoints"))
    num_integraciones = _safe_int(metricas.get("num_integraciones_externas"))

    requiere_auth = bool(metricas.get("requiere_autenticacion_compleja", False))
    requiere_persistencia = bool(metricas.get("requiere_persistencia", False))
    requiere_cloud = bool(metricas.get("requiere_despliegue_cloud", False))

    complejidad_global = metricas.get("complejidad_global")
    if not isinstance(complejidad_global, str):
        complejidad_global = "MEDIA"

    prompt_estimacion = f"""
{PROMPT_ESTIMACION_ESTRUCTURADA}

Métricas estructurales detectadas:
- Endpoints: {num_endpoints}
- Integraciones externas: {num_integraciones}
- Autenticación compleja: {requiere_auth}
- Persistencia: {requiere_persistencia}
- Despliegue cloud: {requiere_cloud}
- Complejidad global: {complejidad_global}
"""

    contenido_estimacion = chat_completion_json(
        prompt=prompt_estimacion,
        system="Responde únicamente con JSON válido.",
        temperature=0.0,
        max_tokens=400,
    )

    try:
        data = json.loads(contenido_estimacion)
        junior = float(data.get("junior_horas", 0))
        senior = float(data.get("senior_horas", 0))
        complejidad = data.get("complejidad", "MEDIA")
    except Exception:
        junior = 24.0
        senior = 12.0
        complejidad = "MEDIA"

    # ------------------------------------------------------
    # VALIDACIÓN POST-LLM: evitar estimaciones irreales (0h)
    # ------------------------------------------------------

    if junior <= 0:
        # fallback razonable pero manteniendo prioridad del LLM
        junior = max(8.0, num_endpoints * 2.5 + num_integraciones * 4.0)

    if senior <= 0:
        senior = max(4.0, num_endpoints * 1.5 + num_integraciones * 3.0)

    # ======================================================
    # NORMALIZACIÓN Y LÍMITES RAZONABLES (AJUSTE FINO)
    # ======================================================

    limites_senior = {
        "BAJA": 14,
        "MEDIA": 32,
        "ALTA": 70,
        "CRITICA": 140,
    }

    limite = limites_senior.get(complejidad, 32)

    senior = min(senior, limite)
    junior = min(junior, limite * 2)

    # Ajuste adicional para escenarios simples sin persistencia ni auth compleja
    if (
        not requiere_persistencia
        and not requiere_auth
        and num_integraciones <= 1
    ):
        senior *= 0.9
        junior *= 0.9

    # Margen mayor si no es generable (más incertidumbre)
    margen = 0.15 if generable else 0.25

    junior_min = junior * (1 - margen)
    junior_max = junior * (1 + margen)
    senior_min = senior * (1 - margen)
    senior_max = senior * (1 + margen)

    # ======================================================
    # PROTECCIÓN CONTRA DIVISIÓN POR CERO
    # ======================================================

    if junior > 0:
        ahorro_vs_junior = min(
            90.0,
            max(0.0, (junior - tiempo_real_scopeguardian_horas) / junior * 100),
        )
    else:
        ahorro_vs_junior = 0.0

    if senior > 0:
        ahorro_vs_senior = min(
            90.0,
            max(0.0, (senior - tiempo_real_scopeguardian_horas) / senior * 100),
        )
    else:
        ahorro_vs_senior = 0.0

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
