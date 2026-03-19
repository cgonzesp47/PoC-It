"""
Módulo para validar y corregir imports de schemas entre api.py y schemas.py.
Soluciona el problema de inconsistencia de nombres de schemas.
"""
import ast
import os
import re
from typing import List, Tuple, Dict, Optional
from difflib import SequenceMatcher


def extraer_clases_pydantic(schemas_path: str) -> List[str]:
    """
    Extrae nombres de clases Pydantic definidas en schemas.py.
    
    Args:
        schemas_path: Ruta al archivo schemas.py
        
    Returns:
        List[str]: Lista de nombres de clases Pydantic encontradas
    """
    try:
        with open(schemas_path, 'r', encoding='utf-8') as f:
            codigo = f.read()
        
        tree = ast.parse(codigo)
        clases = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                # Verificar si hereda de BaseModel (indicador de Pydantic)
                for base in node.bases:
                    if isinstance(base, ast.Name) and 'BaseModel' in base.id:
                        clases.append(node.name)
                        break
        
        return clases
    
    except Exception as e:
        print(f"    [WARN] Error al extraer clases de {schemas_path}: {e}")
        return []


def extraer_imports_desde_schemas(api_path: str) -> List[str]:
    """
    Extrae nombres importados desde app.schemas en api.py.
    
    Args:
        api_path: Ruta al archivo api.py
        
    Returns:
        List[str]: Lista de nombres importados desde app.schemas
    """
    try:
        with open(api_path, 'r', encoding='utf-8') as f:
            codigo = f.read()
        
        tree = ast.parse(codigo)
        imports = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == 'app.schemas':
                    for alias in node.names:
                        if alias.name != '*':  # Ignorar import *
                            imports.append(alias.name)
        
        return imports
    
    except Exception as e:
        print(f"    [WARN] Error al extraer imports de {api_path}: {e}")
        return []


def validar_imports_schemas(api_path: str, schemas_path: str) -> Tuple[bool, List[str], Dict[str, str]]:
    """
    Valida que los imports de schemas en api.py existan en schemas.py.
    
    Args:
        api_path: Ruta al archivo api.py
        schemas_path: Ruta al archivo schemas.py
        
    Returns:
        Tuple[bool, List[str], Dict[str, str]]: 
            - es_valido: True si todos los imports existen
            - errores: Lista de mensajes de error
            - sugerencias: Mapeo de imports incorrectos a sugerencias
    """
    imports_api = extraer_imports_desde_schemas(api_path)
    clases_schemas = extraer_clases_pydantic(schemas_path)
    
    if not imports_api:
        # No hay imports, probablemente archivo vacío o sin schemas
        return True, [], {}
    
    if not clases_schemas:
        # schemas.py no tiene clases, error crítico
        return False, ["schemas.py no contiene clases Pydantic"], {}
    
    errores = []
    sugerencias = {}
    
    for import_name in imports_api:
        if import_name not in clases_schemas:
            # Import incorrecto, buscar sugerencia
            mejor_match = encontrar_nombre_similar(import_name, clases_schemas)
            errores.append(f"Import '{import_name}' no existe en schemas.py")
            if mejor_match:
                sugerencias[import_name] = mejor_match
    
    es_valido = len(errores) == 0
    return es_valido, errores, sugerencias


def encontrar_nombre_similar(nombre: str, opciones: List[str], threshold: float = 0.6) -> Optional[str]:
    """
    Encuentra el nombre más similar usando algoritmo de similaridad.
    
    Args:
        nombre: Nombre a buscar
        opciones: Lista de nombres válidos
        threshold: Umbral de similaridad (0.0 a 1.0)
        
    Returns:
        Optional[str]: Nombre más similar o None si no hay match
    """
    mejor_score = 0.0
    mejor_match = None
    
    for opcion in opciones:
        # Calcular similaridad
        score = SequenceMatcher(None, nombre.lower(), opcion.lower()).ratio()
        
        # Bonificación si comparten sufijos comunes
        sufijos_comunes = ['Request', 'Response', 'Create', 'Update', 'Schema', 'Model', 'Data']
        for sufijo in sufijos_comunes:
            if nombre.endswith(sufijo) and opcion.endswith(sufijo):
                score += 0.2
            elif nombre.endswith(sufijo) or opcion.endswith(sufijo):
                score += 0.1
        
        # Bonificación si comparten prefijos comunes
        if nombre[:4].lower() == opcion[:4].lower():
            score += 0.1
        
        if score > mejor_score:
            mejor_score = score
            mejor_match = opcion
    
    return mejor_match if mejor_score >= threshold else None


