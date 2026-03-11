"""
Módulo para generar estructura JSON del proyecto sin usar agentes.
Llamadas directas a Ollama con prompts optimizados.
"""
import ollama
import json
import re


def generar_estructura_json(nombre: str, objetivo: str, funcionalidades: str, restricciones: str) -> dict:
    """
    Genera un JSON con la estructura de archivos del proyecto.
    
    Args:
        nombre: Nombre de la PoC
        objetivo: Qué problema resuelve
        funcionalidades: Qué debería hacer el sistema
        restricciones: Reglas o límites importantes
        
    Returns:
        dict: Estructura del proyecto en formato JSON
    """
    
    prompt = f"""Genera un JSON con la estructura de archivos para esta API FastAPI:

PROYECTO: {nombre}
PROPÓSITO: {objetivo}
FUNCIONALIDADES: {funcionalidades}
RESTRICCIONES: {restricciones}

ARCHIVOS MÍNIMOS REQUERIDOS (incluye TODOS estos):
- main.py
- app/__init__.py
- app/api.py
- app/models.py
- app/schemas.py
- tests/__init__.py
- tests/test_api.py
- requirements.txt
- .gitignore

REGLAS CRÍTICAS:
- NO uses bases de datos (ni SQLAlchemy, ni SQLite, ni ninguna BD)
- Usa almacenamiento EN MEMORIA: listas o diccionarios globales
- Los modelos deben ser Pydantic (from pydantic import BaseModel)
- El código debe ser funcional y ejecutable
- Responde SOLO el JSON, sin explicaciones adicionales

FORMATO EXACTO (sin indentación extra):
{{
"main.py": "código completo",
"app/models.py": "modelos Pydantic con BaseModel",
...
}}

JSON:"""

    print("  > Generando estructura JSON con Ollama...")
    
    response = ollama.chat(
        model='qwen7b:latest',
        messages=[{
            'role': 'user',
            'content': prompt
        }],
        options={
            'temperature': 0.2,
            'num_predict': 3000,
        }
    )
    
    contenido = response['message']['content'].strip()
    
    # Extraer JSON del contenido (puede venir con markdown o texto extra)
    # Primero intentar extraer de bloques ```json o ```
    # Usar .* (greedy) para capturar TODO el JSON hasta el último }
    json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', contenido, re.DOTALL)
    if json_match:
        contenido_json = json_match.group(1).strip()
    else:
        # Si no hay markdown, buscar desde primer { hasta último } balanceado
        # Encontrar primer {
        start_idx = contenido.find('{')
        if start_idx == -1:
            contenido_json = contenido
        else:
            # Buscar el } que balancea el primer {
            brace_count = 0
            end_idx = start_idx
            for i in range(start_idx, len(contenido)):
                if contenido[i] == '{':
                    brace_count += 1
                elif contenido[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break
            contenido_json = contenido[start_idx:end_idx]
    
    # Normalizar el JSON: eliminar indentación extra que pueda causar problemas
    # Reemplazar múltiples espacios al inicio de líneas con formato estándar
    lineas = contenido_json.split('\n')
    lineas_normalizadas = []
    for linea in lineas:
        # Eliminar espacios en blanco excesivos al inicio
        linea_limpia = linea.lstrip()
        if linea_limpia:  # Solo añadir líneas no vacías
            lineas_normalizadas.append(linea_limpia)
    contenido_json_normalizado = ' '.join(lineas_normalizadas)
    
    # Parsear JSON
    try:
        estructura = json.loads(contenido_json_normalizado)
        print(f"  [OK] Estructura generada con {len(estructura)} archivos")
        return estructura
    except json.JSONDecodeError as e:
        print(f"  [ERROR] Error al parsear JSON: {e}")
        print(f"  Contenido original: {contenido[:300]}...")
        print(f"  JSON extraído: '{contenido_json[:200]}'")
        print(f"  JSON normalizado: '{contenido_json_normalizado[:200]}'")
        # Estructura mínima fallback
        return {
            "main.py": 
            """from fastapi import FastAPI

            app = FastAPI(title="API", version="1.0.0")

            @app.get("/")
            def read_root():
                return {"message": "API funcionando"}
            """,
            "requirements.txt": "fastapi\nuvicorn[standard]\npydantic",
            ".gitignore": "__pycache__/\n*.pyc\nvenv/\n.env"
        }


def añadir_json_a_readme(ruta_readme: str, estructura: dict) -> None:
    """
    Añade la sección de estructura JSON al README existente.
    
    Args:
        ruta_readme: Ruta del archivo README.md
        estructura: Diccionario con la estructura del proyecto
    """
    
    with open(ruta_readme, 'r', encoding='utf-8') as f:
        contenido_readme = f.read()
    
    # Añadir sección JSON al final
    seccion_json = f"""
    ---

    ## ESTRUCTURA_JSON_AUTOGENERADA (NO MODIFICAR)

    ```json
    {json.dumps(estructura, indent=2, ensure_ascii=False)}
    ```

    ---
    """
    
    contenido_actualizado = contenido_readme + seccion_json
    
    with open(ruta_readme, 'w', encoding='utf-8') as f:
        f.write(contenido_actualizado)
    
    print(f"  [OK] Estructura JSON añadida al README")
