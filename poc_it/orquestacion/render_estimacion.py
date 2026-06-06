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

    # Mostrar siempre PoC-it en minutos para que sea más legible (aunque sean 2h -> 120 min).
    total_segundos = max(0, int(horas * 3600))
    minutos = total_segundos // 60
    segundos = total_segundos % 60

    if minutos >= 60:
        # Para duraciones largas, mantener minutos absolutos (requisito: “en minutos”),
        # pero evitando ruido de segundos si no aportan.
        tiempo_pocit = f"{minutos} min (medido)"
    else:
        tiempo_pocit = f"{minutos} min {segundos} s (medido)"

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
