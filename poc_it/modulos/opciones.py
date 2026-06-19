"""
ScopeGuardian - Generador de Opciones Estratégicas

Nuevo enfoque:

Las opciones SOLO se generan cuando:
- La PoC NO puede generarse automáticamente.
- Existe incompatibilidad tecnológica o alcance fuera de FastAPI + Python.

Por tanto:
- Se elimina la lógica por fases.
- Se prioriza valor estratégico.
- Se generan 3 riesgos técnicos + 3 acciones concretas.
"""

import re
from typing import List
from poc_it.infraestructura.llm_client import chat_completion_text


# ==========================================================
# CONSTRUCCIÓN DEL PROMPT ESTRATÉGICO
# ==========================================================

def _construir_prompt_estrategico(
    descripcion_global: str,
) -> str:
    return f"""
Eres un arquitecto software senior especializado en validación técnica de PoCs complejas en cualquier dominio tecnológico.

La siguiente PoC NO puede generarse automáticamente debido a incompatibilidad tecnológica o alcance fuera del dominio soportado.

Tu misión es actuar como asesor técnico estratégico y ayudar a decidir si la PoC es viable antes de implementarla.

Descripción de la PoC:

{descripcion_global}

INSTRUCCIONES OBLIGATORIAS (NO OMITIR NINGUNA):

1. Identifica exactamente 3 RIESGOS TÉCNICOS CRÍTICOS que puedan comprometer la viabilidad real.
2. Cada riesgo debe incluir obligatoriamente: acción concreta para resolverlo o mitigarlo.
3. Genera únicamente riesgos que sean realmente distintos entre sí (no reformulaciones).
4. Los riesgos deben ser profundos (no triviales ni administrativos).
5. Cada riesgo debe desarrollar razonamiento estructurado y accionable.
6. Cada bloque debe tener suficiente detalle técnico (mínimo 150 palabras por riesgo).
7. Prohibido responder de forma genérica.
8. Prohibido proponer tareas superficiales como “crear proyecto base” o “documentar”.
9. No usar bloques ```.
10. No generar código extenso, pero sí puedes mencionar configuraciones técnicas concretas si aportan claridad.
11. Prioriza los riesgos por impacto real en la decisión de continuar o no con la PoC.
12. Si no puedes desarrollar correctamente la estructura completa, no generes riesgos adicionales.

FORMATO OBLIGATORIO:

Desarrolla EXACTAMENTE 3 riesgos usando la siguiente estructura completa.

1)
Riesgo técnico:
Por qué es crítico en esta PoC:
Acción de validación:
Pasos concretos:
1.
2.
3.
Criterio de confirmación (qué resultado confirma viabilidad):
Criterio de invalidación (qué resultado demuestra que el enfoque no es viable):
Impacto en la decisión final:

2)
...

3)
...

- Si existen riesgos adicionales relevantes (más allá de los 3 principales), inclúyelos en una sección final titulada:

Otros riesgos detectados:

En esta sección adicional:
- Lista los riesgos de forma breve (sin repetir toda la plantilla estructurada).
- No repitas riesgos ya desarrollados.
- No inventes riesgos triviales.
- Mantén máximo 5 riesgos adicionales.

No añadas texto fuera de estas secciones.
"""


# ==========================================================
# GENERACIÓN
# ==========================================================

def generar_opciones(
    arquitectura: str,
    limites: str,
    tecnologias: str,
    fase=None,
) -> List[str]:
    """
    Genera 3 riesgos técnicos + 3 acciones concretas.

    La lógica por fases queda eliminada en el nuevo flujo.
    """

    descripcion_global = f"""
Arquitectura declarada:
{arquitectura}

Restricciones:
{limites}

Tecnologías:
{tecnologias}
"""

    # ==========================================================
    # ASESOR ARQUITECTÓNICO AVANZADO + QUALITY GATE
    # ==========================================================
    prompt_estable = f"""
Eres un Principal Engineer especializado en evaluación estructural de sistemas distribuidos y PoCs complejas en entornos cloud.

Tu misión es generar un análisis arquitectónico profundo que aporte el máximo valor técnico posible al usuario, aunque la PoC no pueda generarse automáticamente.

No te limites a identificar riesgos.

Debes generar un documento estructurado con las siguientes secciones:

1. Resumen Ejecutivo
2. Supuestos Arquitectónicos Implícitos
3. Riesgos Estructurales Críticos
4. Validaciones Técnicas Recomendadas
5. Estrategia de Implementación Recomendada
6. Decisión Arquitectónica Sugerida

REGLAS ESTRICTAS:

- No inventes componentes no mencionados en la descripción.
- No generes riesgos triviales.
- No repitas conceptos.
- Basa el análisis exclusivamente en la descripción proporcionada.
- Evalúa explícitamente:
  - Identidad y autorización en entorno de ejecución
  - Dependencia de red y egress
  - Límites estructurales de plataforma (timeouts, escalado, concurrencia)
  - Acoplamientos fuertes
  - Idempotencia
  - Impacto de fallos parciales

PoC:
{descripcion_global}

Genera el análisis completo como documento profesional.
No añadas texto fuera del análisis.
"""

    texto = chat_completion_text(
        prompt=prompt_estable,
        system=None,
        temperature=0.2,
        max_tokens=3000,
        fase="documentacion",
    ).strip()

    texto = re.sub(r"```.*?```", "", texto, flags=re.DOTALL)

    # =========================
    # QUALITY GATE ESTRUCTURAL
    # =========================
    def quality_gate_fail(analysis: str) -> bool:
        analysis_lower = analysis.lower()
        descripcion_lower = descripcion_global.lower()

        # Verificar secciones obligatorias
        required_sections = [
            "resumen ejecutivo",
            "supuestos arquitectónicos",
            "riesgos estructurales",
            "validaciones técnicas",
            "estrategia de implementación",
            "decisión arquitectónica"
        ]

        for section in required_sections:
            if section not in analysis_lower:
                return True

        return False

    if quality_gate_fail(texto):
        texto = chat_completion_text(
            prompt=prompt_estable
            + "\n\nEl análisis anterior no cumple los requisitos estructurales. "
              "Reformula con mayor profundidad, sin inventar componentes y respetando todas las secciones obligatorias.",
            system=None,
            temperature=0.1,
            max_tokens=3000,
            fase="documentacion",
        ).strip()

        texto = re.sub(r"```.*?```", "", texto, flags=re.DOTALL)

    return [texto]
