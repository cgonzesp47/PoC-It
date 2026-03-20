"""
ScopeGuardian - Main mínimo de prueba

Permite probar manualmente el Oráculo de Viabilidad
mediante entrada por consola.
"""

import asyncio
from scope_guardian.analizador_viabilidad import UserTemplate, run_scope_guardian


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


def collect_user_input() -> UserTemplate:
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

    return UserTemplate(
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
        await run_scope_guardian(user_data)
    except KeyboardInterrupt:
        print("\nEjecución cancelada por el usuario.")
    except Exception as exc:
        print("\nHa ocurrido un error durante la evaluación:")
        print(str(exc))


if __name__ == "__main__":
    asyncio.run(main())
