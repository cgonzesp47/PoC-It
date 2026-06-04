"""
PoC-it – Main simplificado (modo libre)

Nuevo flujo:

1. Analizar viabilidad.
2. Mostrar modo detectado.
3. Si el modo es COMPLETO o PARCIAL:
   → Delegar completamente en OrquestadorParcial (generación libre).
4. Si el modo es ASESOR:
   → Generar análisis estratégico.
"""

import asyncio
import logging
import os
import time

from poc_it.analizador_viabilidad import PlantillaUsuario, analizar_viabilidad
from poc_it.models import ModoGeneracion
from poc_it.opciones import generar_opciones
from poc_it.orquestador_parcial import OrquestadorParcial


TEMPLATE_PROMPT = """
==============================
Plantilla de Definición de PoC
==============================

1. Nombre de la PoC
2. ¿Qué problema resuelve?
3. ¿Quién utilizará el sistema?
4. ¿Qué debería poder hacer el sistema?
5. ¿Hay reglas o límites importantes?
6. ¿Qué tecnologías/integraciones necesita?

Responde a cada punto cuando se te solicite.
"""


def collect_user_input() -> PlantillaUsuario:
    print(TEMPLATE_PROMPT)

    nombre = input("1. Nombre de la PoC:\n> ").strip()
    problema = input("\n2. ¿Qué problema resuelve?\n> ").strip()
    usuarios = input("\n3. ¿Quién utilizará el sistema?\n> ").strip()
    funcionalidades = input("\n4. ¿Qué debería poder hacer el sistema?\n> ").strip()
    limites = input("\n5. ¿Hay reglas o límites importantes?\n> ").strip()
    tecnologias = input("\n6. ¿Qué tecnologías/integraciones necesita?\n> ").strip()

    return PlantillaUsuario(
        nombre=nombre,
        problema=problema,
        usuarios=usuarios,
        funcionalidades=funcionalidades,
        limites=limites,
        tecnologias=tecnologias,
    )


