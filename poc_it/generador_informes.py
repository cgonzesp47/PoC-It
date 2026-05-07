"""
PoC-it - Generador dinámico de informes (README)

Genera:
- README_FINAL (siempre)
- README_MANUAL (solo en modo PARCIAL)

Diseñado para modelos locales pequeños (qwen7b).
Prompts concisos, estructurados y orientados a valor.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional
import re

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
        fase="documentacion",
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
    spec: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Genera README_FINAL dinámico.

    Si se proporciona `spec`, se utiliza como fuente de verdad para evitar
    contradicciones doc/código (p.ej. request.type json vs multipart).
    """

    endpoints_str = "\n".join(f"- {e}" for e in endpoints_generados) or "- (No detectados)"

    spec = spec or {}
    spec_json = ""
    try:
        # Compacto para no inflar tokens
        import json as _json
        spec_json = _json.dumps(spec, ensure_ascii=False)
    except Exception:
        spec_json = str(spec)

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

SPEC (FUENTE DE VERDAD - NO CONTRADECIR)
- Este SPEC describe el contrato objetivo (endpoints, request/response, env/deps y notas).
- El README debe ser CONSISTENTE con este SPEC.
{spec_json}

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


def _extraer_variables_entorno_desde_codigo(estructura: Dict[str, str]) -> List[str]:
    """
    Heurística agnóstica: detecta variables de entorno usadas en el código generado.
    - os.getenv("X"), os.environ["X"], os.environ.get("X")
    - sin hardcodear proveedores
    """
    vars_encontradas = set()

    rx_getenv = re.compile(r"os\.getenv\(\s*[\"']([A-Z0-9_]+)[\"']\s*\)")
    rx_environ_get = re.compile(r"os\.environ\.get\(\s*[\"']([A-Z0-9_]+)[\"']\s*\)")
    rx_environ_idx = re.compile(r"os\.environ\[\s*[\"']([A-Z0-9_]+)[\"']\s*\]")

    for path, content in estructura.items():
        if not path.endswith(".py"):
            continue
        content = content or ""
        for rx in (rx_getenv, rx_environ_get, rx_environ_idx):
            for m in rx.findall(content):
                vars_encontradas.add(m)

    return sorted(vars_encontradas)


def _extraer_todos_placeholders(estructura: Dict[str, str]) -> List[str]:
    """
    Heurística agnóstica: detecta TODO/FIXME/PLACEHOLDER/CHANGEME y patrones típicos
    que requieren intervención manual.
    """
    hallazgos: List[str] = []
    rx = re.compile(r"(TODO|FIXME|PLACEHOLDER|CHANGEME|<your-[^>]+>|REPLACE_ME)", re.IGNORECASE)

    for path, content in estructura.items():
        if not isinstance(content, str):
            continue
        for i, line in enumerate(content.splitlines(), start=1):
            if rx.search(line):
                hallazgos.append(f"- {path}:{i}: {line.strip()[:160]}")
        if len(hallazgos) > 30:
            break
    return hallazgos


def generar_readme_manual(
    nombre: str,
    arquitectura: str,
    tecnologias: str,
    endpoints_generados: List[str],
    estructura: Dict[str, str] | None = None,
    spec: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Genera README_MANUAL solo cuando hay generación PARCIAL.

    Mejora agnóstica:
    - Si se proporciona `estructura` (path->content), extrae heurísticas locales:
      * variables de entorno detectadas
      * TODO/FIXME/PLACEHOLDER
    - Usa esas señales como “capabilities gap” para que el manual sea accionable
      sin hardcodear proveedores (GCP/AWS/etc).
    """
    endpoints_str = "\n".join(f"- {e}" for e in endpoints_generados) or "- (No detectados)"

    estructura = estructura or {}
    env_vars = _extraer_variables_entorno_desde_codigo(estructura)
    todos = _extraer_todos_placeholders(estructura)

    env_vars_str = "\n".join(f"- {v}" for v in env_vars) or "- (No detectadas automáticamente)"
    todos_str = "\n".join(todos) or "- (No se detectaron TODO/FIXME/PLACEHOLDER)"

    spec = spec or {}
    spec_json = ""
    try:
        import json as _json
        spec_json = _json.dumps(spec, ensure_ascii=False)
    except Exception:
        spec_json = str(spec)

    prompt = f"""
Genera un README_MANUAL técnico, estructurado y orientado a implementación real.

Este documento debe guiar a un desarrollador para completar manualmente las partes no automatizadas.

IMPORTANTE:
- No repitas el README_FINAL.
- No generes texto genérico.
- No inventes tecnologías no declaradas.
- No inventes nombres de variables de entorno: usa las detectadas o indica que no se detectaron.
- No incluyas el framework principal como dependencia externa.
- Si el sistema está en modo PARCIAL, céntrate en: permisos, credenciales, despliegue, configuración de runtime, y validación operativa.

Proyecto: {nombre}

Arquitectura inferida:
{arquitectura}

Tecnologías declaradas:
{tecnologias}

Endpoints generados automáticamente:
{endpoints_str}

SPEC (FUENTE DE VERDAD - NO CONTRADECIR)
- Este SPEC describe el contrato objetivo (endpoints, request/response, env/deps y notas).
- El README_MANUAL debe ser CONSISTENTE con este SPEC.
{spec_json}

SEÑALES DETECTADAS EN EL CÓDIGO (FUENTE DE VERDAD)
Variables de entorno detectadas:
{env_vars_str}

TODO/FIXME/PLACEHOLDER detectados:
{todos_str}

Estructura obligatoria (profesional y sin redundancias):

# Implementación manual requerida

## 1. Qué funciona ya (generado automáticamente)
Lista breve de lo que está implementado y arrancable.

## 2. Qué NO está garantizado / puede requerir intervención
Lista de gaps típicos: permisos/credenciales del entorno, IDs/URLs reales, configuración de red/egress, etc.

## 3. Configuración de runtime (variables de entorno)
- Lista EXACTA de variables de entorno detectadas (si hay).
- Para cada una: qué representa, ejemplo de valor, y cómo validarla.
- Si no hay variables detectadas, indica qué parámetros suelen ser necesarios para integraciones externas.

## 4. Dependencias externas y permisos (pasos manuales)
- Qué habilitar/configurar fuera del código (permisos/roles, activación de APIs, secretos, etc.) de forma genérica.
- Qué comprobar para evitar 401/403/404.

## 5. Validación post-configuración
- Pasos de prueba del endpoint y señales claras de fallo.

## 6. Checklist final
Checklist breve y accionable.

No incluyas contenido redundante.
"""

    contenido = _llamar_modelo(prompt, max_tokens=1500)

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
    spec: Optional[Dict[str, Any]] = None,
) -> str:
    """
    README_ANALISIS optimizado:
    - Máxima densidad técnica
    - Sin redundancias narrativas
    - Enfoque arquitectónico real
    - Estimación incluida (crítica en modo asesor)
    """

    from poc_it.estimador_esfuerzo import generar_bloque_markdown

    spec = spec or {}
    spec_json = ""
    try:
        import json as _json
        spec_json = _json.dumps(spec, ensure_ascii=False)
    except Exception:
        spec_json = str(spec)

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
