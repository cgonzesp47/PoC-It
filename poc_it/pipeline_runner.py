"""
Ejecución del pipeline PoC-it desacoplada de la recogida de input.

`run_pipeline()` contiene la lógica que antes vivía en `poc_it.main.main()`
(análisis de viabilidad -> generación/materialización -> publicación GitLab),
pero recibe la `PlantillaUsuario` ya construida en lugar de leerla de `input()`.

Esto permite que tanto el CLI (`poc_it/main.py`) como la interfaz web
(`poc_it/web/`) compartan exactamente el mismo camino de ejecución.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from poc_it.analisis.analizador_viabilidad import PlantillaUsuario, analizar_viabilidad
from poc_it.entrada.demo_progress import demo_progress, is_demo_mode
from poc_it.modulos.models import ModoGeneracion
from poc_it.modulos.opciones import generar_opciones
from poc_it.orquestacion.orquestador_parcial import OrquestadorParcial


async def run_pipeline(plantilla: PlantillaUsuario, publish: bool = True) -> Dict[str, Any]:
    """Ejecuta el pipeline completo para una `PlantillaUsuario` ya recogida.

    Devuelve un dict con, entre otras claves: `modo`, `nombre_proyecto`,
    `estado_final`, `pytest_ok`, `publishable`, `archivos_creados`,
    `publish_url`, `duracion_segundos`. En caso de error no controlado,
    devuelve `{"error": str(exc)}`.
    """
    demo = is_demo_mode()
    inicio_ejecucion = time.perf_counter()

    try:
        resultado = await analizar_viabilidad(plantilla)

        if demo:
            total_steps = 8 if resultado.modo in {ModoGeneracion.COMPLETO, ModoGeneracion.PARCIAL} else 5

            if getattr(resultado, "contexto_proyecto", None) and getattr(resultado.contexto_proyecto, "contexto_normalizado", None):
                cn = resultado.contexto_proyecto.contexto_normalizado
                funcionalidades = ", ".join(getattr(cn, "funcionalidades_clave", []) or []) or "N/D"
                integ = ", ".join(getattr(cn, "integraciones_externas", []) or []) or "No"
                contratos = len(getattr(cn, "contratos_api", []) or [])
                demo_progress.step(2, total_steps, "Contexto normalizado generado")
                demo_progress.info(f"Funcionalidades detectadas: {funcionalidades}")
                demo_progress.info(f"Integraciones externas: {integ}")
                demo_progress.info(f"Contratos API identificados: {contratos}")
            else:
                demo_progress.step(2, total_steps, "Contexto normalizado generado")

            modo_str = getattr(resultado.modo, "value", None) or getattr(resultado.modo, "name", None) or str(resultado.modo)
            demo_progress.step(3, total_steps, f"Modo seleccionado: {modo_str}")
        else:
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
        resultado_generacion: Dict[str, Any] = {}
        generacion_exitosa = False

        if resultado.modo in {ModoGeneracion.COMPLETO, ModoGeneracion.PARCIAL}:
            if not demo:
                print("=== GENERACIÓN LIBRE ACTIVADA ===\n")
            else:
                demo_progress.step(4, total_steps, "Generación y validación del spec")
                demo_progress.info("Generando especificación técnica...")

            orquestador = OrquestadorParcial(
                plantilla=plantilla,
                modo_generacion=resultado.modo.value,
                context=resultado.contexto_proyecto,
                t_clasificacion_inicio=resultado.t_clasificacion_inicio,
                t_clasificacion_fin=resultado.t_clasificacion_fin,
                t_ejecucion_inicio=inicio_ejecucion,
            )

            resultado_generacion = orquestador.ejecutar()
            nombre_proyecto_generado = resultado_generacion["nombre_proyecto"]

            if not demo:
                print("======================================")
                print("GENERACIÓN FINALIZADA")
                print("======================================\n")
                print(f"Proyecto generado: {nombre_proyecto_generado}")
                print(f"Archivos creados: {len(resultado_generacion.get('archivos_creados', []))}\n")

            estado_final = resultado_generacion.get("estado_final")
            pytest_ok = resultado_generacion.get("pytest_ok")

            publishable = resultado_generacion.get("publishable")
            if publishable is None:
                publishable = estado_final in ("OK", "OK_DEGRADED")

            generacion_exitosa = bool(publishable)

        else:
            if not demo:
                print("=== ANÁLISIS ESTRATÉGICO ===\n")
            else:
                demo_progress.step(4, total_steps, "Generación del análisis estratégico")
                demo_progress.info("Generando README_ANÁLISIS.md...")

            opciones = resultado.opciones or generar_opciones(
                arquitectura=resultado.arquitectura,
                limites=plantilla.limites,
                tecnologias=plantilla.tecnologias,
            )

            from poc_it.materializacion.generador_informes import generar_readme_asesor
            from poc_it.materializacion.materializador_archivos import materializar_proyecto
            from poc_it.analisis.estimador_esfuerzo import calcular_estimacion_esfuerzo

            fin_ejecucion = time.perf_counter()
            tiempo_real_pocit_horas = (fin_ejecucion - inicio_ejecucion) / 3600.0
            if tiempo_real_pocit_horas <= 0.0:
                tiempo_real_pocit_horas = 1.0 / 3600.0

            estimacion_manual = calcular_estimacion_esfuerzo(
                descripcion_proyecto=plantilla.problema,
                modo=None,
                tiempo_real_scopeguardian_horas=tiempo_real_pocit_horas,
                generable=False,
            )

            contenido_asesor = generar_readme_asesor(
                nombre=plantilla.nombre,
                problema=plantilla.problema,
                usuarios=plantilla.usuarios,
                funcionalidades=plantilla.funcionalidades,
                limites=plantilla.limites,
                tecnologias=plantilla.tecnologias,
                arquitectura=resultado.arquitectura,
                opciones=opciones,
                estimacion_manual=estimacion_manual,
            )

            materializar_proyecto(
                nombre_proyecto=plantilla.nombre,
                estructura={"README_ANÁLISIS.md": contenido_asesor},
            )

            if demo:
                demo_progress.step(5, total_steps, "Análisis materializado")
                demo_progress.info("README_ANÁLISIS.md: OK")

            nombre_proyecto_generado = plantilla.nombre
            generacion_exitosa = True

            print("Se ha generado README_ANÁLISIS.md con el análisis estratégico y estimación conceptual.\n")

        # ==============================================
        # PUBLICACIÓN CENTRALIZADA (ÚNICO PUNTO)
        # ==============================================

        publish_url = None
        publish_status = "SKIPPED"

        try:
            if publish and nombre_proyecto_generado and generacion_exitosa:
                from poc_it.integraciones.gitlab_publisher import GitLabPublisher

                ruta_generada = Path("output") / nombre_proyecto_generado

                publisher = GitLabPublisher()
                url_repo = publisher.publicar(nombre_proyecto_generado, str(ruta_generada))

                if url_repo:
                    publish_url = url_repo
                    publish_status = "OK"
                    if demo:
                        demo_progress.step(total_steps, total_steps, "Publicación GitLab")
                        demo_progress.info("Repositorio creado: OK")
                        demo_progress.info(f"URL: {url_repo}")
                    else:
                        print("======================================")
                        print("REPOSITORIO PUBLICADO EN GITLAB")
                        print("======================================\n")
                        print(f"URL: {url_repo}\n")
                else:
                    publish_status = "FAILED"
                    if demo:
                        demo_progress.step(total_steps, total_steps, "Publicación GitLab")
                        demo_progress.info("Estado: NO COMPLETADA")
                        demo_progress.info("Motivo: el publisher no devolvió URL (resultado vacío)")
            elif nombre_proyecto_generado and not generacion_exitosa:
                publish_status = "SKIPPED_NOT_PUBLISHABLE"
                if demo:
                    demo_progress.step(total_steps, total_steps, "Publicación GitLab")
                    demo_progress.info("Estado: OMITIDA")
                    demo_progress.info("Motivo: proyecto no publicable (publishable=False).")
                else:
                    print("\n[GitLab] Publicación omitida: proyecto no publicable (publishable=False).")

        except Exception as e:
            publish_status = "ERROR"
            if demo:
                demo_progress.step(total_steps, total_steps, "Publicación GitLab")
                demo_progress.info("Estado: NO COMPLETADA")
                demo_progress.info(f"Motivo: {str(e).strip()}")
                demo_progress.info("El artefacto se ha generado correctamente en local")
            else:
                print("\n[GitLab] Publicación omitida o fallida:")
                print(str(e))

        fin_ejecucion = time.perf_counter()
        duracion = fin_ejecucion - inicio_ejecucion
        if not demo:
            minutos = int(duracion // 60)
            segundos = int(duracion % 60)
            print(f"\nTiempo total de ejecución: {minutos}m {segundos}s\n")

        return {
            "modo": getattr(resultado.modo, "value", None) or str(resultado.modo),
            "nombre_proyecto": nombre_proyecto_generado,
            "generacion_exitosa": generacion_exitosa,
            "publish_status": publish_status,
            "publish_url": publish_url,
            "duracion_segundos": duracion,
            **resultado_generacion,
        }

    except Exception as exc:
        return {"error": str(exc)}