def _configure_logging() -> None:
    """
    Configura logging por defecto para CLI.

    Nota:
    - Se mantiene simple: consola + nivel configurable por env var.
    - Esto hace visibles los logs del orquestador tras migrar prints->logging.
    """
    level_name = os.getenv("POCIT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def main() -> None:
    _configure_logging()
    inicio_ejecucion = time.perf_counter()

    try:
        user_data = collect_user_input()
        resultado = await analizar_viabilidad(user_data)

        print("\n==============================")
        print("INFORME DE VIABILIDAD")
        print("==============================\n")
        print(f"MODO: {resultado.modo}\n")

        arquitectura = resultado.arquitectura or ""

        if isinstance(arquitectura, str) and arquitectura.strip():
            print("=== ARQUITECTURA PROPUESTA ===\n")
            print(arquitectura)
            print("\n==============================\n")

        # ==============================================
        # GENERACIÓN / MATERIALIZACIÓN
        # ==============================================

        nombre_proyecto_generado = None

        if resultado.modo in {ModoGeneracion.COMPLETO, ModoGeneracion.PARCIAL}:

            print("=== GENERACIÓN LIBRE ACTIVADA ===\n")

            orquestador = OrquestadorParcial(
                plantilla=user_data,
                modo_generacion=str(resultado.modo).split(".")[-1],
            )

            resultado_generacion = orquestador.ejecutar()

            nombre_proyecto_generado = resultado_generacion["nombre_proyecto"]

            print("======================================")
            print("GENERACIÓN FINALIZADA")
            print("======================================\n")
            print(f"Proyecto generado: {nombre_proyecto_generado}")
            print(f"Archivos creados: {len(resultado_generacion.get('archivos_creados', []))}\n")

            # Determinar si el proyecto es publicable (fuente de verdad estructurada)
            #
            # `OrquestadorParcial` expone:
            # - estado_final: "OK" | "OK_DEGRADED" | "ERROR"
            # - publishable: bool
            # - pytest_ok: bool (compat)
            #
            # Política:
            # - Publish SOLO si `publishable=True`.
            # - OK_DEGRADED sigue siendo publicable, pero SOLO si pytest (suite mínima) pasó.
            estado_final = resultado_generacion.get("estado_final")
            pytest_ok = resultado_generacion.get("pytest_ok")

            publishable = resultado_generacion.get("publishable")
            if publishable is None:
                # compatibilidad: si no viene, caer a heurística antigua
                publishable = estado_final in ("OK", "OK_DEGRADED")

            generacion_exitosa = bool(publishable)

        else:
            print("=== ANÁLISIS ESTRATÉGICO ===\n")

            opciones = resultado.opciones or generar_opciones(
                arquitectura=resultado.arquitectura,
                limites=user_data.limites,
                tecnologias=user_data.tecnologias,
            )

            from poc_it.generador_informes import generar_readme_asesor
            from poc_it.materializador_archivos import materializar_proyecto
            from poc_it.estimador_esfuerzo import calcular_estimacion_esfuerzo

            estimacion_manual = calcular_estimacion_esfuerzo(
                descripcion_proyecto=user_data.problema,
                modo=None,
                tiempo_real_scopeguardian_horas=0.0,
                generable=False,
            )

            contenido_asesor = generar_readme_asesor(
                nombre=user_data.nombre,
                problema=user_data.problema,
                usuarios=user_data.usuarios,
                funcionalidades=user_data.funcionalidades,
                limites=user_data.limites,
                tecnologias=user_data.tecnologias,
                arquitectura=resultado.arquitectura,
                opciones=opciones,
                estimacion_manual=estimacion_manual,
            )

            materializar_proyecto(
                nombre_proyecto=user_data.nombre,
                estructura={
                    "README_ANÁLISIS.md": contenido_asesor
                },
            )

            nombre_proyecto_generado = user_data.nombre

            # En modo ASESOR también se publica el repo: es un artefacto válido (README_ANÁLISIS.md).
            generacion_exitosa = True

            print("Se ha generado README_ANÁLISIS.md con el análisis estratégico y estimación conceptual.\n")

        # ==============================================
        # PUBLICACIÓN CENTRALIZADA (ÚNICO PUNTO)
        # ==============================================

        try:
            # Publicar SOLO si la generación fue realmente exitosa
            if nombre_proyecto_generado and locals().get("generacion_exitosa", False):
                from poc_it.integraciones.gitlab_publisher import GitLabPublisher
                from pathlib import Path

                ruta_generada = Path("output") / nombre_proyecto_generado

                publisher = GitLabPublisher()
                url_repo = publisher.publicar(
                    nombre_proyecto_generado,
                    str(ruta_generada)
                )

                if url_repo:
                    print("======================================")
                    print("REPOSITORIO PUBLICADO EN GITLAB")
                    print("======================================\n")
                    print(f"URL: {url_repo}\n")
            elif nombre_proyecto_generado:
                print("\n[GitLab] Publicación omitida: proyecto no publicable (publishable=False).")

                    # FUTURO: limpieza opcional
                    # if os.getenv("POCIT_CLEAN_LOCAL_AFTER_PUBLISH", "false").lower() == "true":
                    #     shutil.rmtree(ruta_generada)

        except Exception as e:
            print("\n[GitLab] Publicación omitida o fallida:")
            print(str(e))

        fin_ejecucion = time.perf_counter()
        duracion = fin_ejecucion - inicio_ejecucion
        minutos = int(duracion // 60)
        segundos = int(duracion % 60)
        print(f"\nTiempo total de ejecución: {minutos}m {segundos}s\n")

    except KeyboardInterrupt:
        print("\nEjecución cancelada por el usuario.")
    except Exception as exc:
        print("\nHa ocurrido un error durante la evaluación:")
        print(str(exc))


if __name__ == "__main__":
    asyncio.run(main())
