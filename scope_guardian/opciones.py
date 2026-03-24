"""
Módulo de generación de opciones adaptadas según fase del proyecto.

Responsabilidad:
Proponer acciones estratégicas alineadas con el nivel de madurez del usuario.
"""

import re
import ollama
from dataclasses import dataclass
from typing import List
from scope_guardian.fases import FaseProyecto


# ==========================================================
# MODELO DE RESTRICCIONES POR FASE
# ==========================================================

@dataclass(frozen=True)
class ConfiguracionFase:
    objetivo: str
    debe_incluir: List[str]
    debe_evitar: List[str]


def _configuracion_por_fase(fase: FaseProyecto) -> ConfiguracionFase:
    """
    Define de forma declarativa el comportamiento esperado por fase.
    Sin hardcodeos dispersos en el prompt.
    """

    #if fase == FaseProyecto.FASE_0:
    return ConfiguracionFase(
            objetivo=(
                "ARRANCAR la PoC desde cero con enfoque estratégico.\n\n"
                "IMPORTANTE:\n"
                "- Asume que NO existe ningún proyecto creado.\n"
                "- Asume que NO existe código base.\n"
                "- Ignora cualquier mención a validación de arquitectura existente.\n"
                "- Tu rol es iniciar el proyecto desde cero.\n"
            ),
            debe_incluir=[
                "estructura base del proyecto",
                "formalización de contrato REST",
                "especificación OpenAPI o Swagger",
                "documentación técnica inicial",
                "definición clara de responsabilidades por capa",
            ],
            debe_evitar=[
                "implementar método",
                "crear método",
                "refactor",
                "optimizar",
                "mejorar método existente",
                "lógica interna de servicio",
            ],
        )

    if fase == FaseProyecto.FASE_1:
        return ConfiguracionFase(
            objetivo="TRANSICIÓN de diseño conceptual a implementación.",
            debe_incluir=[
                "DTO formales",
                "validación explícita de contratos",
                "esqueleto base de implementación",
                "Swagger mockeado",
            ],
            debe_evitar=[
                "refactor profundo",
                "optimización avanzada",
            ],
        )

    return ConfiguracionFase(
        objetivo="MEJORAR implementación existente.",
        debe_incluir=[
            "refactor",
            "separación de capas",
            "centralización de errores",
            "reducción de deuda técnica",
        ],
        debe_evitar=[
            "bootstrap inicial",
            "creación de proyecto desde cero",
        ],
    )


# ==========================================================
# CONSTRUCCIÓN DEL PROMPT
# ==========================================================

def _construir_prompt(
    arquitectura: str | None,
    limites: str,
    configuracion: ConfiguracionFase,
) -> str:

    incluir = "\n".join(f"- {item}" for item in configuracion.debe_incluir)
    evitar = "\n".join(f"- {item}" for item in configuracion.debe_evitar)

    bloque_arquitectura = ""
    if arquitectura:
        bloque_arquitectura = f"""
Arquitectura actual:

{arquitectura}
"""

    return f"""
{bloque_arquitectura}

Límites:
{limites}

OBJETIVO ESTRATÉGICO:
{configuracion.objetivo}

LAS OPCIONES DEBEN INCLUIR CONCEPTOS RELACIONADOS CON:
{incluir}

LAS OPCIONES NO DEBEN CONTENER:
{evitar}

INSTRUCCIONES CRÍTICAS:

1. Genera EXACTAMENTE 3 opciones.
2. Cada opción debe comenzar con 1), 2), 3).
3. Deben seguir estrictamente esta plantilla:

1)
Clase afectada:
Cambio específico:
Impacto técnico:

4. PROHIBIDO generar bloques de código.
5. PROHIBIDO usar ``` o cualquier sintaxis de código.
6. NO escribas clases Java, métodos, anotaciones ni implementaciones.

Las opciones deben ser estratégicas y descriptivas, NO técnicas en forma de código.

No añadas texto adicional fuera de las 3 opciones.
No expliques razonamiento.
No incluyas comentarios.
"""


# ==========================================================
# GENERACIÓN
# ==========================================================

def _generar_opciones_raw(
    arquitectura: str,
    limites: str,
    fase: FaseProyecto,
) -> list[str]:

    configuracion = _configuracion_por_fase(fase)

    # En FASE_0 no pasamos arquitectura para evitar sesgo hacia micro‑implementación
    arquitectura_para_prompt = None
    if fase != FaseProyecto.FASE_0:
        arquitectura_para_prompt = arquitectura

    prompt = _construir_prompt(
        arquitectura=arquitectura_para_prompt,
        limites=limites,
        configuracion=configuracion,
    )

    respuesta = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt}],
        options={
            "temperature": 0.1,
            "num_predict": 350,
        },
    )

    texto = respuesta.get("message", {}).get("content", "").strip()

    # Eliminación defensiva de bloques de código si el modelo desobedece
    texto = re.sub(r"```.*?```", "", texto, flags=re.DOTALL)

    bloques = re.split(r"\n(?=\d+\))", texto)

    opciones = [
        bloque.strip()
        for bloque in bloques
        if re.match(r"^\d+\)", bloque.strip())
    ]

    return opciones[:3]


def generar_opciones(
    arquitectura: str,
    limites: str,
    tecnologias: str,
    fase: FaseProyecto,
) -> list[str]:
    """
    Genera opciones estratégicamente alineadas con la fase.
    """
    return _generar_opciones_raw(
        arquitectura=arquitectura,
        limites=limites,
        fase=fase,
    )
