"""
Módulo de generación y validación de implementaciones parciales.
Responsabilidad única: proponer y validar caminos de avance de la PoC.
"""

import re
import ollama


PROMPT_OPCIONES = """
A partir de la arquitectura anterior:

{arquitectura}

Límites:
{limites}

INSTRUCCIONES IMPORTANTES:

- NO generes checklist genéricos.
- NO describas pasos típicos de desarrollo.
- NO propongas tareas obvias que ya estén implícitas en la arquitectura.
- Cada opción debe aportar VALOR REAL al usuario.

Las opciones deben cumplir al menos uno de estos objetivos:

- Detectar ambigüedades en el contrato REST (por ejemplo, campos poco definidos).
- Detectar posibles inconsistencias entre endpoints relacionados.
- Mejorar la separación de responsabilidades (Controller / Service / Repository).
- Proponer una mejora estructural que facilite futuras integraciones reales.
- Centralizar o mejorar el manejo de errores.
- Ofrecer generación de documentación Swagger mockeada.
- Ofrecer definir formalmente esquemas JSON si puede haber dudas.
- Reducir incertidumbre técnica para QA o arquitectos.

Cada opción debe ser una IMPLEMENTACIÓN PARCIAL CONCRETA que el sistema pueda realizar sin romper los límites.
Evita propuestas que hagan la PoC no automatizable.
Mantén almacenamiento exclusivamente en memoria.
No introduzcas nuevas tecnologías.
Una de las opciones puede ser una aclaración técnica estratégica si detectas ambigüedad relevante.

Genera EXACTAMENTE 3 opciones usando ESTA plantilla obligatoria:

1)
Clase afectada:
Cambio específico:
Impacto técnico:

2)
Clase afectada:
Cambio específico:
Impacto técnico:

3)
Clase afectada:
Cambio específico:
Impacto técnico:
"""


def _resumir_arquitectura(arquitectura: str) -> str:
    """
    Reduce la arquitectura eliminando bloques extensos y
    limitando tamaño para evitar saturación del modelo.
    """
    lineas = arquitectura.split("\n")
    resumen: list[str] = []

    for linea in lineas:
        if len(linea.strip()) > 200:
            continue
        resumen.append(linea)

    return "\n".join(resumen[:80])


def _generar_opciones_raw(arquitectura: str, limites: str) -> list[str]:
    prompt = PROMPT_OPCIONES.format(
        arquitectura=arquitectura,
        limites=limites,
    )

    respuesta = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt}],
        options={
            "temperature": 0.1,
            "num_predict": 250,
        },
    )

    texto = respuesta.get("message", {}).get("content", "").strip()

    # Dividir en bloques estructurados por numeración
    bloques = re.split(r"\n(?=\d+\))", texto)

    opciones = []
    for bloque in bloques:
        bloque = bloque.strip()
        if re.match(r"^\d+\)", bloque):
            opciones.append(bloque)

    # Si no detecta bloques correctamente, fallback simple
    if not opciones:
        lineas = [l.strip() for l in texto.split("\n") if l.strip()]
        candidatas = [l for l in lineas if len(l) > 15]
        opciones = [
            f"{i+1}) {candidatas[i]}"
            for i in range(min(3, len(candidatas)))
        ]

    return opciones[:3]


def _validar_opcion(
    opcion: str,
    arquitectura: str,
    limites: str,
    tecnologias: str,
) -> bool:
    prompt_validacion = f"""
Arquitectura:
{arquitectura}

Tecnologías:
{tecnologias}

Límites:
{limites}

Opción:
{opcion}

¿La opción respeta los límites, es coherente con la arquitectura
y no introduce tecnologías nuevas?

Responde SOLO:
A) VALIDA
B) INVALIDA
"""

    respuesta = ollama.chat(
        model="qwen7b:latest",
        messages=[{"role": "user", "content": prompt_validacion}],
        options={
            "temperature": 0.0,
            "num_predict": 3,
            "stop": ["\n"],
        },
    )

    texto = respuesta.get("message", {}).get("content", "").strip().upper()
    return texto.startswith("A")


def generar_opciones(
    arquitectura: str,
    limites: str,
    tecnologias: str,
) -> list[str]:
    arquitectura_resumida = _resumir_arquitectura(arquitectura)
    opciones_raw = _generar_opciones_raw(arquitectura_resumida, limites)

    # Si el modelo devuelve menos de 3 opciones, reintentar una vez
    if len(opciones_raw) < 3:
        opciones_raw = _generar_opciones_raw(arquitectura_resumida, limites)

    opciones_validas: list[str] = []

    for opcion in opciones_raw:
        if _validar_opcion(
            opcion=opcion,
            arquitectura=arquitectura,
            limites=limites,
            tecnologias=tecnologias,
        ):
            opciones_validas.append(opcion)

    if not opciones_validas:
        return opciones_raw

    # Asegurar máximo 3 opciones
    return opciones_validas[:3]
