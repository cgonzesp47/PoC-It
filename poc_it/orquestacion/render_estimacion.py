from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from poc_it.estimador_esfuerzo import EstimacionEsfuerzo


def generar_bloque_estimacion_markdown(estimacion: "EstimacionEsfuerzo") -> str:
    """Renderiza el bloque de estimación en Markdown.

    Se separa de `estimador_esfuerzo.py` para mantener capas:
    - Dominio: cálculo de estimación (estimador_esfuerzo)
    - Presentación: render markdown (este módulo)
    """
    horas = estimacion.horas_scopeguardian

    # PoC-it es tiempo MEDIDO, así que lo mostramos como duración real (min/seg).
    # Para poder compararlo contra Junior/Senior (en horas), añadimos también la equivalencia en horas.
    total_segundos = max(0, int(float(horas) * 3600))
    minutos = total_segundos // 60
    segundos = total_segundos % 60

    # Formato humano estable:
    # - si dura >= 1h, mostramos Hh Mm (sin segundos para no meter ruido)
    # - si dura < 1h, mostramos Mm Ss
    if minutos >= 60:
        hh = minutos // 60
        mm = minutos % 60
        duracion = f"{hh} h {mm} min"
    else:
        duracion = f"{minutos} min {segundos} s"

    horas_pocit = max(0.0, float(horas))
    horas_pocit = round(horas_pocit, 2)

    tiempo_pocit = f"{duracion} ({horas_pocit} h, medido)"

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
