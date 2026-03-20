"""
Generador de PoC Backend - Versión sin CrewAI
Orquestación directa con llamadas a Ollama

Este archivo reemplaza la implementación con agentes de CrewAI
por una solución más simple y rápida con llamadas directas al LLM
"""
import time
from sin_crewai.generador_readme import generar_readme_basico, guardar_readme, actualizar_readme_con_endpoints
from sin_crewai.generador_estructura import generar_estructura_json, añadir_json_a_readme
from sin_crewai.materializador import materializar_estructura
from sin_crewai.generador_codigo import rellenar_archivos_con_codigo
#from sin_crewai.validador import validar_y_corregir_archivos
from sin_crewai.validador_imports import validar_y_corregir_imports_post_generacion
from sin_crewai.integrador_git import inicializar_git

#  NUEVA ARQUITECTURA V2 
from sin_crewai.generador_v2_diseno import (
    generar_diseno_estructurado,
    generar_schemas_desde_diseno,
    generar_models_desde_diseno,
    generar_api_desde_diseno,
    generar_tests_desde_diseno,
    generar_main_desde_diseno,
    guardar_diseno
)


def generar_poc(nombre: str, objetivo: str, actores: str, funcionalidades: str, restricciones: str, tecnologias: str = "ninguna"):
    """
    Genera una PoC completa: README + Estructura + Git
    
    Args:
        nombre: Nombre de la PoC
        objetivo: Qué problema resuelve
        actores: Quién utilizará el sistema
        funcionalidades: Qué debería hacer el sistema
        restricciones: Reglas o límites importantes
        tecnologias: Tecnologías/integraciones necesarias (separadas por comas)
    """
    
    print(f"\n{'='*60}")
    print(f"  GENERANDO PoC: {nombre}")
    print(f"{'='*60}\n")
    
    # FASE 1: Generar README básico
    print("[FASE 1] Generando README basico...")
    readme_contenido = generar_readme_basico(nombre, objetivo, actores, funcionalidades, restricciones, tecnologias)
    ruta_readme = guardar_readme(nombre, readme_contenido)
    print("[OK] Fase 1 completada\n")
    
    # FASE 2: Generar estructura JSON
    print("[FASE 2] Disenando estructura del proyecto...")
    estructura = generar_estructura_json(nombre, objetivo, funcionalidades, restricciones)
    # Evitamos añadir el JSON completo al README para no duplicar secciones
    print("[OK] Fase 2 completada\n")
    
    # FASE 3: Materializar estructura (esqueletos vacíos)
    print("[FASE 3] Creando archivos del proyecto...")
    archivos_creados = materializar_estructura(nombre, estructura)
    print("[OK] Fase 3 completada\n")
    
    # FASE 4 V2: Generación basada en diseño estructurado

    print("[FASE 4 - V2] Generando codigo funcional (arquitectura JSON)...")

    design = generar_diseno_estructurado(nombre, objetivo, funcionalidades, restricciones, tecnologias)
    guardar_diseno(nombre, design)

    directorio_base = f"output/{nombre}"

    #l Generación determinista
    schemas_code = generar_schemas_desde_diseno(design)
    with open(f"{directorio_base}/app/schemas.py", "w", encoding="utf-8") as f:
        f.write(schemas_code)

    models_code = generar_models_desde_diseno(design)
    with open(f"{directorio_base}/app/models.py", "w", encoding="utf-8") as f:
        f.write(models_code)

    api_code = generar_api_desde_diseno(nombre, design)
    with open(f"{directorio_base}/app/api.py", "w", encoding="utf-8") as f:
        f.write(api_code)

    # Generar tests usando LLM
    tests_code = generar_tests_desde_diseno(nombre, design)
    with open(f"{directorio_base}/tests/test_api.py", "w", encoding="utf-8") as f:
        f.write(tests_code)

    # Generar main.py determinista
    main_code = generar_main_desde_diseno(nombre, design, tecnologias)
    with open(f"{directorio_base}/main.py", "w", encoding="utf-8") as f:
        f.write(main_code)

    print("[OK] Fase 4 V2 completada\n")
    
    # FASE 4.5: Actualizar README con endpoints reales
    print("[FASE 4.5] Documentando endpoints generados...")
    actualizar_readme_con_endpoints(nombre, tecnologias)
    print("[OK] Fase 4.5 completada\n")
    
    # FASE 5 – RESET LIMPIO (sin validaciones complejas)
    print("[FASE 5] Verificación mínima de ejecución...")

    try:
        # Solo comprobamos que la app se pueda importar
        import importlib.util
        import os

        ruta_proyecto = os.path.abspath(f"output/{nombre}")
        ruta_main = os.path.join(ruta_proyecto, "main.py")

        # Añadimos el directorio del proyecto al sys.path
        import sys
        if ruta_proyecto not in sys.path:
            sys.path.insert(0, ruta_proyecto)

        spec = importlib.util.spec_from_file_location("main", ruta_main)

        if spec is None or spec.loader is None:
            raise RuntimeError("No se pudo crear ModuleSpec para main.py")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        print("[OK] Import de main.py correcto\n")

    except Exception as e:
        raise RuntimeError(
            f"Error al importar la aplicación generada: {e}"
        )

    print("[OK] Fase 5 completada (modo simplificado)\n")

    # FASE 5.5: Validar y corregir imports de schemas
    print("[FASE 5.5] Validando consistencia de imports...")
    imports_ok = validar_y_corregir_imports_post_generacion(nombre, intentar_correccion=True)
    if imports_ok:
        print("[OK] Fase 5.5 completada\n")
    else:
        print("[WARNING] Fase 5.5 con inconsistencias en imports\n")
    
    # FASE 7 – Self-Repair guiado por tests

    print("[FASE 7] Ejecutando tests y aplicando self-repair si es necesario...")

    import subprocess
    import ollama

    directorio_proyecto = f"output/{nombre}"
    max_iteraciones = 3
    iteracion = 0
    tests_ok = False

    while iteracion < max_iteraciones:
        print(f"  > Ejecutando pytest (iteración {iteracion + 1})...")
        resultado = subprocess.run(
            ["pytest", "-q"],
            cwd=directorio_proyecto,
            capture_output=True,
            text=True
        )

        if resultado.returncode == 0:
            print("  [OK] Todos los tests pasan correctamente.\n")
            tests_ok = True
            break

        print("  [INFO] Tests fallidos detectados. Iniciando reparación...")

        stdout_completo = (resultado.stdout or "") + "\n" + (resultado.stderr or "")

        with open(f"{directorio_proyecto}/app/api.py", "r", encoding="utf-8") as f:
            codigo_api = f.read()

        with open(f"{directorio_proyecto}/tests/test_api.py", "r", encoding="utf-8") as f:
            codigo_tests = f.read()

        prompt_reparacion = f"""
El siguiente código FastAPI NO pasa los tests.

API ACTUAL:
{codigo_api}

TESTS:
{codigo_tests}

SALIDA COMPLETA DE PYTEST:
{stdout_completo}

IMPORTANTE:
- NO regeneres el archivo completo.
- NO repitas imports.
- NO repitas router.
- Devuelve SOLO las funciones que deben modificarse.
- No incluyas nada más.

Formato obligatorio de respuesta:

# === PATCH START ===
def nombre_funcion(...):
    ...
# === PATCH END ===
"""

        response = ollama.chat(
            model="qwen7b:latest",
            messages=[{"role": "user", "content": prompt_reparacion}],
            options={"temperature": 0.1, "num_predict": 1500}
        )

        nuevo_codigo = response["message"]["content"]
        nuevo_codigo = nuevo_codigo.replace("```python", "").replace("```", "").strip()

        # Extraer solo bloque PATCH
        import re

        match = re.search(
            r"# === PATCH START ===(.*?)# === PATCH END ===",
            nuevo_codigo,
            re.DOTALL
        )

        if match:
            patch_code = match.group(1).strip()
        else:
            patch_code = ""

        # VALIDACIÓN ESTRICTA DE PATCH
        patch_valido = True

        # Debe contener al menos una función
        if not re.search(r"def\s+\w+\s*\(", patch_code):
            patch_valido = False

        # No debe contener imports ni router (protección contra regeneración completa)
        if re.search(r"^\s*(from|import)\s+", patch_code, re.MULTILINE):
            patch_valido = False

        if "router =" in patch_code or "@router." in patch_code:
            patch_valido = False

        # Si el patch no es válido, NO tocar el archivo y reintentar
        if not patch_valido:
            print("  [WARNING] PATCH inválido detectado. Reintentando...")
            iteracion += 1
            continue

        # APLICAR PATCH SEGURO
        funciones_original = re.split(r"\n(?=def\s+\w+\s*\()", codigo_api)
        mapa_funciones = {}

        for bloque in funciones_original:
            match_func = re.match(r"\s*def\s+(\w+)\s*\(", bloque)
            if match_func:
                mapa_funciones[match_func.group(1)] = bloque
            else:
                # Encabezado (imports, router, variables globales)
                mapa_funciones.setdefault("__header__", "")
                mapa_funciones["__header__"] += bloque

        funciones_patch = re.split(r"\n(?=def\s+\w+\s*\()", patch_code)

        for bloque in funciones_patch:
            match_func = re.match(r"\s*def\s+(\w+)\s*\(", bloque)
            if match_func:
                mapa_funciones[match_func.group(1)] = bloque

        codigo_final = mapa_funciones.get("__header__", "")
        for nombre_func, contenido in mapa_funciones.items():
            if nombre_func != "__header__":
                codigo_final += "\n" + contenido.strip() + "\n"

        with open(f"{directorio_proyecto}/app/api.py", "w", encoding="utf-8") as f:
            f.write(codigo_final.strip() + "\n")

        iteracion += 1

    if not tests_ok:
        print("  [WARNING] No se lograron pasar todos los tests tras las iteraciones permitidas.\n")
    else:
        print("[OK] Fase 7 completada\n")

    # FASE 8: Inicializar Git
    print("[FASE 8] Inicializando control de versiones...")
    git_ok = inicializar_git(nombre)
    if git_ok:
        print("[OK] Fase 8 completada\n")
    else:
        print("[WARNING] Fase 8 con errores (Git no disponible o fallo)\n")

    return {
        'nombre': nombre,
        'readme': ruta_readme,
        'archivos': archivos_creados,
        'git': git_ok,
        'tests_ok': tests_ok
    }


