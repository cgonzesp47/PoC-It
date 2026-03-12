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


def _limpiar_codigo_generado(codigo: str) -> str:
    """
    Limpia el código generado eliminando:
    - Explicaciones de texto sin comentar
    - Código duplicado en bloques
    - Bloques de notas sin #
    
    Args:
        codigo: Código Python generado
        
    Returns:
        str: Código limpio
    """
    import re
    
    # PASO 1: Eliminar bloques de texto conversacional al final
    # Detectar patrones de texto sin comentar
    patrones_texto_conversacional = [
        r'\n\s*\*\*NOTA[S]?:\*\*.*$',  # **NOTA:** o **NOTAS:**
        r'\n\s*NOTA[S]?:.*$',  # NOTAS: sin asteriscos
        r'\n\s*Aquí tienes.*$',  # "Aquí tienes el archivo..."
        r'\n\s*Estos? (modelo|código|archivo)s?.*$',  # "Estos modelos cubren..."
        r'\n\s*¡?Espero que.*$',  # "¡Espero que esto sea útil!"
        r'\n\s*Si necesitas.*$',  # "Si necesitas más ayuda..."
        r'\n\s*Asegúrate de.*$',  # "Asegúrate de ajustar..."
        r'\n\s*Este código.*$',  # "Este código es funcional..."
        r'\n\s*Reemplaza.*con los valores.*$',  # "Reemplaza X con los valores..."
    ]
    
    for patron in patrones_texto_conversacional:
        codigo = re.sub(patron, '', codigo, flags=re.DOTALL | re.MULTILINE)
    
    # PASO 2: Eliminar listas explicativas sin comentar (- item, 1. item, etc)
    lineas = codigo.split('\n')
    codigo_limpio_paso2 = []
    en_lista_sin_comentar = False
    
    for linea in lineas:
        linea_strip = linea.strip()
        
        # Detectar inicio de lista sin comentar
        if linea_strip and not linea_strip.startswith('#'):
            # Lista con guión
            if re.match(r'^-\s+\w', linea_strip):
                en_lista_sin_comentar = True
                continue
            # Lista numerada
            elif re.match(r'^\d+\.\s+\w', linea_strip):
                en_lista_sin_comentar = True
                continue
        
        # Si estamos en lista, verificar si continúa
        if en_lista_sin_comentar:
            # Si encuentra código real, salir del modo lista
            if (linea_strip.startswith(('from ', 'import ', 'def ', 'class ', '@', '#')) or
                linea_strip == '' or
                (linea_strip and not re.match(r'^[-\d]', linea_strip))):
                en_lista_sin_comentar = False
            else:
                continue  # Saltar línea de lista
        
        codigo_limpio_paso2.append(linea)
    
    codigo = '\n'.join(codigo_limpio_paso2)
    
    # PASO 3: Detectar y eliminar bloques de código COMPLETAMENTE duplicados
    lineas = codigo.split('\n')
    
    # Encontrar bloques de imports (señal de inicio de duplicación)
    indices_imports = []
    for i, linea in enumerate(lineas):
        linea_strip = linea.strip()
        if linea_strip.startswith('from ') or (linea_strip.startswith('import ') and not linea_strip.startswith('import ')):
            # Verificar que sea inicio de bloque (líneas previas vacías o es la primera)
            if i == 0 or not lineas[i-1].strip():
                indices_imports.append(i)
    
    # Si hay múltiples bloques de imports, probablemente hay duplicación
    if len(indices_imports) > 1:
        print(f"    [LIMPIEZA] Detectados {len(indices_imports)} bloques de imports, eliminando duplicados...")
        
        # Tomar solo el PRIMER bloque (antes del primer bloque de imports duplicado)
        primer_fin = indices_imports[1]
        # Buscar hacia atrás líneas vacías antes del segundo bloque
        while primer_fin > 0 and not lineas[primer_fin - 1].strip():
            primer_fin -= 1
        
        codigo = '\n'.join(lineas[:primer_fin])
    
    # PASO 4: Eliminar líneas vacías excesivas al final
    lineas = codigo.split('\n')
    while lineas and not lineas[-1].strip():
        lineas.pop()
    
    codigo = '\n'.join(lineas)
    
    # PASO 5: Verificar que no quede texto sin comentar al final
    lineas = codigo.split('\n')
    codigo_final = []
    
    for linea in lineas:
        linea_strip = linea.strip()
        
        # Si es línea de código Python válido o comentario, incluir
        if (not linea_strip or  # Línea vacía
            linea_strip.startswith('#') or  # Comentario
            linea_strip.startswith(('from ', 'import ', 'def ', 'class ', '@', 'async ', 'return ', 'if ', 'else:', 'elif ', 'for ', 'while ', 'try:', 'except', 'finally:', 'with ', 'yield', 'raise', 'assert', 'pass', 'break', 'continue')) or  # Palabras clave Python
            '=' in linea_strip or  # Asignaciones
            linea_strip.endswith((':',  ')', ']', '}', ',', '"', "'")) or  # Finales típicos
            linea_strip.startswith(('"', "'", '(', '[', '{')) or  # Inicios típicos
            re.match(r'^[a-zA-Z_]\w*\(', linea_strip)):  # Llamadas a función
            codigo_final.append(linea)
        else:
            # Línea sospechosa, omitir
            print(f"    [LIMPIEZA] Línea sospechosa eliminada: {linea_strip[:60]}...")
    
    return '\n'.join(codigo_final)


