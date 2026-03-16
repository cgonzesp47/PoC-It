"""
Módulo para generar código funcional en archivos ya creados.
Rellena los esqueletos con implementaciones completas.
"""
import ollama
import os


def _generar_requirements_contextuales(tecnologias: str) -> str:
    """
    Genera requirements.txt con dependencias según las tecnologías especificadas.
    Usa el LLM para inferir las dependencias necesarias (sin hardcodeo).
    
    Args:
        tecnologias: Tecnologías solicitadas (separadas por comas)
        
    Returns:
        str: Contenido del requirements.txt
    """
    
    # Si no hay tecnologías específicas, solo base
    if not tecnologias or tecnologias.lower().strip() in ['ninguna', 'no', 'none']:
        return '''fastapi>=0.104.0
uvicorn[standard]>=0.24.0
pydantic>=2.0.0
pytest>=7.4.0
httpx>=0.25.0'''
    
    # Usar LLM para inferir dependencias
    prompt = f"""Genera requirements.txt para una API FastAPI que necesita estas tecnologías: {tecnologias}

REGLAS ESTRICTAS:
1. SIEMPRE incluir dependencias base: fastapi, uvicorn[standard], pydantic, pytest, httpx
2. Añadir SOLO las dependencias necesarias para las tecnologías especificadas
3. Usar versiones recientes (>=X.Y.Z)
4. Formato: una dependencia por línea (paquete>=version)
5. NO incluir comentarios, NO incluir explicaciones
6. Si una tecnología necesita múltiples paquetes, incluir todos

FORMATO ESPERADO:
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
pydantic>=2.0.0
...

Genera SOLO el requirements.txt (sin texto adicional):"""
    
    print(f"    > Infiriendo dependencias para: {tecnologias}")
    
    try:
        response = ollama.chat(
            model='qwen7b:latest',
            messages=[{'role': 'user', 'content': prompt}],
            options={
                'temperature': 0.1,
                'num_predict': 500
            }
        )
        
        contenido = response['message']['content'].strip()
        
        # Limpiar si viene con markdown
        if '```' in contenido:
            contenido = contenido.split('```')[1]
            if contenido.startswith('txt') or contenido.startswith('python'):
                contenido = '\n'.join(contenido.split('\n')[1:])
        
        # Validar que tenga al menos las dependencias base
        if 'fastapi' not in contenido.lower():
            print(f"    [WARN] LLM no incluyó dependencias base, usando fallback")
            return '''fastapi>=0.104.0
uvicorn[standard]>=0.24.0
pydantic>=2.0.0
pytest>=7.4.0
httpx>=0.25.0'''
        
        return contenido.strip()
        
    except Exception as e:
        print(f"    [ERROR] Error al generar requirements: {e}")
        # Fallback a dependencias base
        return '''fastapi>=0.104.0
uvicorn[standard]>=0.24.0
pydantic>=2.0.0
pytest>=7.4.0
httpx>=0.25.0'''


def generar_codigo_para_archivo(
    nombre_proyecto: str,
    ruta_archivo: str,
    objetivo: str,
    funcionalidades: str,
    restricciones: str,
    estructura_completa: dict,
    tecnologias: str = "ninguna"
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
        tecnologias: Tecnologías/integraciones necesarias
        
    Returns:
        str: Código funcional generado
    """
    
    # Generar requirements.txt contextual
    if ruta_archivo == 'requirements.txt':
        return _generar_requirements_contextuales(tecnologias)
    
    # Archivos que no necesitan código complejo
    archivos_simples = {
        'tests/__init__.py': '',
        'app/__init__.py': 'from app.api import router',
        '.gitignore': '__pycache__/\n*.pyc\n*.pyo\n*.pyd\n.Python\nvenv/\n.env\n*.log\n.pytest_cache/\n*.db',
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
    
    # Ajustar num_predict según complejidad del archivo
    num_predict_map = {
        'main.py': 500,
        'app/__init__.py': 100,
        'app/models.py': 800,
        'app/schemas.py': 800,
        'app/api.py': 3500,  # Archivo más complejo, necesita más tokens
        'tests/test_api.py': 1000,
    }
    
    num_predict = num_predict_map.get(ruta_archivo, 1000)
    
    # Solo 1 reintento para ahorrar tiempo
    MAX_REINTENTOS = 1
    codigo = ""
    
    for intento in range(MAX_REINTENTOS + 1):
        response = ollama.chat(
            model='qwen7b:latest',
            messages=[{
                'role': 'user',
                'content': prompt
            }],
            options={
                'temperature': 0.2,
                'num_predict': num_predict,
            }
        )
        
        codigo = response['message']['content'].strip()
        
        # Limpiar markdown
        if codigo.startswith('```python'):
            codigo = codigo.replace('```python', '').replace('```', '').strip()
        elif codigo.startswith('```'):
            codigo = codigo.replace('```', '').strip()
        
        # Si no está truncado, retornar inmediatamente
        if not _esta_truncado(codigo):
            return codigo
        
        if intento < MAX_REINTENTOS:
            print(f"    [WARN] Código truncado, reintentando ({intento+1}/{MAX_REINTENTOS})...")
    
    # Si falla, retornar el último intento (puede estar incompleto pero mejor que nada)
    print(f"    [WARN] Código posiblemente incompleto, usando último intento")
    return codigo


def _esta_truncado(codigo: str) -> bool:
    """
    Detecta si el código parece estar truncado o incompleto.
    
    Args:
        codigo: Código Python a validar
        
    Returns:
        bool: True si el código parece truncado
    """
    if not codigo:
        return True
    
    lineas = codigo.strip().split('\n')
    if not lineas:
        return True
    
    ultima_linea = lineas[-1].strip()
    
    # Señales de truncamiento
    truncamiento_signals = [
        ultima_linea.endswith((',', '(', '[', '{', '\\')),
        'def ' in ultima_linea and ':' not in ultima_linea,
        'class ' in ultima_linea and ':' not in ultima_linea,
        codigo.count('"""') % 2 != 0,  # Docstring sin cerrar
        codigo.count("'''") % 2 != 0,  # Docstring alternativo sin cerrar
        codigo.count('(') != codigo.count(')'),  # Paréntesis desbalanceados
        codigo.count('[') != codigo.count(']'),  # Corchetes desbalanceados
        codigo.count('{') != codigo.count('}'),  # Llaves desbalanceadas
    ]
    
    return any(truncamiento_signals)


