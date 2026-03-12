"""
Módulo para generar código funcional en archivos ya creados.
Rellena los esqueletos con implementaciones completas.
"""
import ollama
import os


def generar_codigo_para_archivo(
    nombre_proyecto: str,
    ruta_archivo: str,
    objetivo: str,
    funcionalidades: str,
    restricciones: str,
    estructura_completa: dict
) -> str:
    """
    Genera código funcional para un archivo específico.
    
    Args:
        nombre_proyecto: Nombre de la PoC
        ruta_archivo: Ruta del archivo (ej: "app/api.py")
        objetivo: Qué problema resuelve el proyecto
        funcionalidades: Funcionalidades del sistema
        restricciones: Restricciones del sistema
        estructura_completa: Estructura completa del proyecto para contexto
        
    Returns:
        str: Código funcional generado
    """
    
    # Archivos que no necesitan código complejo
    archivos_simples = {
        'tests/__init__.py': '',
        'app/__init__.py': 'from app.api import router',
        '.gitignore': '__pycache__/\n*.pyc\n*.pyo\n*.pyd\n.Python\nvenv/\n.env\n*.log\n.pytest_cache/\n*.db',
        'requirements.txt': 'fastapi>=0.104.0\nuvicorn[standard]>=0.24.0\npydantic>=2.0.0\npytest>=7.4.0\nhttpx>=0.25.0'
    }
    
    if ruta_archivo in archivos_simples:
        return archivos_simples[ruta_archivo]
    
    # Archivos que necesitan generación con IA
    prompts = {
        'main.py': _get_prompt_main(nombre_proyecto),
        'app/models.py': _get_prompt_models(nombre_proyecto, objetivo, funcionalidades),
        'app/schemas.py': _get_prompt_schemas(nombre_proyecto, objetivo, funcionalidades),
        'app/api.py': _get_prompt_api(nombre_proyecto, objetivo, funcionalidades, restricciones),
        'tests/test_api.py': _get_prompt_tests(nombre_proyecto, funcionalidades)
    }
    
    if ruta_archivo not in prompts:
        return f"# {ruta_archivo}\n# TODO: Implementar"
    
    prompt = prompts[ruta_archivo]
    
    print(f"    > Generando código para {ruta_archivo}...")
    
    response = ollama.chat(
        model='qwen7b:latest',
        messages=[{
            'role': 'user',
            'content': prompt
        }],
        options={
            'temperature': 0.2,
            'num_predict': 800,
        }
    )
    
    codigo = response['message']['content'].strip()
    
    # Limpiar si viene con markdown
    if codigo.startswith('```python'):
        codigo = codigo.replace('```python', '').replace('```', '').strip()
    elif codigo.startswith('```'):
        codigo = codigo.replace('```', '').strip()
    
    return codigo


def _get_prompt_main(nombre: str) -> str:
    return f"""Genera el archivo main.py para una API FastAPI llamada {nombre}.

Requisitos:
- Importar FastAPI
- Importar router desde app.api
- Crear instancia de FastAPI con título y versión
- Incluir el router
- Añadir endpoint raíz GET / que retorne mensaje de bienvenida

Responde SOLO el código Python, sin explicaciones:"""


def _get_prompt_models(nombre: str, objetivo: str, funcionalidades: str) -> str:
    return f"""Genera el archivo app/models.py con modelos Pydantic para esta API:

PROYECTO: {nombre}
PROPÓSITO: {objetivo}
FUNCIONALIDADES: {funcionalidades}

Requisitos:
- Usa Pydantic BaseModel (NO SQLAlchemy)
- Crea modelos para las entidades principales del dominio
- Incluye todos los campos necesarios con tipos correctos
- Usa Optional para campos opcionales
- Añade ejemplos en Field() si es útil

Responde SOLO el código Python, sin explicaciones:"""


def _get_prompt_schemas(nombre: str, objetivo: str, funcionalidades: str) -> str:
    return f"""Genera el archivo app/schemas.py con schemas request/response para esta API:

PROYECTO: {nombre}
PROPÓSITO: {objetivo}
FUNCIONALIDADES: {funcionalidades}

Requisitos:
- Schemas para crear entidades (sin id)
- Schemas para actualizar entidades (campos opcionales)
- Schemas para respuestas (con id y campos completos)
- Usa Pydantic BaseModel
- Hereda de los modelos cuando sea apropiado

Responde SOLO el código Python, sin explicaciones:"""


def _get_prompt_api(nombre: str, objetivo: str, funcionalidades: str, restricciones: str) -> str:
    return f"""Genera el archivo app/api.py con rutas CRUD funcionales para esta API:

PROYECTO: {nombre}
PROPÓSITO: {objetivo}
FUNCIONALIDADES: {funcionalidades}
RESTRICCIONES: {restricciones}

Requisitos CRÍTICOS:
- Usa APIRouter de FastAPI
- Almacenamiento EN MEMORIA: listas/diccionarios globales al inicio del archivo
- Implementa operaciones CRUD básicas según las funcionalidades
- Maneja errores con HTTPException
- Usa los schemas correctos en las rutas
- Implementa TODAS las restricciones de negocio mencionadas
- Código funcional y ejecutable

EJEMPLO:
```python
from fastapi import APIRouter, HTTPException
from app.models import Item
from app.schemas import ItemCreate

router = APIRouter()
items = []  # Almacenamiento en memoria

@router.post("/items", response_model=Item)
def create_item(item: ItemCreate):
    new_item = Item(id=len(items)+1, **item.dict())
    items.append(new_item)
    return new_item
```

Responde SOLO el código Python funcional, sin explicaciones:"""


def _get_prompt_tests(nombre: str, funcionalidades: str) -> str:
    return f"""Genera el archivo tests/test_api.py con tests básicos para esta API:

PROYECTO: {nombre}
FUNCIONALIDADES: {funcionalidades}

Requisitos:
- Usa pytest
- Usa TestClient de FastAPI
- Crea tests para los endpoints principales
- Verifica códigos de estado
- Verifica estructura de respuestas

Responde SOLO el código Python, sin explicaciones:"""


def rellenar_archivos_con_codigo(
    nombre_proyecto: str,
    objetivo: str,
    funcionalidades: str,
    restricciones: str,
    estructura: dict
) -> int:
    """
    Rellena todos los archivos del proyecto con código funcional.
    
    Args:
        nombre_proyecto: Nombre de la PoC
        objetivo: Qué problema resuelve
        funcionalidades: Funcionalidades del sistema
        restricciones: Restricciones del sistema
        estructura: Estructura de archivos del proyecto
        
    Returns:
        int: Número de archivos rellenados
    """
    
    directorio_base = f"output/{nombre_proyecto}"
    archivos_rellenados = 0
    
    print(f"  > Generando código funcional para archivos...")
    
    for ruta_archivo in estructura.keys():
        codigo = generar_codigo_para_archivo(
            nombre_proyecto,
            ruta_archivo,
            objetivo,
            funcionalidades,
            restricciones,
            estructura
        )
        
        ruta_completa = os.path.join(directorio_base, ruta_archivo)
        
        try:
            with open(ruta_completa, 'w', encoding='utf-8') as f:
                f.write(codigo)
            archivos_rellenados += 1
            print(f"    [OK] {ruta_archivo} rellenado")
        except Exception as e:
            print(f"    [ERROR] Error al rellenar {ruta_archivo}: {e}")
    
    print(f"  [OK] {archivos_rellenados} archivos rellenados con código")
    
    return archivos_rellenados