def generar_codigo_fallback(nombre_archivo: str, nombre_proyecto: str = "", objetivo: str = "") -> str:
    """
    Genera código fallback contextual para archivos con errores.
    
    Args:
        nombre_archivo: Nombre del archivo
        nombre_proyecto: Nombre del proyecto (para contexto)
        objetivo: Objetivo del proyecto (para detectar keywords)
        
    Returns:
        str: Código fallback apropiado según el contexto
    """
    
    # Fallback contextual para api.py
    if nombre_archivo == 'app/api.py' and objetivo:
        keywords_lower = objetivo.lower()
        
        # PoCs de autenticación/Google Drive/OAuth/Storage
        if any(word in keywords_lower for word in ['google drive', 'oauth', 'upload', 'file', 'storage', 'service account', 'cloud']):
            return '''from fastapi import APIRouter, UploadFile, File, HTTPException, status
from typing import List
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

uploaded_files = []

@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_file(file: UploadFile = File(...)):
    """Subir archivo"""
    try:
        file_info = {
            "id": len(uploaded_files) + 1,
            "filename": file.filename,
            "content_type": file.content_type,
            "status": "uploaded"
        }
        uploaded_files.append(file_info)
        logger.info(f"Archivo: {file.filename}")
        return file_info
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/files")
def list_files():
    """Listar archivos"""
    return {"files": uploaded_files, "total": len(uploaded_files)}

@router.get("/health")
def health_check():
    return {"status": "ok"}
'''
        
        # PoCs de préstamos/alquiler
        elif any(word in keywords_lower for word in ['préstamo', 'alquiler', 'rent', 'borrow', 'loan']):
            return '''from fastapi import APIRouter, HTTPException, status
from typing import List

router = APIRouter()

items_db = []
loans_db = []
item_counter = 0
loan_counter = 0

@router.post("/items", status_code=status.HTTP_201_CREATED)
def create_item(name: str, description: str = None):
    """Crear ítem"""
    global item_counter
    item_counter += 1
    item = {"id": item_counter, "name": name, "description": description, "available": True}
    items_db.append(item)
    return item

@router.get("/items")
def list_items(available: bool = None):
    """Listar ítems"""
    if available is not None:
        return [i for i in items_db if i["available"] == available]
    return items_db

@router.post("/loans", status_code=status.HTTP_201_CREATED)
def create_loan(item_id: int, user: str):
    """Crear préstamo"""
    global loan_counter
    item = next((i for i in items_db if i["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Ítem no encontrado")
    if not item["available"]:
        raise HTTPException(status_code=400, detail="No disponible")
    
    loan_counter += 1
    loan = {"id": loan_counter, "item_id": item_id, "user": user, "status": "active"}
    loans_db.append(loan)
    item["available"] = False
    return loan

@router.get("/loans")
def list_loans():
    """Listar préstamos"""
    return loans_db

@router.get("/health")
def health_check():
    return {"status": "ok"}
'''
    
    # Fallbacks estándar
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


