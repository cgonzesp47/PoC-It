from __future__ import annotations

import os
import re
from typing import Optional

from poc_it.orquestacion.constantes import OUTPUT_DIRNAME
from poc_it.orquestacion.render_estimacion import generar_bloque_estimacion_markdown


_ESTIMACION_HEADER = "## Estimación de esfuerzo – PoC generada automáticamente"


def _patch_estimacion_section(markdown: str, new_section: str) -> str:
    """
    Parche determinista: reemplaza la sección de estimación completa.

    Estrategia:
    - Encontrar el header exacto de estimación.
    - Reemplazar desde ese header hasta:
      * el siguiente header H2 (línea que empiece por '## '), o
      * EOF.

    Si no se encuentra la sección, se añade al final (precedida por separador).
    """
    if not markdown:
        markdown = ""

    # Localizar el inicio de la sección por header H2 exacto.
    m = re.search(rf"(?m)^{re.escape(_ESTIMACION_HEADER)}\s*$", markdown)
    if not m:
        # No existe: añadimos al final.
        suffix = "\n\n---\n\n" if markdown.strip() else ""
        return (markdown.rstrip() + suffix + new_section.strip() + "\n").lstrip()

    start = m.start()

    # Encontrar el inicio del siguiente H2 después del header encontrado (excluyendo el propio).
    m2 = re.search(r"(?m)^##\s+", markdown[m.end() :])
    end = (m.end() + m2.start()) if m2 else len(markdown)

    before = markdown[:start].rstrip()
    after = markdown[end:].lstrip()

    glued = before + "\n\n" + new_section.strip() + "\n\n" + after
    return glued.rstrip() + "\n"


def parchear_bloque_estimacion(
    *,
    nombre_proyecto: str,
    estimacion_generada,
    readme_final_filename: str = "README_FINAL.md",
    readme_analisis_filename: str = "README_ANALISIS.md",
) -> None:
    """
    Actualiza el bloque de estimación en README_FINAL.md y README_ANALISIS.md (si existe),
    sin re-generar documentación con LLM.

    Nota: se apoya en el render determinista `generar_bloque_estimacion_markdown`.
    """
    if not nombre_proyecto:
        raise ValueError("nombre_proyecto es obligatorio")

    bloque_estimacion = generar_bloque_estimacion_markdown(estimacion_generada)

    nota = (
        "> Nota: El ahorro real puede ser superior al porcentaje mostrado.  \n"
        "> El porcentaje se limita deliberadamente para evitar estimaciones excesivamente optimistas."
    )

    new_section = (
        f"{_ESTIMACION_HEADER}\n\n"
        "Esta estimación se refiere únicamente al alcance realmente generado por el sistema.\n\n"
        f"{bloque_estimacion}\n\n"
        f"{nota}"
    )

    project_dir = os.path.normpath(os.path.join(OUTPUT_DIRNAME, nombre_proyecto))

    for fname in (readme_final_filename, readme_analisis_filename):
        path = os.path.join(project_dir, fname)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            md = f.read()
        patched = _patch_estimacion_section(md, new_section)
        if patched != md:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(patched)
