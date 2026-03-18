"""
Módulo para materializar la estructura de archivos del proyecto.
Crea físicamente los archivos definidos en el JSON.
"""
import os
import json


def materializar_estructura(nombre_proyecto: str, estructura: dict) -> list:
    """
    Crea físicamente los archivos definidos en la estructura JSON.
    
    Args:
        nombre_proyecto: Nombre del proyecto
        estructura: Diccionario con la estructura {archivo: contenido}
        
    Returns:
        list: Lista de archivos creados
    """
    
    directorio_base = f"output/{nombre_proyecto}"
    archivos_creados = []
    
    print(f"  > Materializando estructura en {directorio_base}...")
    
    for ruta_archivo, contenido in estructura.items():
        # Ruta completa del archivo
        ruta_completa = os.path.join(directorio_base, ruta_archivo)
        
        # Crear directorios si es necesario
        directorio = os.path.dirname(ruta_completa)
        if directorio and directorio != directorio_base:
            os.makedirs(directorio, exist_ok=True)
        
        # Escribir archivo
        try:
            with open(ruta_completa, 'w', encoding='utf-8') as f:
                f.write(contenido)
            archivos_creados.append(ruta_archivo)
            print(f"    [OK] {ruta_archivo}")
        except Exception as e:
            print(f"    [ERROR] Error al crear {ruta_archivo}: {e}")
    
    print(f"  [OK] {len(archivos_creados)} archivos creados")
    
    return archivos_creados


def extraer_estructura_de_readme(ruta_readme: str) -> dict | None:
    """
    Extrae el JSON de estructura del README.
    
    Args:
        ruta_readme: Ruta del archivo README.md
        
    Returns:
        dict | None: Estructura extraída o None si no se encuentra
    """
    
    with open(ruta_readme, 'r', encoding='utf-8') as f:
        contenido = f.read()
    
    # Buscar la sección JSON
    inicio = contenido.find('## ESTRUCTURA_JSON_AUTOGENERADA')
    if inicio == -1:
        print("  [ERROR] No se encontró sección JSON en README")
        return None
    
    # Extraer el bloque JSON
    inicio_json = contenido.find('```json', inicio)
    fin_json = contenido.find('```', inicio_json + 7)
    
    if inicio_json == -1 or fin_json == -1:
        print("  [ERROR] No se encontró bloque JSON válido")
        return None
    
    json_str = contenido[inicio_json + 7:fin_json].strip()
    
    try:
        estructura = json.loads(json_str)
        print(f"  [OK] Estructura JSON extraída ({len(estructura)} archivos)")
        return estructura
    except json.JSONDecodeError as e:
        print(f"  [ERROR] Error al parsear JSON: {e}")
        return None
