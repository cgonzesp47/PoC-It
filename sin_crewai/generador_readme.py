"""
Módulo para generar README.md básico sin usar agentes.
Llamadas directas a Ollama con prompts optimizados.
"""
import ollama
import os


def generar_readme_basico(nombre: str, objetivo: str, actores: str, funcionalidades: str, restricciones: str, tecnologias: str = "ninguna") -> str:
    """
    Genera un README.md básico usando Ollama directamente.
    
    Args:
        nombre: Nombre de la PoC
        objetivo: Qué problema resuelve
        actores: Quién utilizará el sistema
        funcionalidades: Qué debería hacer el sistema
        restricciones: Reglas o límites importantes
        tecnologias: Tecnologías/integraciones usadas
        
    Returns:
        str: Contenido del README.md generado
    """
    
    prompt = f"""Escribe un README.md para esta API FastAPI.

PROYECTO: {nombre}
PROPÓSITO: {objetivo}
USUARIOS: {actores}
FUNCIONALIDADES: {funcionalidades}
RESTRICCIONES: {restricciones}
TECNOLOGÍAS: {tecnologias}

Genera SOLO documentación en formato Markdown que incluya unicamente los siguientes apartados:
- Título # {nombre}
- Descripción del proyecto
- Requisitos (Python 3.8+)
- Instalación (python -m venv venv, pip install -r requirements.txt)
- Uso (uvicorn main:app --reload)
- Documentación API (disponible en /docs)
- Tecnologías usadas

NO generes código Python, solo texto Markdown descriptivo.
Solo debes incluir esas secciones, no añadas secciones adicionales ni detalles que no estén en la información proporcionada.

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
            'num_predict': 1200,
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


def actualizar_readme_con_endpoints(nombre_proyecto: str, tecnologias: str) -> None:
    """
    Actualiza el README con información de endpoints y requisitos
    basándose en el código generado real.
    
    Esta función se llama DESPUÉS de generar el código, para documentar
    endpoints reales y requisitos específicos inferidos del código.
    
    Args:
        nombre_proyecto: Nombre del proyecto
        tecnologias: Tecnologías usadas en el proyecto
    """
    ruta_readme = f"output/{nombre_proyecto}/README.md"
    
    # Leer código generado para inferir endpoints
    try:
        codigo_main = ""
        codigo_api = ""
        
        # Intentar leer main.py o app/api.py
        if os.path.exists(f"output/{nombre_proyecto}/main.py"):
            with open(f"output/{nombre_proyecto}/main.py", 'r', encoding='utf-8') as f:
                codigo_main = f.read()
        
        if os.path.exists(f"output/{nombre_proyecto}/app/api.py"):
            with open(f"output/{nombre_proyecto}/app/api.py", 'r', encoding='utf-8') as f:
                codigo_api = f.read()
        
        codigo_completo = codigo_main + "\n" + codigo_api
        
        if not codigo_completo.strip():
            print("  [SKIP] No hay código generado para analizar")
            return
        
        # Generar documentación de endpoints y requisitos con Ollama
        prompt = f"""Analiza este código FastAPI y genera DOS secciones en Markdown:

CÓDIGO:
```python
{codigo_completo[:3000]}  # Primeros 3000 caracteres
```

TECNOLOGÍAS INDICADAS POR USUARIO: {tecnologias}

Genera SOLO estas dos secciones en formato Markdown:

## Endpoints Disponibles

Analiza el código y lista SOLO los endpoints que encuentres (decoradores @app.get, @app.post, @app.delete, @app.put, etc.)
Para cada endpoint indica:
- Método HTTP y ruta
- Descripción breve de qué hace (basándote en el código)
- Parámetros principales (si los tiene)

Si no encuentras endpoints claramente definidos, indica "Revisar /docs para documentación completa"

## Requisitos para Ejecutar

Analiza los imports del código y determina qué configuración externa necesita el usuario.

Si ves imports de librerías de servicios externos (cloud providers, bases de datos, APIs de terceros, etc.):
- Identifica qué servicio es
- Explica qué credenciales o configuración necesita
- Indica cómo configurarlas (variables de entorno, archivos de config, etc.)

Si NO ves imports de servicios externos, indica que la PoC funciona sin configuración adicional.

Sé ESPECÍFICO basándote SOLO en lo que VES en el código. NO supongas tecnologías que no aparecen en los imports.

NO repitas secciones que ya existan en el README (título, descripción, instalación).
Genera SOLO estas dos secciones nuevas:"""

        print("  > Analizando código generado para documentar endpoints...")
        
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
        
        nuevas_secciones = response['message']['content'].strip()
        
        # Limpiar markdown extra
        if nuevas_secciones.startswith('```markdown'):
            nuevas_secciones = nuevas_secciones.replace('```markdown', '').replace('```', '').strip()
        elif nuevas_secciones.startswith('```'):
            nuevas_secciones = nuevas_secciones.replace('```', '').strip()
        
        # Leer README actual
        with open(ruta_readme, 'r', encoding='utf-8') as f:
            readme_actual = f.read()
        
        # Añadir nuevas secciones al final
        readme_actualizado = readme_actual + "\n\n---\n\n" + nuevas_secciones
        
        # Guardar README actualizado
        with open(ruta_readme, 'w', encoding='utf-8') as f:
            f.write(readme_actualizado)
        
        print(f"  [OK] README actualizado con endpoints y requisitos")
        
    except Exception as e:
        print(f"  [WARNING] No se pudo actualizar README: {e}")
