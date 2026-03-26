"""
ScopeGuardian - Main mínimo de prueba

Permite probar manualmente el Oráculo de Viabilidad
mediante entrada por consola.
"""

import asyncio
from scope_guardian.analizador_viabilidad import (
    PlantillaUsuario,
    analizar_viabilidad,
)

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
    """
    Solicita por consola los 6 campos de la plantilla.
    """

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
    """
    Punto de entrada principal.
    """

    try:
        user_data = collect_user_input()
        resultado = await analizar_viabilidad(user_data)

        print("\n==============================")
        print("INFORME DE VIABILIDAD")
        print("==============================\n")
        print(f"DECISION: {'TRUE' if resultado.puede_generarse_automaticamente else 'FALSE'}")
        print("\n==============================\n")

        if resultado.arquitectura.strip():
            print("=== ARQUITECTURA PROPUESTA ===\n")
            print(resultado.arquitectura)
            print("\n==============================\n")

        if not resultado.puede_generarse_automaticamente:
            print("Resultado: Solicitud fuera de generación automática.\n")
        else:
            print("Resultado: Puede generarse automáticamente.\n")

        # --------------------------------------------------
        # NUEVO FLUJO: MODO GENERADOR PRINCIPAL
        # --------------------------------------------------
        from scope_guardian.capacidades import evaluar_capacidades
        from scope_guardian.orquestador_parcial import OrquestadorParcial

        descripcion_global = (
            f"Nombre: {user_data.nombre}\n"
            f"Problema: {user_data.problema}\n"
            f"Usuarios: {user_data.usuarios}\n"
            f"Funcionalidades: {user_data.funcionalidades}\n"
            f"Límites: {user_data.limites}\n"
            f"Tecnologías: {user_data.tecnologias}\n"
        )

        # Solo intentamos generar si el analizador declaró viabilidad automática
        if resultado.puede_generarse_automaticamente:
            evaluacion = evaluar_capacidades(descripcion_global)

            if evaluacion.generables:
                # Determinar tipo de generación
                if evaluacion.manuales:
                    modo_generacion = "PARCIAL"
                else:
                    modo_generacion = "COMPLETA"

                print(f"\n=== MODO GENERADOR {modo_generacion} ACTIVADO ===\n")

                orquestador = OrquestadorParcial(
                    nombre_proyecto=user_data.nombre,
                    descripcion_global=descripcion_global,
                    tecnologias=user_data.tecnologias,
                )

                resumen = orquestador.ejecutar()

                print("======================================")
                print(f"GENERACIÓN {modo_generacion} FINALIZADA")
                print("======================================\n")
                print(f"Proyecto generado en: output/{resumen['nombre_proyecto']}")
                print(f"Archivos creados: {len(resumen['archivos_creados'])}\n")

                if modo_generacion == "PARCIAL":
                    print("Elementos que requieren intervención manual:")
                    for bloque in resumen.get("bloques_manuales", []):
                        print(f"- {bloque}")
                    print("\nConsulta README_FINAL.md para instrucciones adicionales.")
                else:
                    print("La PoC ha sido generada completamente sin pasos manuales.")

                return

        # --------------------------------------------------
        # MODO ASESOR ESTRATÉGICO (Incompatibilidad tecnológica)
        # --------------------------------------------------
        print("=== ANÁLISIS ESTRATÉGICO DE RIESGOS ===\n")

        from scope_guardian.opciones import generar_opciones

        opciones_estrategicas = resultado.opciones

        # Si el analizador no generó opciones, forzamos generación estratégica
        if not opciones_estrategicas:
            opciones_estrategicas = generar_opciones(
                arquitectura=resultado.arquitectura,
                limites=user_data.limites,
                tecnologias=user_data.tecnologias,
            )

        if opciones_estrategicas:
            for opcion in opciones_estrategicas:
                print(opcion)
                print("\n------------------------------\n")

            print(
                "Estas validaciones deben ejecutarse antes de iniciar la implementación.\n"
                "El objetivo es confirmar o descartar hipótesis críticas de viabilidad técnica."
            )
        else:
            print("No se han podido generar análisis estratégicos.")
        
        return

    except KeyboardInterrupt:
        print("\nEjecución cancelada por el usuario.")
    except Exception as exc:
        print("\nHa ocurrido un error durante la evaluación:")
        print(str(exc))


if __name__ == "__main__":
    asyncio.run(main())