def main():
    """Punto de entrada principal del programa."""
    
    tiempo_inicio = time.time()
    
    print("\n" + "="*60)
    print("  GENERADOR DE PoC BACKEND - FastAPI")
    print("  Version: Sin CrewAI (Llamadas directas a Ollama)")
    print("="*60)
    
    # Recoger datos del usuario
    print("\nPor favor, completa la siguiente informacion:\n")
    
    nombre = input("1. Nombre de la PoC: ").strip()
    if not nombre:
        print("[ERROR] El nombre no puede estar vacio")
        return
    
    objetivo = input("2. ¿Qué problema resuelve?: ").strip()
    actores = input("3. ¿Quién utilizará el sistema?: ").strip()
    funcionalidades = input("4. ¿Qué debería poder hacer el sistema?: ").strip()
    restricciones = input("5. ¿Hay reglas o límites importantes?: ").strip()
    
    print("6. ¿Qué tecnologías/integraciones necesita? (separadas por comas)")
    print("   Ejemplos: google-drive, aws-s3, mongodb, postgresql, ninguna")
    tecnologias = input("   > ").strip()
    if not tecnologias:
        tecnologias = "ninguna"
    
    # Generar PoC
    try:
        resultado = generar_poc(nombre, objetivo, actores, funcionalidades, restricciones, tecnologias)
        
        # Calcular tiempo
        tiempo_final = time.time()
        duracion = tiempo_final - tiempo_inicio
        minutos = int(duracion // 60)
        segundos = int(duracion % 60)
        
        # Mostrar resultado
        print("\n" + "="*60)
        print("  PoC GENERADA EXITOSAMENTE")
        print("="*60)
        print(f"\nDirectorio: output/{resultado['nombre']}/")
        print(f"README: {resultado['readme']}")
        print(f"Archivos creados: {len(resultado['archivos'])}")
        for archivo in resultado['archivos']:
            print(f"   - {archivo}")
        print(f"Git inicializado: {'Si' if resultado['git'] else 'No'}")
        print(f"\nTiempo total: {minutos}m {segundos}s ({duracion:.2f}s)")
        print("\n" + "="*60)
        print("  Listo para usar")
        print("="*60)
        print(f"\nProximos pasos:")
        print(f"   cd output/{nombre}")
        print(f"   python -m venv venv")
        print(f"   venv\\Scripts\\activate")
        print(f"   pip install -r requirements.txt")
        print(f"   uvicorn main:app --reload")
        print()
        
    except KeyboardInterrupt:
        print("\n\n[CANCEL] Proceso cancelado por el usuario")
    except Exception as e:
        print(f"\n\n[ERROR] Error durante la generacion: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
