"""
Generador de PoC Backend - Versión sin CrewAI
Orquestación directa con llamadas a Ollama

Este archivo reemplaza la implementación con agentes de CrewAI
por una solución más simple y rápida con llamadas directas al LLM
"""
import time
from sin_crewai.generador_readme import generar_readme_basico, guardar_readme
from sin_crewai.generador_estructura import generar_estructura_json, añadir_json_a_readme
from sin_crewai.materializador import materializar_estructura
from sin_crewai.generador_codigo import rellenar_archivos_con_codigo
from sin_crewai.validador import validar_y_corregir_archivos
from sin_crewai.integrador_git import inicializar_git


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
    readme_contenido = generar_readme_basico(nombre, objetivo, actores, funcionalidades, restricciones)
    ruta_readme = guardar_readme(nombre, readme_contenido)
    print("[OK] Fase 1 completada\n")
    
    # FASE 2: Generar estructura JSON
    print("[FASE 2] Disenando estructura del proyecto...")
    estructura = generar_estructura_json(nombre, objetivo, funcionalidades, restricciones)
    añadir_json_a_readme(ruta_readme, estructura)
    print("[OK] Fase 2 completada\n")
    
    # FASE 3: Materializar estructura (esqueletos vacíos)
    print("[FASE 3] Creando archivos del proyecto...")
    archivos_creados = materializar_estructura(nombre, estructura)
    print("[OK] Fase 3 completada\n")
    
    # FASE 4: Rellenar archivos con código funcional
    print("[FASE 4] Generando codigo funcional...")
    archivos_rellenados = rellenar_archivos_con_codigo(nombre, objetivo, funcionalidades, restricciones, estructura, tecnologias)
    print("[OK] Fase 4 completada\n")
    
    # FASE 5: Validar y corregir código
    print("[FASE 5] Validando codigo generado...")
    # Agregar contexto completo a estructura para reintentos y fallbacks inteligentes
    estructura_con_contexto = {
        **estructura,
        'objetivo': objetivo,
        'funcionalidades': funcionalidades,
        'restricciones': restricciones,
        'tecnologias': tecnologias
    }
    reporte_validacion = validar_y_corregir_archivos(nombre, estructura_con_contexto, modo_reintento=True)
    print("[OK] Fase 5 completada\n")
    
    # FASE 6: Inicializar Git
    print("[FASE 6] Inicializando control de versiones...")
    git_ok = inicializar_git(nombre)
    if git_ok:
        print("[OK] Fase 6 completada\n")
    else:
        print("[WARNING] Fase 6 con errores (Git no disponible o fallo)\n")
    
    return {
        'nombre': nombre,
        'readme': ruta_readme,
        'archivos': archivos_creados,
        'validacion': reporte_validacion,
        'git': git_ok
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
