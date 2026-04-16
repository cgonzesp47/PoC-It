"""
PoC-it - Generador dinámico de informes (README)

Genera:
- README_FINAL (siempre)
- README_MANUAL (solo en modo PARCIAL)

Diseñado para modelos locales pequeños (qwen7b).
Prompts concisos, estructurados y orientados a valor.
"""

from __future__ import annotations

from typing import List
from poc_it.llm_client import chat_completion_text
from poc_it.estimador_esfuerzo import (
    generar_bloque_markdown,
)


# Modelo gestionado centralmente por llm_client


def _llamar_modelo(prompt: str, max_tokens: int = 1200) -> str:
    raw = chat_completion_text(
        prompt=prompt,
        system=None,
        temperature=0.2,
        max_tokens=max_tokens,
    )

    # Blindaje contra respuestas None o no-string del modelo
    if not isinstance(raw, str):
        return ""

    return raw.strip()


def generar_readme_final(
    nombre: str,
    descripcion_global: str,
    arquitectura: str,
    endpoints_generados: List[str],
    modo: str,
    tecnologias: str,
    estimacion_generada,
    estimacion_completa,
) -> str:
    """
    Genera README_FINAL dinámico.
    """

    endpoints_str = "\n".join(f"- {e}" for e in endpoints_generados) or "- (No detectados)"

    prompt = f"""
Genera un README_FINAL profesional, técnico y orientado a arquitectura.

Este documento debe aportar valor real a un desarrollador o arquitecto que evalúa la PoC.

IMPORTANTE:
- Si el modo es PARCIAL, deja explícitamente claro qué integraciones NO están implementadas.
- No exageres funcionalidades.
- No incluyas comentarios meta.
- No seas superficial.

Proyecto: {nombre}
Modo de generación: {modo}
Tecnologías declaradas: {tecnologias}

Descripción de la PoC:
{descripcion_global}

Arquitectura inferida:
{arquitectura}

Endpoints generados:
{endpoints_str}

Estructura obligatoria (profesional y detallada):

# {nombre}

## 1. Resumen Ejecutivo
- Objetivo de la PoC
- Alcance real implementado
- Nivel de generación (Completa / Parcial)
- Supuestos técnicos clave

## 2. Arquitectura y Diseño
- Descripción conceptual de la arquitectura
- Capas y responsabilidades
- Flujo request → servicio → integración externa
- Decisiones de diseño relevantes
- Trade-offs asumidos

## 3. Modelo de Ejecución
- Cómo se ejecuta la aplicación
- Supuestos de entorno (local vs cloud si aplica)
- Modelo de autenticación si existe
- Dependencias externas implicadas

## 4. Alcance Implementado
- Funcionalidad realmente operativa
- Elementos preparados pero no integrados (si modo PARCIAL)
- Simplificaciones realizadas

## 5. Limitaciones y Consideraciones Técnicas
- Aspectos no cubiertos
- Riesgos técnicos conocidos
- Consideraciones de seguridad
- Consideraciones de escalabilidad

## 6. Ejecución Local
- Instalación
- Comando uvicorn
- Acceso a documentación OpenAPI

El documento debe ser claro, profesional y útil para revisión técnica.
No repitas información trivial.
"""

    contenido = _llamar_modelo(prompt, max_tokens=1200)

    # Eliminación de repeticiones accidentales del modelo
    marcador = f"# {nombre}"
    partes = contenido.split(marcador)
    if len(partes) > 2:
        contenido = marcador + partes[1]

    contenido = contenido.strip()

    # ======================================================
    # ESTIMACIONES RECIBIDAS DESDE ORQUESTADOR (una sola llamada externa)
    # ======================================================

    bloque_generada = generar_bloque_markdown(estimacion_generada)
    bloque_completa = generar_bloque_markdown(estimacion_completa)

    # Eliminar fila de PoC-it en estimación B (proyecto completo no automatizable)
    lineas = bloque_completa.splitlines()
    lineas_filtradas = [l for l in lineas if "PoC-it" not in l]
    bloque_completa = "\n".join(lineas_filtradas)

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
Genera un README_MANUAL técnico, estructurado y orientado a implementación real.

Este documento debe guiar a un desarrollador para completar manualmente las partes no automatizadas.

IMPORTANTE:
- No repitas el README_FINAL.
- No generes texto genérico.
- No inventes tecnologías no declaradas.
- No incluyas el framework principal como dependencia externa.
- Indica explícitamente que las dependencias externas deben añadirse manualmente a `requirements.txt` porque el sistema está en modo PARCIAL.

