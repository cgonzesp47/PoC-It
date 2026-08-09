from __future__ import annotations

from typing import Dict


def sanitize_generated_tests(files: Dict[str, str]) -> Dict[str, str]:
    """
    Compatibilidad transitoria.

    La arquitectura nueva no aplica sanitización regex ni reescribe suites
    completas tras el render. La validación debe ocurrir antes del render,
    sobre el plan y sus artefactos estructurados.

    Esta función conserva la firma histórica, pero devuelve los archivos
    sin modificaciones.
    """
    return dict(files or {})
