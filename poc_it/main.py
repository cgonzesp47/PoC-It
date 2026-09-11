"""
PoC-it – Main simplificado (modo libre)

Nuevo flujo:

1. Analizar viabilidad.
2. Mostrar modo detectado.
3. Si el modo es COMPLETO o PARCIAL:
   → Delegar completamente en OrquestadorParcial (generación libre).
4. Si el modo es ASESOR:
   → Generar análisis estratégico.

La ejecución real del pipeline vive en `poc_it.pipeline_runner.run_pipeline`,
compartida con la interfaz web (`poc_it/web/`). Este módulo solo se encarga
de la recogida de input por terminal y de invocar ese pipeline.
"""

import asyncio
import logging
import os

from poc_it.analisis.analizador_viabilidad import PlantillaUsuario
from poc_it.entrada.demo_progress import demo_progress, is_demo_mode
from poc_it.pipeline_runner import run_pipeline


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
        # - Por lo tanto, el contador de run_pipeline arranca justo después de recoger el input.
        user_data = collect_user_input()
        if demo:
            demo_progress.step(1, 8, "Entrada recibida")
            demo_progress.info(f"Nombre: {user_data.nombre}")
            demo_progress.info(f"Tecnologías solicitadas: {user_data.tecnologias}")

        resultado = await run_pipeline(user_data, publish=True)

        if resultado.get("error"):
            print("\nHa ocurrido un error durante la evaluación:")
            print(resultado["error"])

    except KeyboardInterrupt:
        print("\nEjecución cancelada por el usuario.")
    except Exception as exc:
        print("\nHa ocurrido un error durante la evaluación:")
        print(str(exc))


if __name__ == "__main__":
    asyncio.run(main())
