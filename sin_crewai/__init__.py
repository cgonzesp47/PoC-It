"""
Paquete sin_crewai - Generación de PoC Backend sin framework de orquestación

Módulos:
- generador_readme: Genera README.md usando Ollama directamente
- generador_estructura: Diseña estructura JSON del proyecto
- materializador: Crea físicamente los archivos del proyecto
- integrador_git: Inicializa repositorio Git y crea commit inicial
"""

from .generador_readme import generar_readme_basico, guardar_readme
from .generador_estructura import generar_estructura_json, añadir_json_a_readme
from .materializador import materializar_estructura, extraer_estructura_de_readme
from .integrador_git import inicializar_git, obtener_estado_git

__all__ = [
    'generar_readme_basico',
    'guardar_readme',
    'generar_estructura_json',
    'añadir_json_a_readme',
    'materializar_estructura',
    'extraer_estructura_de_readme',
    'inicializar_git',
    'obtener_estado_git',
]
