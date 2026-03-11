"""
Módulo para generar README.md básico sin usar agentes.
Llamadas directas a Ollama con prompts optimizados.
"""
import ollama
import os


def generar_readme_basico(nombre: str, objetivo: str, actores: str, funcionalidades: str, restricciones: str) -> str:
    """
    Genera un README.md básico usando Ollama directamente.
    
    Args:
        nombre: Nombre de la PoC
        objetivo: Qué problema resuelve
        actores: Quién utilizará el sistema
        funcionalidades: Qué debería hacer el sistema
        restricciones: Reglas o límites importantes
        
    Returns:
        str: Contenido del README.md generado
    """
    
    prompt = f"""Escribe un README.md para esta API FastAPI.

PROYECTO: {nombre}
PROPÓSITO: {objetivo}
USUARIOS: {actores}
FUNCIONALIDADES: {funcionalidades}
RESTRICCIONES: {restricciones}

Genera SOLO documentación en formato Markdown que incluya:
- Título # {nombre}
- Descripción del proyecto
- Requisitos (Python 3.8+)
- Instalación (python -m venv venv, pip install -r requirements.txt)
- Uso (uvicorn main:app --reload)
- Documentación API (disponible en /docs)
- Tecnologías usadas

NO generes código Python, solo texto Markdown descriptivo.

README.md:"""

    print("  > Generando README básico con Ollama...")
    
    response = ollama.chat(
        model='qwen7b:latest',
        messages=[{
            'role': 'user',
            'content': prompt
        }],
        options={
            'temperature': 0.3,
            'num_predict': 600,
        }
    )
    
    readme_contenido = response['message']['content'].strip()
    
    # Limpiar si el modelo añade markdown extra
    if readme_contenido.startswith('```markdown'):
        readme_contenido = readme_contenido.replace('```markdown', '').replace('```', '').strip()
    elif readme_contenido.startswith('```'):
        readme_contenido = readme_contenido.replace('```', '').strip()
    
    return readme_contenido


def guardar_readme(nombre_proyecto: str, contenido: str) -> str:
    """
    Guarda el README en el directorio del proyecto.
    
    Args:
        nombre_proyecto: Nombre del proyecto
        contenido: Contenido del README
        
    Returns:
        str: Ruta completa del archivo guardado
    """
    directorio = f"output/{nombre_proyecto}"
    os.makedirs(directorio, exist_ok=True)
    
    ruta_readme = f"{directorio}/README.md"
    
    with open(ruta_readme, 'w', encoding='utf-8') as f:
        f.write(contenido)
    
    print(f"  [OK] README guardado en: {ruta_readme}")
    
    return ruta_readme
