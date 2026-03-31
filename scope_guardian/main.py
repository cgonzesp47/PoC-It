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
from scope_guardian.models import ModoGeneracion

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
        print(f"MODO: {resultado.modo}")
        print("\n==============================\n")

        if resultado.arquitectura.strip():
            print("=== ARQUITECTURA PROPUESTA ===\n")
            print(resultado.arquitectura)
            print("\n==============================\n")

        if resultado.modo == ModoGeneracion.COMPLETO:
            print("Resultado: Generación completa.\n")
        elif resultado.modo == ModoGeneracion.PARCIAL:
            print("Resultado: Generación parcial.\n")
        else:
            print("Resultado: Asesor técnico.\n")

        # --------------------------------------------------
        # NUEVO FLUJO: GENERACIÓN CON PLANTILLAS + INYECCIÓN
        # --------------------------------------------------

        from scope_guardian.generador_proyecto_base import generar_proyecto_base
        from scope_guardian.generador_artefactos import (
            generar_diseno_estructural,
            generar_schemas_clases,
            generar_services_py,
            generar_api_endpoints,
        )
        from pathlib import Path

        descripcion_global = (
            f"Nombre: {user_data.nombre}\n"
            f"Problema: {user_data.problema}\n"
            f"Usuarios: {user_data.usuarios}\n"
            f"Funcionalidades: {user_data.funcionalidades}\n"
            f"Límites: {user_data.limites}\n"
            f"Tecnologías: {user_data.tecnologias}\n"
        )

        if resultado.modo == ModoGeneracion.COMPLETO:
            print("\n=== MODO GENERADOR COMPLETO ACTIVADO ===\n")

            # 1) Generar estructura base determinista
            estructura_base = generar_proyecto_base(
                nombre_proyecto=user_data.nombre,
                bloques_generables=[],
                descripcion_global=descripcion_global,
                tecnologias=user_data.tecnologias,
            )

            # 2) Generar diseño estructural (fuente única de verdad)
            diseno = generar_diseno_estructural(descripcion_global)

            # 3) Generar cada archivo por separado usando el diseño
            clases_schemas = generar_schemas_clases(diseno)
            services_code = generar_services_py(diseno)
            # Inyectamos también la arquitectura inferida dentro del diseño
            # para que el generador de endpoints tenga visibilidad de las rutas
            diseno_extendido = dict(diseno)
            if resultado.arquitectura:
                diseno_extendido["_architecture_text"] = resultado.arquitectura

            endpoints_code = generar_api_endpoints(diseno_extendido)

            # Validación estructural basada en AST del bloque endpoints
            import ast

            def validar_endpoints_ast(code: str) -> bool:
                try:
                    tree = ast.parse(code)
                except Exception:
                    return False

                tiene_endpoint = False

                for node in ast.walk(tree):
                    # No permitir imports
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        return False

                    # No permitir redefinición de router
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == "router":
                                return False

                    # Detectar funciones decoradas con router
                    if isinstance(node, ast.FunctionDef):
                        for dec in node.decorator_list:
                            if isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
                                if dec.value.id == "router":
                                    tiene_endpoint = True

                return tiene_endpoint

            if not validar_endpoints_ast(endpoints_code):
                print("[REINTENTO] Endpoints inválidos según AST, regenerando...")
                from scope_guardian.generador_artefactos import _llamar_modelo

                prompt_reforzado = f"""
Genera ÚNICAMENTE funciones de endpoints FastAPI.

REGLAS ESTRICTAS:
- Usa SOLO decoradores @router.get/post/put/delete
- NO generes imports
- NO redefinas router
- NO uses FastAPI()
- NO generes texto explicativo

Arquitectura:
{resultado.arquitectura}

Devuelve SOLO código Python entre:

### BEGIN_CODE
<codigo>
### END_CODE
"""
                salida = _llamar_modelo(prompt_reforzado, max_tokens=3000)
                import re
                match = re.search(r"### BEGIN_CODE(.*?)### END_CODE", salida, re.DOTALL)
                if match:
                    endpoints_code = match.group(1).strip()

            # Plantilla fija Pydantic (framework bloqueado)
            schemas_code = (
                "from pydantic import BaseModel\n\n"
                f"{clases_schemas}\n"
            )

            # Plantilla fija FastAPI (framework bloqueado)
            api_code = (
                "from fastapi import APIRouter, HTTPException\n"
                "from . import schemas\n"
                "from . import services\n\n"
                "router = APIRouter()\n\n"
                f"{endpoints_code}\n"
            )

            # 4) Reemplazar contenido dinámico en estructura base
            estructura_base["app/schemas.py"] = schemas_code
            estructura_base["app/services.py"] = services_code
            estructura_base["app/api.py"] = api_code

            # MAIN.PY DETERMINISTA (contrato explícito con router)
            estructura_base["main.py"] = (
                "from fastapi import FastAPI\n"
                "from app.api import router\n\n"
                "app = FastAPI()\n\n"
                "app.include_router(router)\n"
            )

            # requirements determinista para generación completa
            # (las dependencias externas en generación parcial van al README_MANUAL)

            # 5) Validación automática antes de materializar
            import ast

            def es_codigo_valido(code: str) -> tuple[bool, str]:
                try:
                    ast.parse(code)
                    return True, ""
                except Exception as e:
                    return False, str(e)

            # Validar archivos críticos
            archivos_criticos = {
                "schemas.py": schemas_code,
                "services.py": services_code,
                "api.py": api_code,
            }

            for nombre_archivo, contenido in archivos_criticos.items():
                valido, error = es_codigo_valido(contenido)

                # VALIDACIÓN SINTÁCTICA
                if not valido:
                    print(f"\n[VALIDACIÓN FALLIDA - SINTAXIS] {nombre_archivo}: {error}")
                    print("Intentando autocorrección...\n")

                    from scope_guardian.generador_artefactos import _llamar_modelo

                    prompt_fix = f"""
El siguiente archivo Python contiene un error de compilación.

ERROR:
{error}

CÓDIGO ACTUAL:
{contenido}

Corrige el archivo completo.
Devuelve SOLO código Python válido entre:

### BEGIN_CODE
<codigo>
### END_CODE
"""

                    salida_fix = _llamar_modelo(prompt_fix, max_tokens=3000)

                    import re
                    match = re.search(r"### BEGIN_CODE(.*?)### END_CODE", salida_fix, re.DOTALL)
                    if match:
                        contenido_corregido = match.group(1).strip()
                        valido2, error2 = es_codigo_valido(contenido_corregido)
                        if valido2:
                            contenido = contenido_corregido
                            print(f"[AUTOCORRECCIÓN EXITOSA] {nombre_archivo}\n")
                        else:
                            print(f"[ERROR PERSISTENTE] {nombre_archivo}: {error2}\n")

                # VALIDACIÓN ESTRUCTURAL ESPECÍFICA PARA api.py
                if nombre_archivo == "api.py":
                    max_intentos = 2
                    intento = 0

                    while intento < max_intentos:
                        errores_estructurales = []

                        if "router = APIRouter()" not in contenido:
                            errores_estructurales.append("No existe 'router = APIRouter()' a nivel módulo.")

                        if "def router(" in contenido:
                            errores_estructurales.append("Se redefinió 'router' como función.")

                        if "@router." not in contenido:
                            errores_estructurales.append("No hay endpoints decorados con @router.")

                        if "@app." in contenido:
                            errores_estructurales.append("Se usó @app en vez de @router.")

                        if not errores_estructurales:
                            break  # estructura correcta

                        print(f"\n[VALIDACIÓN FALLIDA - ESTRUCTURA] api.py (intento {intento+1}):")
                        for e in errores_estructurales:
                            print(f"- {e}")

                        print("Regenerando endpoints con prompt reforzado...\n")

                        # Regeneración reforzada
                        prompt_reforzado = f"""
Genera ÚNICAMENTE funciones de endpoints FastAPI.

REGLAS OBLIGATORIAS:
- Usa EXCLUSIVAMENTE @router.get/post/put/delete
- NO definas router
- NO uses @app
- NO crees FastAPI()
- router ya existe
- NO redefinas router como función

Diseño:
{diseno}

Devuelve SOLO código Python entre:

### BEGIN_CODE
<codigo>
### END_CODE
"""

                        from scope_guardian.generador_artefactos import _llamar_modelo
                        salida = _llamar_modelo(prompt_reforzado, max_tokens=3000)

                        import re
                        match = re.search(r"### BEGIN_CODE(.*?)### END_CODE", salida, re.DOTALL)
                        if match:
                            endpoints_code = match.group(1).strip()

                            contenido = (
                                "from fastapi import APIRouter, HTTPException\n"
                                "from . import schemas\n"
                                "from . import services\n\n"
                                "router = APIRouter()\n\n"
                                f"{endpoints_code}\n"
                            )

                        intento += 1

                archivos_criticos[nombre_archivo] = contenido

            # Actualizar con posibles correcciones
            estructura_base["app/schemas.py"] = archivos_criticos["schemas.py"]
            estructura_base["app/services.py"] = archivos_criticos["services.py"]
            estructura_base["app/api.py"] = archivos_criticos["api.py"]

            # 6) Materializar en disco
            output_dir = Path("output") / user_data.nombre
            output_dir.mkdir(parents=True, exist_ok=True)

            for ruta, contenido in estructura_base.items():
                ruta_completa = output_dir / ruta
                ruta_completa.parent.mkdir(parents=True, exist_ok=True)
                ruta_completa.write_text(contenido, encoding="utf-8")

            # README_FINAL (siempre generado también en modo COMPLETO)
            readme_final = f"# {user_data.nombre}\n\n"
            readme_final += "## Generación completa\n\n"
            readme_final += "Se ha generado el proyecto completo con validación sintáctica y autocorrección automática.\n\n"
            readme_final += "El proyecto está listo para ejecución y pruebas.\n"
            (output_dir / "README_FINAL.md").write_text(readme_final, encoding="utf-8")

            print("======================================")
            print("GENERACIÓN COMPLETA FINALIZADA")
            print("======================================\n")
            print(f"Proyecto generado en: output/{user_data.nombre}")
            print(f"Archivos creados: {len(estructura_base)}\n")
            print("La PoC ha sido generada utilizando validación + autocorrección.\n")

            return

        # --------------------------------------------------
        # MODO GENERACIÓN PARCIAL (mismo pipeline sin validación fuerte)
        # --------------------------------------------------
        if resultado.modo == ModoGeneracion.PARCIAL:
            print("\n=== MODO GENERACIÓN PARCIAL ACTIVADO ===\n")

            # 1) Estructura base
            estructura_base = generar_proyecto_base(
                nombre_proyecto=user_data.nombre,
                bloques_generables=[],
                descripcion_global=descripcion_global,
                tecnologias=user_data.tecnologias,
            )

            # 2) Diseño estructural
            diseno = generar_diseno_estructural(descripcion_global)

            # 3) Generación de código generable
            clases_schemas = generar_schemas_clases(diseno)
            services_code = generar_services_py(diseno)
            # Inyectamos también la arquitectura inferida dentro del diseño
            diseno_extendido = dict(diseno)
            if resultado.arquitectura:
                diseno_extendido["_architecture_text"] = resultado.arquitectura

            endpoints_code = generar_api_endpoints(diseno_extendido)

            # Validación estructural basada en AST del bloque endpoints
            import ast

            def validar_endpoints_ast(code: str) -> bool:
                try:
                    tree = ast.parse(code)
                except Exception:
                    return False

                tiene_endpoint = False

                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        return False

                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == "router":
                                return False

                    if isinstance(node, ast.FunctionDef):
                        for dec in node.decorator_list:
                            if isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
                                if dec.value.id == "router":
                                    tiene_endpoint = True

                return tiene_endpoint

            if not validar_endpoints_ast(endpoints_code):
                print("[REINTENTO] Endpoints inválidos según AST, regenerando...")
                from scope_guardian.generador_artefactos import _llamar_modelo

                prompt_reforzado = f"""
Genera ÚNICAMENTE funciones de endpoints FastAPI.

REGLAS ESTRICTAS:
- Usa SOLO decoradores @router.get/post/put/delete
- NO generes imports
- NO redefinas router
- NO uses FastAPI()
- NO generes texto explicativo

Arquitectura:
{resultado.arquitectura}

Devuelve SOLO código Python entre:

### BEGIN_CODE
<codigo>
### END_CODE
"""
                salida = _llamar_modelo(prompt_reforzado, max_tokens=3000)
                import re
                match = re.search(r"### BEGIN_CODE(.*?)### END_CODE", salida, re.DOTALL)
                if match:
                    endpoints_code = match.group(1).strip()

            schemas_code = (
                "from pydantic import BaseModel\n\n"
                f"{clases_schemas}\n"
            )

            api_code = (
                "from fastapi import APIRouter, HTTPException\n"
                "from . import schemas\n"
                "from . import services\n\n"
                "router = APIRouter()\n\n"
                f"{endpoints_code}\n"
            )

            estructura_base["app/schemas.py"] = schemas_code
            estructura_base["app/services.py"] = services_code
            estructura_base["app/api.py"] = api_code
            estructura_base["main.py"] = (
                "from fastapi import FastAPI\n"
                "from app.api import router\n\n"
                "app = FastAPI()\n\n"
                "app.include_router(router)\n"
            )

            # 4) Materializar
            output_dir = Path("output") / user_data.nombre
            output_dir.mkdir(parents=True, exist_ok=True)

            for ruta, contenido in estructura_base.items():
                ruta_completa = output_dir / ruta
                ruta_completa.parent.mkdir(parents=True, exist_ok=True)
                ruta_completa.write_text(contenido, encoding="utf-8")

            # 5) README_FINAL (siempre generado)
            readme_final = f"# {user_data.nombre}\n\n"
            readme_final += "## Generación parcial\n\n"
            readme_final += "Se ha generado todo el código que puede construirse automáticamente.\n\n"
            readme_final += "Revisar README_MANUAL para pasos externos pendientes.\n"
            (output_dir / "README_FINAL.md").write_text(readme_final, encoding="utf-8")

            # 6) README_MANUAL
            readme_manual = f"# Pasos manuales - {user_data.nombre}\n\n"
            readme_manual += "Configuración manual requerida para integraciones externas:\n\n"
            readme_manual += f"{user_data.tecnologias}\n\n"
            readme_manual += (
                "- Configuración de credenciales (Service Account / ADC)\n"
                "- Permisos e IAM\n"
                "- Variables de entorno necesarias\n"
                "- Despliegue en Cloud Run\n"
            )
            (output_dir / "README_MANUAL.md").write_text(readme_manual, encoding="utf-8")

            print("======================================")
            print("GENERACIÓN PARCIAL FINALIZADA")
            print("======================================\n")
            print(f"Proyecto generado en: output/{user_data.nombre}\n")

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
