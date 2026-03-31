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
    # VERSIÓN ESTABLE — UN SOLO PASE SIN TECNOLOGÍAS HARDCODEADAS
    # ==========================================================
    prompt_estable = f"""
Eres un arquitecto software senior encargado de decidir si una PoC es viable o no desde un punto de vista arquitectónico profundo.

Tu objetivo NO es hacer un checklist básico.
Tu objetivo es detectar posibles puntos de fallo estructurales que podrían obligar a rediseñar la solución.

Analiza la siguiente PoC y genera EXACTAMENTE 3 riesgos arquitectónicos críticos.

Un riesgo arquitectónico crítico es aquel que:
- Puede invalidar el enfoque elegido.
- Puede requerir cambio de diseño.
- Puede implicar rediseño de autenticación, integración o despliegue.
- Puede afectar seguridad, aislamiento, modelo de permisos o arquitectura de ejecución.

Reglas estrictas:
- No repitas riesgos similares.
- No generes riesgos operativos triviales.
- No describas pasos de consola básicos.
- No generes documentación adicional.
- No incluyas YAML ni bloques extraños.
- Basa el análisis únicamente en la descripción proporcionada.
- Cada riesgo debe incluir una acción concreta de validación técnica real.
- Cada riesgo debe analizar el modo de fallo (failure mode).
- Cada riesgo debe indicar qué alternativa arquitectónica existiría si falla.

PoC:
{descripcion_global}

Usa EXACTAMENTE esta estructura:

1)
Riesgo arquitectónico:
Hipótesis arquitectónica que se está poniendo a prueba:
Por qué es crítico en esta PoC:
Modo de fallo técnico probable:
Acción de validación técnica:
Pasos concretos:
1.
2.
3.
Criterio de confirmación:
Criterio de invalidación:
Alternativa arquitectónica si falla:
Impacto en la decisión final:

2)
...

3)
...

No añadas texto fuera de los 3 bloques.
"""

    respuesta = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt_estable}],
        options={"temperature": 0.2, "num_predict": 1700},
    )

    texto = respuesta.get("message", {}).get("content", "").strip()

    texto = re.sub(r"```.*?```", "", texto, flags=re.DOTALL)

    bloques = re.split(r"\n(?=\d+\))", texto)

    opciones = [
        bloque.strip()
        for bloque in bloques
        if re.match(r"^\d+\)", bloque.strip())
    ]

    if not opciones and texto:
        return [texto]

    return opciones[:3]