def _get_prompt_main(nombre: str) -> str:
    return f"""Genera el archivo main.py para una API FastAPI llamada {nombre}.

Requisitos:
- Importar FastAPI
- Importar router desde app.api
- Crear instancia de FastAPI con título y versión
- Incluir el router
- Añadir endpoint raíz GET / que retorne mensaje de bienvenida

IMPORTANTE: SOLO código Python puro. Todos los comentarios con #. Sin texto explicativo al final.

Genera el código:"""


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

IMPORTANTE: SOLO código Python puro. Todos los comentarios con #. Sin texto explicativo al final.

Genera el código:"""


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

IMPORTANTE: SOLO código Python puro. Todos los comentarios con #. Sin texto explicativo al final.

Genera el código:"""


def _get_prompt_api(nombre: str, objetivo: str, funcionalidades: str, restricciones: str) -> str:
    # Condensar contexto para dejar más espacio a la generación de código
    objetivo_resumido = objetivo[:300] + "..." if len(objetivo) > 300 else objetivo
    funcionalidades_resumidas = funcionalidades[:400] + "..." if len(funcionalidades) > 400 else funcionalidades
    restricciones_resumidas = restricciones[:250] + "..." if len(restricciones) > 250 else restricciones
    
    return f"""Genera app/api.py para FastAPI: {nombre}

CONTEXTO:
{objetivo_resumido}

QUÉ DEBE HACER:
{funcionalidades_resumidas}

REGLAS:
{restricciones_resumidas}

ESTRUCTURA OBLIGATORIA:
```python
from fastapi import APIRouter, HTTPException
from app.schemas import ...

router = APIRouter()
# Almacenamiento en memoria aquí

@router.post("/endpoint")
def funcion(param: Schema):
    # Implementación con validaciones
    pass
```

IMPORTANTE - REGLAS DE FORMATO:
1. SOLO código Python - NINGÚN texto explicativo fuera del código
2. TODOS los comentarios deben empezar con # (numeral)
3. PROHIBIDO incluir bloques "NOTAS:" o listas sin #
4. NO agregues secciones explicativas al final

GENERA CÓDIGO COMPLETO Y FUNCIONAL (solo código Python puro):"""


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

IMPORTANTE: SOLO código Python puro. Todos los comentarios con #. Sin texto explicativo al final.

Genera el código:"""


def rellenar_archivos_con_codigo(
    nombre_proyecto: str,
    objetivo: str,
    funcionalidades: str,
    restricciones: str,
    estructura: dict,
    tecnologias: str = "ninguna"
) -> int:
    """
    Rellena todos los archivos del proyecto con código funcional.
    
    Args:
        nombre_proyecto: Nombre de la PoC
        objetivo: Qué problema resuelve
        funcionalidades: Funcionalidades del sistema
        restricciones: Restricciones del sistema
        estructura: Estructura de archivos del proyecto
        tecnologias: Tecnologías/integraciones necesarias
        
    Returns:
        int: Número de archivos rellenados
    """
    from sin_crewai.validador import _limpiar_codigo_generado
    
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
            estructura,
            tecnologias
        )
        
        # CRÍTICO: Limpiar código ANTES de escribirlo al archivo
        # Esto elimina duplicaciones, texto sin comentar, etc.
        if ruta_archivo.endswith('.py'):
            codigo_original_len = len(codigo.split('\n'))
            codigo = _limpiar_codigo_generado(codigo)
            codigo_limpio_len = len(codigo.split('\n'))
            
            if codigo_original_len != codigo_limpio_len:
                print(f"    [LIMPIEZA] {ruta_archivo}: {codigo_original_len} → {codigo_limpio_len} líneas")
        
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