Proyecto: {nombre}

Arquitectura inferida:
{arquitectura}

Tecnologías declaradas:
{tecnologias}

Endpoints generados automáticamente:
{endpoints_str}

Estructura obligatoria (profesional y sin redundancias):

# Implementación manual requerida

## 1. Contexto
- Qué partes no han sido automatizadas
- Por qué requieren intervención manual

## 2. Modelo de Configuración Esperado
- Modelo de identidad/autenticación
- Servicios externos implicados
- Dependencias de red si aplican

## 3. Configuración Técnica Paso a Paso
- Habilitación de servicios necesarios
- Creación y configuración de credenciales
- Variables de entorno obligatorias
- Permisos e IAM requeridos
- Despliegue en entorno cloud si aplica

## 4. Dependencias Externas
- Librerías que deben añadirse manualmente
- Configuración adicional necesaria
- Riesgos de mala configuración

## 5. Validación Post-Configuración
- Cómo comprobar que la integración funciona
- Errores comunes esperables
- Señales de fallo típicas

## 6. Checklist Técnico Final
Checklist breve, claro y no duplicado que permita validar que todo está correctamente configurado.

El documento debe ser claro, técnico y útil para implementación real.
No incluyas contenido redundante.
"""

    contenido = _llamar_modelo(prompt, max_tokens=650)

    # Eliminación de repeticiones accidentales del modelo
    marcador = "# Implementación manual requerida"
    partes = contenido.split(marcador)
    if len(partes) > 2:
        contenido = marcador + partes[1]

    return contenido.strip()


def generar_readme_asesor(
    nombre: str,
    problema: str,
    usuarios: str,
    funcionalidades: str,
    limites: str,
    tecnologias: str,
    arquitectura: str,
    opciones: list[str],
    estimacion_manual,
) -> str:
    """
    README_ANALISIS optimizado:
    - Máxima densidad técnica
    - Sin redundancias narrativas
    - Enfoque arquitectónico real
    - Estimación incluida (crítica en modo asesor)
    """

    from poc_it.estimador_esfuerzo import generar_bloque_markdown

    contenido = f"# Análisis Técnico-Estratégico – {nombre}\n\n"

    # ------------------------------------------------------
    # 1. Contexto condensado
    # ------------------------------------------------------
    contenido += "## 1. Contexto\n\n"
    contenido += f"- Problema a resolver: {problema}\n"
    if usuarios:
        contenido += f"- Usuarios objetivo: {usuarios}\n"
    if funcionalidades:
        contenido += f"- Alcance funcional esperado: {funcionalidades}\n"
    if limites:
        contenido += f"- Restricciones declaradas: {limites}\n"
    contenido += "\n"

    # ------------------------------------------------------
    # 2. Evaluación arquitectónica directa
    # ------------------------------------------------------
    contenido += "## 2. Evaluación arquitectónica\n\n"
    contenido += f"- Stack tecnológico declarado: {tecnologias}\n"
    contenido += f"- Patrón arquitectónico implícito: {arquitectura}\n"
    contenido += (
        "- Nivel de complejidad inferido: derivado de integraciones externas, "
        "necesidad de autenticación, persistencia y despliegue.\n\n"
    )

    # ------------------------------------------------------
    # 3. Riesgos estructurales reales
    # ------------------------------------------------------
    contenido += "## 3. Riesgos estructurales\n\n"

    if opciones:
        for opcion in opciones[:5]:
            contenido += f"- {opcion.strip()}\n"
    else:
        contenido += "- No se han identificado riesgos críticos adicionales a nivel estructural.\n"

    contenido += "\n"

    # ------------------------------------------------------
    # 4. Recomendaciones técnicas accionables
    # ------------------------------------------------------
    contenido += "## 4. Recomendaciones técnicas\n\n"
    contenido += (
        "- Validar modelo de identidad y permisos antes de integrar servicios externos.\n"
        "- Diseñar gestión de errores y logging desde el inicio.\n"
        "- Separar claramente capas (API / servicio / integración externa).\n"
        "- Incorporar pruebas básicas de integración en fases tempranas.\n"
    )

    # ------------------------------------------------------
    # 5. Estimación (no se elimina, es crítica en asesor)
    # ------------------------------------------------------
    contenido += "\n## 5. Estimación conceptual de implementación manual\n\n"
    contenido += generar_bloque_markdown(estimacion_manual)

    return contenido.strip()
