from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Dict

from poc_it.materializador_archivos import materializar_proyecto
from poc_it.postprocesador_alineacion import AlignmentIssue, postprocesar_alineacion_llm

logger = logging.getLogger(__name__)


def postprocesar_alineacion_por_pytest(
    *,
    nombre_proyecto: str,
    project_dir: str,
    resultado: Dict[str, Any],
    estructura: Dict[str, str],
    archivos_creados: list[str],
) -> None:
    """Ejecuta un post-procesado de alineación basado en fallos de pytest.

    Mantiene comportamiento del orquestador:
    - Si `tests/` existe y pytest falla, crea un AlignmentIssue con el output.
    - Lanza hasta `POSTPROCESADO_MAX_REPAIRS` reparaciones usando el LLM.
    - Nunca rompe el flujo: ante excepción, loggea y continúa.
    """
    try:
        max_repairs = int(os.getenv("POSTPROCESADO_MAX_REPAIRS", "2"))

        last_signature: str = ""

        for attempt in range(max_repairs + 1):
            issues: list[AlignmentIssue] = []

            try:
                tests_dir = os.path.join(project_dir, "tests")
                if os.path.isdir(tests_dir):
                    p = subprocess.run(
                        ["python", "-m", "pytest", "-q"],
                        cwd=project_dir,
                        capture_output=True,
                        text=True,
                    )
                    if p.returncode != 0:
                        out = (p.stdout or "") + "\n" + (p.stderr or "")
                        # Circuit breaker: si pytest falla con el MISMO output de forma repetida,
                        # no tiene sentido seguir re-parcheando: suele llevar a loops de regeneración
                        # indirectos y consumo de tokens.
                        signature = out.strip()[:1200]
                        if signature and signature == last_signature:
                            logger.info(
                                "[POST] Pytest vuelve a fallar con la misma firma; se corta post-procesado para evitar loop."
                            )
                            break
                        last_signature = signature

                        issues.append(
                            AlignmentIssue(
                                code="PYTEST_FAILURE",
                                severity="error",
                                file="tests",
                                message="Errores residuales: pytest falla; alinear tests/handlers/modelos.",
                                hint=out[:8000],
                            )
                        )
            except Exception as exc:
                logger.info("[POST] Aviso: pytest no ejecutable: %s", exc)

            if not issues:
                break

            if attempt >= max_repairs:
                logger.info("[POST] Reparación por pytest agotada; se continúa sin bloquear.")
                break

            logger.info(
                "[POST] Pytest falló; ejecutando post-procesado (attempt %s/%s)",
                attempt + 1,
                max_repairs,
            )

            spec_dict = resultado.get("spec") if isinstance(resultado, dict) else None
            pp = postprocesar_alineacion_llm(
                estructura=estructura,
                spec=spec_dict if isinstance(spec_dict, dict) else None,
                issues=issues,
                max_files=6,
            )
            if not pp.patched_files:
                logger.info("[POST] El modelo no devolvió patch; se continúa.")
                break

            materializar_proyecto(
                nombre_proyecto=nombre_proyecto,
                estructura=pp.patched_files,
                limpiar_directorio=False,
            )
            estructura.update(pp.patched_files)
            archivos_creados.extend(
                [os.path.join(project_dir, p.replace("/", os.sep)) for p in pp.patched_files.keys()]
            )
    except Exception as exc:
        logger.info("[POST] Aviso: post-procesado de alineación falló (se continúa): %s", exc)
