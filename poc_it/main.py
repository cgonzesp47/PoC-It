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
from poc_it.demo_progress import demo_progress, is_demo_mode
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


# demo_mode se gestiona en poc_it.demo_progress
def log_step(step: int, total: int, title: str, detail: str | None = None) -> None:
    demo_progress.step(step, total, title)
    if detail:
        for line in detail.splitlines():
            if line.strip():
                demo_progress.info(line)


def _configure_logging() -> None:
    """
    Configura logging por defecto para CLI.

    Estrategia demo:
    - En demo, reducimos ruido: solo WARNING/ERROR.
    - Además silenciamos prints/debug sueltos de librerías/módulos (stdout/stderr),
      porque hay partes del pipeline que emiten "[DEBUG] ..." sin pasar por logging.
    - En dev, respetamos POCIT_LOG_LEVEL (default INFO).
    """
    demo = is_demo_mode()

    if demo:
        # En demo queremos output “presentable”: solo mostramos los pasos [x/n] por print().
        # Cualquier logging del pipeline interno se silencia para evitar trazas/ruido.
        level = logging.CRITICAL
        fmt = "%(levelname)s | %(name)s | %(message)s"
    else:
        level_name = os.getenv("POCIT_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
        fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

    logging.basicConfig(level=level, format=fmt)

    # Silenciar librerías ruidosas en demo
    if demo:
        for noisy in ("httpx", "urllib3", "uvicorn", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        # Filtro de stdout/stderr para eliminar líneas "[DEBUG] ..." en modo demo.
        # Motivo: aún existen mensajes legacy que NO pasan por logging y salen por print().
        import io
        import re
        import sys

        debug_line = re.compile(r"^\[DEBUG\]\s*")
        demo_noise = re.compile(
            r"^\s*(\[\{.*\}\]\s*)$"
            r"|^\s*(\['.*'\]\s*)$"
            r"|^(Traceback \(most recent call last\):)"
            r"|^(ModuleNotFoundError: )"
            r"|^(RUNTIME_WIRING_VERIFY_FAILED)"
            r"|^(OpenAPI probe failed\.)$"
            r"|^WARNING\s*\|\s*poc_it\..*"
            r"|^ERROR\s*\|\s*poc_it\..*"
        )

        class _StdoutFilter(io.TextIOBase):
            def __init__(self, underlying):
                self._u = underlying

            def write(self, s: str) -> int:
                if not s:
                    return 0
                # Preservar saltos de línea y filtrar por línea
                parts = s.splitlines(True)
                kept = [p for p in parts if not debug_line.match(p) and not demo_noise.match(p.strip())]
                if not kept:
                    return len(s)
                return self._u.write("".join(kept))

            def flush(self) -> None:
                return self._u.flush()

        sys.stdout = _StdoutFilter(sys.stdout)
        sys.stderr = _StdoutFilter(sys.stderr)


async def main() -> None:
    _configure_logging()

    try:
        demo = is_demo_mode()

        # IMPORTANTE:
        # - El tiempo total de ejecución NO debe incluir el tiempo del usuario rellenando la plantilla.
        # - Por lo tanto, iniciamos el contador justo después de recoger el input.
        user_data = collect_user_input()
        if demo:
            demo_progress.step(1, 8, "Entrada recibida")
            demo_progress.info(f"Nombre: {user_data.nombre}")
            demo_progress.info(f"Tecnologías solicitadas: {user_data.tecnologias}")
        inicio_ejecucion = time.perf_counter()

        resultado = await analizar_viabilidad(user_data)

        if demo:
            total_steps = 8 if resultado.modo in {ModoGeneracion.COMPLETO, ModoGeneracion.PARCIAL} else 5

            # [2/8] y [3/8] se imprimen en orquestador_parcial para tener más detalles reales.
            # Aquí mantenemos únicamente fallback si no hay contexto disponible.
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

        if resultado.modo in {ModoGeneracion.COMPLETO, ModoGeneracion.PARCIAL}:
            if not demo:
                print("=== GENERACIÓN LIBRE ACTIVADA ===\n")
            else:
                demo_progress.step(4, total_steps, "Generación y validación del spec")
                demo_progress.info("Generando especificación técnica...")

            orquestador = OrquestadorParcial(
                plantilla=user_data,
                modo_generacion=resultado.modo.value,
                context=resultado.contexto_proyecto,
                t_clasificacion_inicio=resultado.t_clasificacion_inicio,
                t_clasificacion_fin=resultado.t_clasificacion_fin,
            )

            resultado_generacion = orquestador.ejecutar()
            nombre_proyecto_generado = resultado_generacion["nombre_proyecto"]

            if demo:
                archivos_n = len(resultado_generacion.get("archivos_creados", []))
                demo_progress.step(5, total_steps, "Código backend generado")
                demo_progress.info(f"Archivos creados: {archivos_n}")
                # tests result (pytest_ok viene del orquestador)
                pytest_ok = bool(resultado_generacion.get("pytest_ok"))
                estado_final = resultado_generacion.get("estado_final")
                detail = f"Pytest: {'OK' if pytest_ok else 'FAIL'}"
                if estado_final:
                    detail += f"\nEstado: {estado_final}"
                demo_progress.step(6, total_steps, "Tests generados y ejecutados")
                for line in detail.splitlines():
                    if line.strip():
                        demo_progress.info(line)
                if resultado.modo == ModoGeneracion.PARCIAL:
                    demo_progress.step(7, total_steps, "Documentación generada")
                    demo_progress.info("README.md: OK")
                    demo_progress.info("README_MANUAL.md: OK")
                    demo_progress.info("README_ANALISIS.md: OK")
                else:
                    demo_progress.step(7, total_steps, "Documentación generada")
                    demo_progress.info("README.md: OK")
                    demo_progress.info("README_ANALISIS.md: OK")
            else:
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
                    if demo:
                        log_step(total_steps, total_steps, "Publicación GitLab", f"Repositorio creado: OK\nURL: {url_repo}")
                    else:
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
            if demo:
                demo_progress.step(8, total_steps, "Publicación GitLab")
                demo_progress.info("Estado: NO COMPLETADA")
                demo_progress.info(f"Motivo: {str(e).strip()}")
                demo_progress.info("La PoC se ha generado correctamente en local")
            else:
                print("\n[GitLab] Publicación omitida o fallida:")
                print(str(e))

        fin_ejecucion = time.perf_counter()
        duracion = fin_ejecucion - inicio_ejecucion
        minutos = int(duracion // 60)
        segundos = int(duracion % 60)
        if not demo:
            print(f"\nTiempo total de ejecución: {minutos}m {segundos}s\n")

    except KeyboardInterrupt:
        print("\nEjecución cancelada por el usuario.")
    except Exception as exc:
        print("\nHa ocurrido un error durante la evaluación:")
        print(str(exc))


if __name__ == "__main__":
    asyncio.run(main())
