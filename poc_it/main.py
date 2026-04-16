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
import time

from poc_it.analizador_viabilidad import (
    PlantillaUsuario,
    analizar_viabilidad,
)
from poc_it.models import ModoGeneracion
from poc_it.orquestador_parcial import OrquestadorParcial
from poc_it.opciones import generar_opciones


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


async def main() -> None:
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
        # GENERACIÓN LIBRE (COMPLETO o PARCIAL)
        # ==============================================

        if resultado.modo in {ModoGeneracion.COMPLETO, ModoGeneracion.PARCIAL}:

            print("=== GENERACIÓN LIBRE ACTIVADA ===\n")

            # Pasamos SOLO el problema como descripción principal.
            # El resto del contexto ya viaja estructurado en PlantillaUsuario.
            orquestador = OrquestadorParcial(
                plantilla=user_data,
                modo_generacion=str(resultado.modo).split(".")[-1],
            )

            resultado_generacion = orquestador.ejecutar()

            print("======================================")
            print("GENERACIÓN FINALIZADA")
            print("======================================\n")
            print(f"Proyecto generado: {resultado_generacion['nombre_proyecto']}")
            print(f"Archivos creados: {len(resultado_generacion.get('archivos_creados', []))}\n")

        # ==============================================
        # MODO ASESOR
        # ==============================================

        else:
            print("=== ANÁLISIS ESTRATÉGICO ===\n")

            opciones = resultado.opciones or generar_opciones(
                arquitectura=resultado.arquitectura,
                limites=user_data.limites,
                tecnologias=user_data.tecnologias,
            )

            from poc_it.generador_informes import generar_readme_asesor
            from poc_it.materializador_archivos import materializar_proyecto

            from poc_it.estimador_esfuerzo import calcular_estimacion_llm

            estimacion_manual = calcular_estimacion_llm(
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

            print("Se ha generado README_ANÁLISIS.md con el análisis estratégico y estimación conceptual.\n")

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
