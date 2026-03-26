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
import ollama
from typing import List


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
2. Los riesgos deben ser profundos (no triviales ni administrativos).
3. Para cada riesgo debes desarrollar razonamiento estructurado y accionable.
4. Cada riesgo debe incluir obligatoriamente TODAS las secciones siguientes.
5. Cada bloque debe tener suficiente detalle técnico (mínimo 120 palabras por riesgo).
6. Prohibido responder de forma genérica.
7. Prohibido proponer tareas superficiales como “crear proyecto base” o “documentar”.
8. No usar bloques ```.
9. No generar código extenso, pero sí puedes mencionar comandos, endpoints o configuraciones concretas si aportan claridad.
10. Prioriza los riesgos por impacto real en la decisión de continuar o no con la PoC.

FORMATO OBLIGATORIO (RESPETA EXACTAMENTE ESTA ESTRUCTURA):

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

3)
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

No añadas texto adicional fuera de los 3 bloques.
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

    prompt = _construir_prompt_estrategico(descripcion_global)

    respuesta = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt}],
        options={
            "temperature": 0.2,
            "num_predict": 1200,
        },
    )

    texto = respuesta.get("message", {}).get("content", "").strip()

    # Limpieza defensiva
    texto = re.sub(r"```.*?```", "", texto, flags=re.DOTALL)

    bloques = re.split(r"\n(?=\d+\))", texto)

    opciones = [
        bloque.strip()
        for bloque in bloques
        if re.match(r"^\d+\)", bloque.strip())
    ]

    return opciones[:3]