def validar_y_corregir_archivos(nombre_proyecto: str, estructura: dict, modo_reintento: bool = True) -> Dict[str, str]:
    """
    Valida y corrige todos los archivos Python del proyecto.
    
    Args:
        nombre_proyecto: Nombre del proyecto
        estructura: Estructura de archivos del proyecto (debe incluir objetivo, funcionalidades, restricciones)
        modo_reintento: Si True, reintenta generar el archivo. Si False, usa fallback (por defecto True)
        
    Returns:
        Dict[str, str]: Reporte de validación por archivo
    """
    from sin_crewai.generador_codigo import generar_codigo_para_archivo
    
    directorio_base = f"output/{nombre_proyecto}"
    reporte = {}
    archivos_corregidos = 0
    MAX_REINTENTOS = 3  # Máximo de reintentos por archivo
    
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
            codigo_cortado = detectar_codigo_cortado(codigo)
            
            if not es_valido or codigo_cortado:
                motivo = f"Error de sintaxis: {error}" if not es_valido else "Código truncado"
                print(f"    [ERROR] {ruta_archivo}: {motivo}")
                
                if modo_reintento:
                    # Modo reintento: regenerar archivo hasta MAX_REINTENTOS
                    print(f"    [RETRY] Regenerando archivo...")
                    regenerado = False
                    
                    for intento in range(1, MAX_REINTENTOS + 1):
                        print(f"    [RETRY] Intento {intento}/{MAX_REINTENTOS}...")
                        
                        # Regenerar código
                        codigo_nuevo = generar_codigo_para_archivo(
                            nombre_proyecto,
                            ruta_archivo,
                            estructura.get('objetivo', ''),
                            estructura.get('funcionalidades', ''),
                            estructura.get('restricciones', ''),
                            estructura
                        )
                        
                        # Validar nuevo código
                        es_valido_nuevo, error_nuevo = validar_sintaxis_python(codigo_nuevo, ruta_archivo)
                        codigo_cortado_nuevo = detectar_codigo_cortado(codigo_nuevo)
                        
                        if es_valido_nuevo and not codigo_cortado_nuevo:
                            # Código válido, limpiar y guardar
                            codigo_limpio = _limpiar_codigo_generado(codigo_nuevo)
                            with open(ruta_completa, 'w', encoding='utf-8') as f:
                                f.write(codigo_limpio)
                            print(f"    [OK] {ruta_archivo} regenerado exitosamente")
                            reporte[ruta_archivo] = f"REGENERADO (intento {intento})"
                            archivos_corregidos += 1
                            regenerado = True
                            break
                        else:
                            motivo_fallo = error_nuevo if not es_valido_nuevo else "Código truncado"
                            print(f"    [WARN] Intento {intento} falló: {motivo_fallo}")
                    
                    if not regenerado:
                        # Tras MAX_REINTENTOS, usar fallback
                        print(f"    [FALLBACK] No se pudo regenerar, usando código fallback...")
                        codigo_fallback = generar_codigo_fallback(ruta_archivo, nombre_proyecto, estructura.get('objetivo', ''))
                        # Sobrescribir completamente el archivo
                        with open(ruta_completa, 'w', encoding='utf-8') as f:
                            f.write(codigo_fallback)
                        reporte[ruta_archivo] = f"FALLBACK (tras {MAX_REINTENTOS} intentos)"
                        archivos_corregidos += 1
                else:
                    # Modo fallback directo
                    print(f"    [FALLBACK] Aplicando código fallback...")
                    codigo_fallback = generar_codigo_fallback(ruta_archivo, nombre_proyecto, estructura.get('objetivo', ''))
                    # Sobrescribir completamente el archivo
                    with open(ruta_completa, 'w', encoding='utf-8') as f:
                        f.write(codigo_fallback)
                    reporte[ruta_archivo] = f"FALLBACK: {motivo}"
                    archivos_corregidos += 1
            
            else:
                print(f"    [OK] {ruta_archivo}")
                reporte[ruta_archivo] = "OK"
        
        except Exception as e:
            print(f"    [ERROR] {ruta_archivo}: Error al validar - {e}")
            reporte[ruta_archivo] = f"ERROR: {e}"
    
    if archivos_corregidos > 0:
        print(f"  [INFO] {archivos_corregidos} archivos corregidos")
    
    print(f"  [OK] Validación completada")
    
    return reporte
