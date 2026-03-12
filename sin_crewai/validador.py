"""
Módulo para validar el código Python generado.
Verifica sintaxis, imports y estructura básica.
"""
import ast
import os
import sys
from typing import Dict, List, Tuple


def validar_sintaxis_python(codigo: str, nombre_archivo: str) -> Tuple[bool, str]:
    """
    Valida que el código Python sea sintácticamente correcto.
    
    Args:
        codigo: Código Python a validar
        nombre_archivo: Nombre del archivo (para mensajes de error)
        
    Returns:
        Tuple[bool, str]: (es_valido, mensaje_error)
    """
    
    if not codigo.strip():
        return True, ""  # Archivos vacíos son válidos
    
    try:
        ast.parse(codigo)
        return True, ""
    except SyntaxError as e:
        mensaje = f"Error de sintaxis en línea {e.lineno}: {e.msg}"
        return False, mensaje
    except Exception as e:
        return False, f"Error al parsear: {str(e)}"


def detectar_codigo_cortado(codigo: str) -> bool:
    """
    Detecta si el código parece estar cortado a mitad.
    
    Args:
        codigo: Código Python a verificar
        
    Returns:
        bool: True si el código parece cortado
    """
    
    if not codigo.strip():
        return False
    
    lineas = codigo.strip().split('\n')
    ultima_linea = lineas[-1].strip()
    
    # Señales de código cortado
    señales_cortado = [
        ultima_linea.endswith(','),  # Lista/dict sin cerrar
        ultima_linea.endswith('('),  # Función sin cerrar
        ultima_linea.endswith('['),  # Lista sin cerrar
        ultima_linea.endswith('{'),  # Dict sin cerrar
        ultima_linea.endswith('\\'),  # Línea continuada
        'for ' in ultima_linea and ':' not in ultima_linea,  # For sin completar
        'if ' in ultima_linea and ':' not in ultima_linea,  # If sin completar
        'def ' in ultima_linea and ':' not in ultima_linea,  # Def sin completar
    ]
    
    return any(señales_cortado)


def generar_codigo_fallback(nombre_archivo: str) -> str:
    """
    Genera código fallback mínimo para archivos con errores.
    
    Args:
        nombre_archivo: Nombre del archivo
        
    Returns:
        str: Código fallback básico
    """
    
    fallbacks = {
        'main.py': '''from fastapi import FastAPI

app = FastAPI(title="API", version="1.0.0")

@app.get("/")
def read_root():
    return {"message": "API funcionando"}
''',
        'app/__init__.py': '# App package',
        'app/api.py': '''from fastapi import APIRouter

router = APIRouter()

@router.get("/items")
def list_items():
    return {"items": []}
''',
        'app/models.py': '''from pydantic import BaseModel

class Item(BaseModel):
    id: int
    name: str
''',
        'app/schemas.py': '''from pydantic import BaseModel

class ItemCreate(BaseModel):
    name: str

class ItemResponse(BaseModel):
    id: int
    name: str
''',
        'tests/__init__.py': '',
        'tests/test_api.py': '''from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_read_root():
    response = client.get("/")
    assert response.status_code == 200
''',
    }
    
    return fallbacks.get(nombre_archivo, f"# {nombre_archivo}\n# TODO: Implementar")


def validar_y_corregir_archivos(nombre_proyecto: str, estructura: dict) -> Dict[str, str]:
    """
    Valida y corrige todos los archivos Python del proyecto.
    
    Args:
        nombre_proyecto: Nombre del proyecto
        estructura: Estructura de archivos del proyecto
        
    Returns:
        Dict[str, str]: Reporte de validación por archivo
    """
    
    directorio_base = f"output/{nombre_proyecto}"
    reporte = {}
    archivos_corregidos = 0
    
    print(f"  > Validando código generado...")
    
    for ruta_archivo in estructura.keys():
        # Solo validar archivos Python
        if not ruta_archivo.endswith('.py'):
            continue
        
        ruta_completa = os.path.join(directorio_base, ruta_archivo)
        
        try:
            with open(ruta_completa, 'r', encoding='utf-8') as f:
                codigo = f.read()
            
            # Validar sintaxis
            es_valido, error = validar_sintaxis_python(codigo, ruta_archivo)
            
            if not es_valido:
                print(f"    [ERROR] {ruta_archivo}: {error}")
                print(f"    [FIX] Aplicando código fallback...")
                
                # Generar y guardar código fallback
                codigo_fallback = generar_codigo_fallback(ruta_archivo)
                with open(ruta_completa, 'w', encoding='utf-8') as f:
                    f.write(codigo_fallback)
                
                reporte[ruta_archivo] = f"CORREGIDO: {error}"
                archivos_corregidos += 1
            
            # Detectar código cortado
            elif detectar_codigo_cortado(codigo):
                print(f"    [WARNING] {ruta_archivo}: Código parece cortado")
                print(f"    [FIX] Aplicando código fallback...")
                
                codigo_fallback = generar_codigo_fallback(ruta_archivo)
                with open(ruta_completa, 'w', encoding='utf-8') as f:
                    f.write(codigo_fallback)
                
                reporte[ruta_archivo] = "CORREGIDO: Código cortado"
                archivos_corregidos += 1
            
            else:
                print(f"    [OK] {ruta_archivo}")
                reporte[ruta_archivo] = "OK"
        
        except Exception as e:
            print(f"    [ERROR] {ruta_archivo}: Error al validar - {e}")
            reporte[ruta_archivo] = f"ERROR: {e}"
    
    if archivos_corregidos > 0:
        print(f"  [INFO] {archivos_corregidos} archivos corregidos con código fallback")
    
    print(f"  [OK] Validación completada")
    
    return reporte
