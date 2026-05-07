"""
PoC-it – Orquestador Libre (modo generación completa)

Nuevo enfoque:

- Eliminamos generación parcial fragmentada.
- Eliminamos proyecto base rígido.
- Eliminamos expansión por bloques.
- Delegamos completamente en el LLM la generación total.
- Validación sintáctica ya gestionada en generador_artefactos.

Flujo nuevo:

1. Generar proyecto completo vía LLM.
2. Materializar archivos.
3. Retornar resumen.
"""

from __future__ import annotations

from typing import Dict, Any, Optional, Tuple
import os
import subprocess

from poc_it.generador_artefactos import generar_proyecto_completo
from poc_it.generador_tests_unitarios import generar_tests_unitarios_minimos
from poc_it.materializador_archivos import materializar_proyecto
from poc_it.models import ProjectContext, PlantillaUsuario
from poc_it.clasificador import clasificar_viabilidad


def _runtime_verify_fastapi_project(project_dir: str) -> Tuple[bool, str]:
    """
    Verificación runtime mínima (genérica) para proyectos FastAPI generados.

    Objetivo:
    - Confirmar que el entrypoint `app.main:app` es importable SIN configuración externa.

    Importante:
    - No valida endpoints concretos (p.ej. /health) porque no siempre existirán.
    - No valida integraciones externas (Drive, DB, etc.). Solo valida "arranque/import-time".
    - Devuelve detalles ricos (stdout/stderr + hints) para repair loop.
    """
    py = "python"

    p1 = subprocess.run(
        [py, "-c", "import app.main; print('IMPORT_OK')"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if p1.returncode != 0:
        out = (p1.stdout or "") + "\n" + (p1.stderr or "")
        hint = (
            "HINTS:\n"
            "- Si el error es ValidationError/BaseSettings: estás validando settings en import-time; usa lazy get_settings().\n"
            "- Si el error es TypeError missing positional arguments: estás instanciando un servicio/clase en import-time sin pasar args; crea el servicio dentro del endpoint o con Depends.\n"
            "- Si el error es ImportError: estás importando un símbolo que no existe o tienes imports circulares.\n"
            "- Si el error es ModuleNotFoundError: falta una dependencia en requirements.txt.\n"
        )
        return False, f"[runtime_verify] import app.main failed:\n{out}\n{hint}"

    return True, "IMPORT_OK"


class OrquestadorParcial:
    """
    Orquestador libre basado en generación completa.
    """

    # ======================================================
    # 🔹 MÉTODOS PRIVADOS DE ESTIMACIÓN (MODULARIZADOS)
    # ======================================================

    def _estimacion_generada(self, modo: str, horas: float):
        from poc_it.estimador_esfuerzo import calcular_estimacion_llm

        descripcion = f"""
Proyecto: {self.nombre_proyecto}
Modo: {modo}

Descripción:
{self.descripcion_global}
"""
        return calcular_estimacion_llm(
            descripcion_proyecto=descripcion,
            modo=modo,
            tiempo_real_scopeguardian_horas=horas,
        )

    def _estimacion_completa(self, horas: float):
        from poc_it.estimador_esfuerzo import calcular_estimacion_llm

        return calcular_estimacion_llm(
            descripcion_proyecto=self.descripcion_global,
            modo="COMPLETA_SOLICITADA",
            tiempo_real_scopeguardian_horas=horas,
        )

    def _estimacion_manual(self):
        from poc_it.estimador_esfuerzo import calcular_estimacion_llm

        return calcular_estimacion_llm(
            descripcion_proyecto=self.descripcion_global,
            modo=None,
            tiempo_real_scopeguardian_horas=0.0,
            generable=False,
        )

    def __init__(self, plantilla: PlantillaUsuario, modo_generacion: str):
        self.plantilla = plantilla
        self.nombre_proyecto = plantilla.nombre
        self.descripcion_global = plantilla.problema
        self.tecnologias = plantilla.tecnologias
        self.modo_generacion = modo_generacion

    def ejecutar(self) -> Dict[str, Any]:
        """
        Ejecuta el flujo completo incluyendo clasificación basada en ProjectContext.
        """

        try:
            # ==========================================
            # 1) Construcción del contexto inicial
            # ==========================================
            # Usamos la plantilla REAL proporcionada por el usuario
            context = ProjectContext(plantilla=self.plantilla)

            # ==========================================
            # 1.1) Fase de Normalización Formal de Contexto
            # ==========================================
            from poc_it.normalizador_contexto import normalizar_plantilla
            from poc_it.models import ContextoNormalizado

            # Normalizamos usando la plantilla completa real
            contexto_dict = normalizar_plantilla(self.plantilla)

            try:
                contexto_normalizado = ContextoNormalizado(**contexto_dict)
                context.contexto_normalizado = contexto_normalizado
                context.registrar_modelo("normalizacion_contexto", "chat_completion_json")
            except Exception:
                # En caso extremo de estructura inesperada
                context.contexto_normalizado = None

            # ==========================================
            # 2) Clasificación con nuevo agente
            # ==========================================
            import time
            t_clasificacion_inicio = time.perf_counter()
            context = clasificar_viabilidad(context)
            t_clasificacion_fin = time.perf_counter()

            # Mostrar estado actual del contexto
            print("\n[DEBUG CONTEXT DESPUÉS DE CLASIFICACIÓN]")
            print(context.model_dump_json(indent=2))
            print("")

            modo_generacion = context.clasificacion or self.modo_generacion

            # (Clasificador oficial mantenido. Sin comparativa adicional.)

            # ==========================================
            # 3) Generación (omitida si modo ASESOR)
            # ==========================================
            import time

            estructura = {}
            archivos_creados = []
            tiempo_generacion_horas = 0.0

            if modo_generacion.upper() != "ASESOR":
                t_generacion_inicio = time.perf_counter()

                resultado = generar_proyecto_completo(
                    descripcion_global=self.descripcion_global,
                    contexto_normalizado=(
                        context.contexto_normalizado.model_dump()
                        if context.contexto_normalizado
                        else None
                    ),
                )

                # ------------------------------------------
                # Persistir SPEC.json como artefacto del proyecto
                # Fuente de verdad para documentación y auditoría
                # ------------------------------------------
                try:
                    spec = resultado.get("spec")
                    if isinstance(spec, dict) and spec:
                        spec_path = os.path.join("output", self.nombre_proyecto, "SPEC.json")
                        os.makedirs(os.path.dirname(spec_path), exist_ok=True)
                        with open(spec_path, "w", encoding="utf-8") as f:
                            import json as _json
                            f.write(_json.dumps(spec, ensure_ascii=False, indent=2))
                        archivos_creados.append(spec_path)
                except Exception as _e:
                    print(f"[DEBUG] No se pudo persistir SPEC.json: {_e}")

                t_generacion_fin = time.perf_counter()
                tiempo_generacion_horas = (t_generacion_fin - t_generacion_inicio) / 3600

                files = resultado.get("files", [])

                if not files:
                    raise ValueError("El modelo no generó archivos válidos.")

                estructura = {
                    f["path"]: f["content"]
                    for f in files
                    if "path" in f and "content" in f
                }

                archivos_creados = materializar_proyecto(
                    nombre_proyecto=self.nombre_proyecto,
                    estructura=estructura,
                )

                # ==========================================
                # 3.2) Generación de pruebas unitarias mínimas
                # ==========================================
                # Módulo independiente: basado en SPEC (si existe) y en la estructura generada.
                # Controlado por flag, para no añadir coste si no se desea.
                generar_tests = os.getenv("GENERAR_TESTS_UNITARIOS", "1").strip() in (
                    "1",
                    "true",
                    "True",
                    "yes",
                    "YES",
                )
                if generar_tests:
                    try:
                        spec_dict = resultado.get("spec") if isinstance(resultado, dict) else None
                        tests_result = generar_tests_unitarios_minimos(
                            nombre_proyecto=self.nombre_proyecto,
                            spec=spec_dict if isinstance(spec_dict, dict) else None,
                            estructura_generada=estructura,
                            intentos=1,
                        )
                        if tests_result.errores:
                            print("[TESTS] Aviso: generación de tests con warnings:", tests_result.errores)

                        if tests_result.estructura_tests:
                            archivos_tests = materializar_proyecto(
                                nombre_proyecto=self.nombre_proyecto,
                                estructura=tests_result.estructura_tests,
                                limpiar_directorio=False,
                            )
                            archivos_creados.extend(archivos_tests)
                    except Exception as _e:
                        print(f"[TESTS] Error generando/materializando tests: {_e}")

                # ==========================================
                # 3.1) Verificación runtime mínima (evita PoCs que no arrancan)
                # ==========================================
                # Si falla, intentamos un repair loop dirigido usando el traceback real.
                project_dir = os.path.join("output", self.nombre_proyecto)

                max_runtime_repairs = 5
                for attempt in range(max_runtime_repairs + 1):
                    ok_runtime, detail = _runtime_verify_fastapi_project(project_dir)
                    if ok_runtime:
                        break

                    if attempt >= max_runtime_repairs:
                        raise ValueError(
                            "El proyecto generado no supera verificación runtime.\n"
                            + detail
                            + "\n\n"
                            + "Sugerencia: evita validar configuración/credenciales en import-time; "
                              "haz lazy init y valida en runtime (en el endpoint que lo necesite)."
                        )

                    # Repair: pedimos al modelo corregir SOLO los archivos implicados en el traceback.
                    # Importante: no "inventar" símbolos (p.ej. get_settings / get_drive_service) que luego no existan.
                    error_context = f"""
FALLO EN VERIFICACIÓN RUNTIME (import app.main)
El proyecto debe ser importable sin configuración externa.

Error:
{detail}

REGLAS DE REPARACIÓN (MÍNIMAS, CANÓNICAS)
- Corrige SOLO los archivos del proyecto implicados en el traceback.
- No cambies la arquitectura ni introduzcas nuevas dependencias innecesarias: corrige wiring/errores.
- Evita instanciar servicios/configuración en import-time. Haz lazy init dentro de endpoints/funciones.
- Mantén el patrón FastAPI con routers.
- Regla general: si importas `from X import Y`, entonces Y DEBE existir en X (no inventar símbolos).
- Si existe `app/config/settings.py`, el patrón de settings debe ser consistente:
  - Debe existir `class Settings(BaseSettings)`.
  - Debe existir `def get_settings() -> Settings` (cacheada con lru_cache) y ser el ÚNICO punto de creación.
  - Prohibido `settings = Settings()` en import-time si hay campos requeridos.
  - Si algún módulo hace `from app.config.settings import get_settings`, entonces get_settings DEBE existir.

SALIDA
- Devuelve JSON con la lista completa de archivos corregidos (solo los modificados) con formato:
  {{ "files": [{{"path":"...", "content":"..."}}] }}
"""

                    reparacion = generar_proyecto_completo(
                        descripcion_global=self.descripcion_global + "\n\n" + error_context,
                        contexto_normalizado=(
                            context.contexto_normalizado.model_dump()
                            if context.contexto_normalizado
                            else None
                        ),
                        intentos=2,
                    )

                    repaired_files = reparacion.get("files", [])
                    if not repaired_files:
                        continue

                    # Aplicar solo los archivos devueltos (parche)
                    patch = {f["path"]: f["content"] for f in repaired_files if "path" in f and "content" in f}
                    if patch:
                        materializar_proyecto(nombre_proyecto=self.nombre_proyecto, estructura=patch)
                        estructura.update(patch)
                        archivos_creados.extend(
                            [os.path.join(project_dir, p.replace("/", os.sep)) for p in patch.keys()]
                        )

            # ======================================================
            # CÁLCULO DE ESTIMACIONES MEDIANTE MÉTODOS MODULARIZADOS
            # ======================================================

            modo_upper = modo_generacion.upper()

            # Inicialización segura (evita errores en ejecución paralela)
            estimacion_generada = None
            estimacion_completa = None
            estimacion_manual = None

            if modo_upper == "PARCIAL":
                estimacion_generada = self._estimacion_generada(
                    modo_generacion,
                    tiempo_generacion_horas,
                )
                estimacion_completa = self._estimacion_completa(
                    tiempo_generacion_horas,
                )
                estimacion_manual = self._estimacion_manual()

            elif modo_upper == "COMPLETO":
                estimacion_generada = self._estimacion_generada(
                    modo_generacion,
                    tiempo_generacion_horas,
                )
                estimacion_completa = self._estimacion_completa(
                    tiempo_generacion_horas,
                )
                estimacion_manual = None  # explícito

            elif modo_upper == "ASESOR":
                estimacion_manual = self._estimacion_manual()
                estimacion_generada = None
                estimacion_completa = None

            # Blindaje adicional: asegurar que las estimaciones sean objetos válidos
            # Nunca sustituimos por string porque generar_bloque_markdown espera atributos
            if estimacion_generada is None:
                estimacion_generada = self._estimacion_manual()

            if estimacion_completa is None:
                estimacion_completa = self._estimacion_manual()

            if estimacion_manual is None:
                estimacion_manual = self._estimacion_manual()

            # ======================================================
            # GENERACIÓN DE DOCUMENTACIÓN (solo si el proyecto pasó gate import-time)
            # ======================================================

            # NOTA: si el modo es ASESOR, puede no haber estructura/archivos; en ese caso
            # seguimos generando documentación porque el objetivo es asesorar, no compilar.
            generar_docs = True
            if modo_generacion.upper() != "ASESOR":
                project_dir = os.path.join("output", self.nombre_proyecto)
                ok_runtime, detail = _runtime_verify_fastapi_project(project_dir)
                if not ok_runtime:
                    generar_docs = False
                    print("[DOCS] Saltando generación de documentación: el proyecto no es importable aún.")
                    print(detail)

            if generar_docs:
                from concurrent.futures import ThreadPoolExecutor
                from poc_it.generador_informes import (
                    generar_readme_final,
                    generar_readme_manual,
                    generar_readme_asesor,
                )
                from poc_it.opciones import generar_opciones

                # Detectar endpoints simples a partir de paths
                endpoints_detectados = [
                    path for path in estructura.keys()
                    if path.endswith(".py")
                ]

                # Usar primero contexto normalizado si existe
                if context.contexto_normalizado:
                    arquitectura_real = context.contexto_normalizado.objetivo_tecnico
                    limites_reales = ", ".join(context.contexto_normalizado.restricciones_tecnicas)
                    tecnologias_reales = ", ".join(context.contexto_normalizado.integraciones_externas)
                    funcionalidades_reales = ", ".join(context.contexto_normalizado.funcionalidades_clave)
                    usuarios_reales = ", ".join(context.contexto_normalizado.actores_principales)
                else:
                    arquitectura_real = context.plantilla.problema
                    limites_reales = context.plantilla.limites or ""
                    tecnologias_reales = context.plantilla.tecnologias or ""
                    funcionalidades_reales = context.plantilla.funcionalidades or ""
                    usuarios_reales = context.plantilla.usuarios or ""

                opciones_estrategicas = generar_opciones(
                    arquitectura=arquitectura_real,
                    limites=limites_reales,
                    tecnologias=tecnologias_reales,
                )

                t_documentacion_inicio = time.perf_counter()

                # Documentación debe usar el modo/proveedor de DOCS (no el de code-gen)
                # (Se asume que generador_informes delega en LLM con un modo/fase distinto)
                with ThreadPoolExecutor(max_workers=3) as executor:
                    # SPEC fuente de verdad:
                    # - Preferimos el SPEC de generación (resultado["spec"]) si existe
                    # - Si no existe, fallback al ContextoNormalizado
                    spec_dict = None
                    try:
                        spec_dict = resultado.get("spec") if isinstance(resultado, dict) else None
                    except Exception:
                        spec_dict = None
                    if not isinstance(spec_dict, dict) or not spec_dict:
                        spec_dict = context.contexto_normalizado.model_dump() if context.contexto_normalizado else None

                    future_final = executor.submit(
                        generar_readme_final,
                        self.nombre_proyecto,
                        self.descripcion_global,
                        "Arquitectura generada dinámicamente por LLM",
                        endpoints_detectados,
                        modo_generacion.upper(),
                        self.tecnologias,
                        estimacion_generada,
                        estimacion_completa,
                        spec=spec_dict,
                    )

                    future_analisis = executor.submit(
                        generar_readme_asesor,
                        self.nombre_proyecto,
                        context.plantilla.problema,
                        usuarios_reales,
                        funcionalidades_reales,
                        limites_reales,
                        tecnologias_reales,
                        arquitectura_real,
                        opciones_estrategicas,
                        estimacion_manual,
                        spec_dict,
                    )

                    future_manual = None
                    if modo_generacion.upper() == "PARCIAL":
                        future_manual = executor.submit(
                            generar_readme_manual,
                            self.nombre_proyecto,
                            "Arquitectura generada dinámicamente por LLM",
                            self.tecnologias,
                            endpoints_detectados,
                            estructura,
                            spec_dict,
                        )

                    readme_final = future_final.result()
                    readme_analisis = future_analisis.result()
                    readme_manual = future_manual.result() if future_manual else None

                # IMPORTANTE:
                # materializar_proyecto() limpia el directorio por defecto para evitar artefactos residuales.
                # Para escribir documentación debemos NO limpiar, o nos cargamos el código generado.
                archivos_readme_final = materializar_proyecto(
                    nombre_proyecto=self.nombre_proyecto,
                    estructura={"README.md": readme_final},
                    limpiar_directorio=False,
                )
                archivos_creados.extend(archivos_readme_final)

                archivos_readme_analisis = materializar_proyecto(
                    nombre_proyecto=self.nombre_proyecto,
                    estructura={"README_ANALISIS.md": readme_analisis},
                    limpiar_directorio=False,
                )
                archivos_creados.extend(archivos_readme_analisis)

                if readme_manual:
                    archivos_readme_manual = materializar_proyecto(
                        nombre_proyecto=self.nombre_proyecto,
                        estructura={"README_MANUAL.md": readme_manual},
                        limpiar_directorio=False,
                    )
                    archivos_creados.extend(archivos_readme_manual)

                t_documentacion_fin = time.perf_counter()

                print("\n[PERFORMANCE]")
                print(f"- Clasificación: {t_clasificacion_fin - t_clasificacion_inicio:.2f}s")
                print(f"- Generación libre: {t_generacion_fin - t_generacion_inicio:.2f}s")
                print(f"- Documentación: {t_documentacion_fin - t_documentacion_inicio:.2f}s\n")

        except Exception as exc:
            # No contaminar el README principal con logs: dejamos un README_ERROR.md
            # y un README.md mínimo indicando dónde mirar.
            fallback_readme = (
                f"# {self.nombre_proyecto}\n\n"
                "## Estado\n\n"
                "La generación no finalizó correctamente. Revisa `README_ERROR.md` para ver el detalle del error.\n"
            )

            fallback_error = (
                f"# {self.nombre_proyecto} – Error de generación\n\n"
                "## Error durante la generación libre\n\n"
                f"Error detectado:\n\n```\n{str(exc)}\n```\n"
            )

            archivos_creados = materializar_proyecto(
                nombre_proyecto=self.nombre_proyecto,
                estructura={"README.md": fallback_readme, "README_ERROR.md": fallback_error},
                limpiar_directorio=True,
            )

            return {
                "nombre_proyecto": self.nombre_proyecto,
                "archivos_creados": archivos_creados,
                "error": str(exc),
            }

        return {
            "nombre_proyecto": self.nombre_proyecto,
            "archivos_creados": archivos_creados,
        }
