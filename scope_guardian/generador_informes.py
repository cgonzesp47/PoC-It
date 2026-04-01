"""
ScopeGuardian - Generador dinámico de informes (README)

Genera:
- README_FINAL (siempre)
- README_MANUAL (solo en modo PARCIAL)

Diseñado para modelos locales pequeños (qwen7b).
Prompts concisos, estructurados y orientados a valor.
"""

from __future__ import annotations

import ollama
from typing import List
from scope_guardian.estimador_esfuerzo import (
    calcular_estimacion_llm,
    generar_bloque_markdown,
)


MODEL = "qwen7b:latest"


def _llamar_modelo(prompt: str, max_tokens: int = 1800) -> str:
    response = ollama.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.2, "num_predict": max_tokens},
    )
    return response.get("message", {}).get("content", "").strip()


def generar_readme_final(
    nombre: str,
    descripcion_global: str,
    arquitectura: str,
    endpoints_generados: List[str],
    modo: str,
    tecnologias: str,
    tiempo_real_scopeguardian_horas: float,
) -> str:
    """
    Genera README_FINAL dinámico.
    """

    endpoints_str = "\n".join(f"- {e}" for e in endpoints_generados) or "- (No detectados)"

    prompt = f"""
Genera un README_FINAL conciso, ejecutivo + técnico.

IMPORTANTE:
- Si el modo es PARCIAL, deja claro que las integraciones externas NO están implementadas.
- Usa expresiones como "estructura preparada para" o "base lista para integrar".
- No afirmes que una integración funciona realmente si no está implementada.

Proyecto: {nombre}
Modo de generación: {modo}
Tecnologías declaradas: {tecnologias}

Descripción de la PoC:
{descripcion_global}

Arquitectura inferida:
{arquitectura}

Endpoints generados:
{endpoints_str}

Estructura obligatoria (breve y funcional):

# {nombre}

## 1. Resumen
- Qué problema resuelve
- Tipo de generación (Completa / Parcial)

## 2. Arquitectura generada
- Entidades
- Endpoints
- Capas

## 3. Alcance implementado
- Qué funciona ahora
- Qué es simulado o simplificado

## 4. Cómo ejecutar
- Instalación
- Comando uvicorn
- Acceso a /docs

## 5. Limitaciones actuales
- Simplificaciones
- No producción

No escribas texto innecesario.
No expliques el modelo.
No incluyas comentarios meta.
"""

    contenido = _llamar_modelo(prompt)

    # 🔎 Eliminación de repeticiones accidentales del modelo
    marcador = f"# {nombre}"
    partes = contenido.split(marcador)
    if len(partes) > 2:
        contenido = marcador + partes[1]

    contenido = contenido.strip()

    # ======================================================
    # ESTIMACIÓN BASADA EN LLM
    # ======================================================

    descripcion_estimacion = f"""
Proyecto: {nombre}
Modo: {modo}

Descripción:
{descripcion_global}

Arquitectura:
{arquitectura}

Endpoints generados:
{endpoints_str}
"""

    # ======================================================
    # ESTIMACIÓN A) PoC REAL GENERADA
    # ======================================================

    estimacion_generada = calcular_estimacion_llm(
        descripcion_proyecto=descripcion_estimacion,
        modo=modo,
        tiempo_real_scopeguardian_horas=tiempo_real_scopeguardian_horas,
    )

    bloque_generada = generar_bloque_markdown(estimacion_generada)

    # ======================================================
    # ESTIMACIÓN B) PoC COMPLETA SOLICITADA POR EL USUARIO
    # ======================================================

    descripcion_completa = f"""
PoC completa solicitada por el usuario (sin simplificaciones):

{descripcion_global}
"""

    estimacion_completa = calcular_estimacion_llm(
        descripcion_proyecto=descripcion_completa,
        modo="COMPLETA_SOLICITADA",
        tiempo_real_scopeguardian_horas=tiempo_real_scopeguardian_horas,
    )

    bloque_completa = generar_bloque_markdown(estimacion_completa)

    nota = """
> Nota: El ahorro real puede ser superior al porcentaje mostrado.  
> El porcentaje se limita deliberadamente para evitar estimaciones excesivamente optimistas.
"""

    seccion_estimacion = (
        "## Estimación de esfuerzo – A) PoC generada automáticamente\n\n"
        "Esta estimación se refiere únicamente al alcance realmente generado por el sistema.\n\n"
        + bloque_generada
        + "\n\n---\n\n"
        + "## Estimación de esfuerzo – B) PoC completa solicitada\n\n"
        "Esta estimación considera la implementación completa tal y como fue descrita por el usuario, "
        "incluyendo integraciones reales, configuración cloud y validación de permisos.\n\n"
        + bloque_completa
        + "\n\n"
        + nota.strip()
    )

    return contenido + "\n\n---\n\n" + seccion_estimacion


def generar_readme_manual(
    nombre: str,
    arquitectura: str,
    tecnologias: str,
    endpoints_generados: List[str],
) -> str:
    """
    Genera README_MANUAL solo cuando hay generación PARCIAL.
    """

    endpoints_str = "\n".join(f"- {e}" for e in endpoints_generados) or "- (No detectados)"

    prompt = f"""
Genera un README_MANUAL profesional, conciso y orientado a acción.

IMPORTANTE:
- Sé específico con TODAS las tecnologías incluidas en la plantilla.
- Para cada tecnología externa, indica configuración, permisos y riesgos asociados.
- No menciones tecnologías concretas si no aparecen en la plantilla.
- No incluyas dependencias del núcleo (como el framework principal) como si fueran externas.
- Indica explícitamente que las dependencias externas deben añadirse manualmente al archivo `requirements.txt`, ya que el sistema está en modo PARCIAL y no modifica dependencias automáticamente.

Proyecto: {nombre}

Arquitectura inferida:
{arquitectura}

Tecnologías declaradas:
{tecnologias}

Endpoints generados automáticamente:
{endpoints_str}

Estructura obligatoria:

# Implementación manual requerida

## 1. Componentes no automatizados
- Qué requiere lógica externa real

## 2. Dependencias externas detectadas
- Librerías que requieren configuración adicional

## 3. Configuración necesaria
- Credenciales
- Variables de entorno
- Permisos / IAM
- Servicios a habilitar

## 4. Riesgos técnicos
- Autenticación
- Permisos
- Cuotas
- Dependencias externas

## 5. Checklist profesional
- Logging
- Manejo de errores
- Tests de integración
- Seguridad

Sé directo.
No repitas el README_FINAL.
No incluyas texto genérico.
"""

    contenido = _llamar_modelo(prompt)

    # 🔎 Eliminación de repeticiones accidentales del modelo
    marcador = "# Implementación manual requerida"
    partes = contenido.split(marcador)
    if len(partes) > 2:
        contenido = marcador + partes[1]

    return contenido.strip()
