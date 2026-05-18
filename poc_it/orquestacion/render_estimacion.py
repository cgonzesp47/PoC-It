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
