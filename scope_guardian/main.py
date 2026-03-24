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

        print("=== OPCIONES DE PROFUNDIZACIÓN ===\n")
        for opcion in resultado.opciones:
            print(opcion)

        # Bloque interactivo: el programa debe quedarse esperando selección
        if resultado.opciones:
            while True:
                seleccion = input(
                    "\nSelecciona una opción (1-3) o pulsa Enter para salir:\n> "
                ).strip()

                if seleccion == "":
                    print("Saliendo sin seleccionar opción.")
                    break

                if seleccion.isdigit() and 1 <= int(seleccion) <= len(resultado.opciones):
                    opcion_elegida = resultado.opciones[int(seleccion) - 1]
                    print(f"\nHas seleccionado la opción {seleccion}.")
                    
                    from scope_guardian.materializador_opcion import materializar_opcion
                    contexto = (
                        f"Nombre: {user_data.nombre}\n"
                        f"Problema: {user_data.problema}\n"
                        f"Funcionalidades: {user_data.funcionalidades}\n"
                        f"Límites: {user_data.limites}\n"
                        f"Tecnologías: {user_data.tecnologias}\n"
                    )
                    
                    detalle = materializar_opcion(opcion_elegida, contexto)
                    
                    print("\n==============================")
                    print("DESARROLLO DE LA OPCIÓN")
                    print("==============================\n")
                    print(detalle)
                    print("\n==============================")
                    
                    break
                else:
                    print("Selección no válida. Introduce un número correcto.")
        else:
            print("No se han generado opciones.")

    except KeyboardInterrupt:
        print("\nEjecución cancelada por el usuario.")
    except Exception as exc:
        print("\nHa ocurrido un error durante la evaluación:")
        print(str(exc))


if __name__ == "__main__":
    asyncio.run(main())