def corregir_imports_automaticamente(
    api_path: str,
    mapeo: Dict[str, str]
) -> bool:
    """
    Corrige automáticamente los imports incorrectos en api.py.
    
    Args:
        api_path: Ruta al archivo api.py
        mapeo: Diccionario {nombre_incorrecto: nombre_correcto}
        
    Returns:
        bool: True si se aplicaron correcciones
    """
    try:
        with open(api_path, 'r', encoding='utf-8') as f:
            codigo = f.read()
        
        codigo_original = codigo
        correcciones_aplicadas = []
        
        # Corregir en la línea de import
        for incorrecto, correcto in mapeo.items():
            # Patrón para encontrar el import específico
            patron_import = rf'\bfrom\s+app\.schemas\s+import\s+([^;\n]*\b{re.escape(incorrecto)}\b[^;\n]*)'
            
            def reemplazar_en_import(match):
                linea_import = match.group(1)
                # Reemplazar solo la palabra completa
                nueva_linea = re.sub(rf'\b{re.escape(incorrecto)}\b', correcto, linea_import)
                return f'from app.schemas import {nueva_linea}'
            
            codigo_nuevo = re.sub(patron_import, reemplazar_en_import, codigo)
            
            if codigo_nuevo != codigo:
                correcciones_aplicadas.append(f"{incorrecto} → {correcto}")
                codigo = codigo_nuevo
        
        # Corregir en el uso dentro del código
        for incorrecto, correcto in mapeo.items():
            # Buscar usos como type hints, parámetros, etc.
            # Usar word boundary para evitar reemplazos parciales
            codigo = re.sub(rf'\b{re.escape(incorrecto)}\b', correcto, codigo)
        
        if codigo != codigo_original:
            # Guardar archivo corregido
            with open(api_path, 'w', encoding='utf-8') as f:
                f.write(codigo)
            
            print(f"    [AUTO-FIX] Imports corregidos en api.py:")
            for correccion in correcciones_aplicadas:
                print(f"      • {correccion}")
            
            return True
        
        return False
    
    except Exception as e:
        print(f"    [ERROR] Error al corregir imports: {e}")
        return False


def validar_y_corregir_imports_post_generacion(
    nombre_proyecto: str,
    intentar_correccion: bool = True
) -> bool:
    """
    Valida y opcionalmente corrige imports después de generar código.
    
    Args:
        nombre_proyecto: Nombre del proyecto
        intentar_correccion: Si True, intenta auto-corregir errores
        
    Returns:
        bool: True si no hay errores o se corrigieron exitosamente
    """
    directorio_base = f"output/{nombre_proyecto}"
    api_path = os.path.join(directorio_base, "app/api.py")
    schemas_path = os.path.join(directorio_base, "app/schemas.py")
    
    # Verificar que ambos archivos existan
    if not os.path.exists(api_path) or not os.path.exists(schemas_path):
        return True  # No se puede validar, asumir OK
    
    print(f"  > Validando consistencia de imports entre api.py y schemas.py...")
    
    es_valido, errores, sugerencias = validar_imports_schemas(api_path, schemas_path)
    
    if es_valido:
        print(f"    [OK] Imports consistentes")
        return True
    
    # Hay errores
    print(f"    [ERROR] Detectados {len(errores)} imports inconsistentes:")
    for error in errores:
        print(f"      • {error}")
    
    if intentar_correccion and sugerencias:
        print(f"    [AUTO-FIX] Intentando auto-corrección...")
        exito = corregir_imports_automaticamente(api_path, sugerencias)
        
        if exito:
            # Re-validar después de corrección
            es_valido_post, errores_post, _ = validar_imports_schemas(api_path, schemas_path)
            
            if es_valido_post:
                print(f"    [OK] Auto-corrección exitosa")
                return True
            else:
                print(f"    [WARN] Auto-corrección parcial, quedan {len(errores_post)} errores")
                return False
        else:
            print(f"    [WARN] No se pudo aplicar auto-corrección")
            return False
    
    return False
