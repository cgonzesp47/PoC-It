from crewai import Agent, LLM
from textwrap import dedent

from tools.escribir_archivo_readme import escribir_archivo_readme_tool
from tools.leer_archivo_readme import leer_archivo_readme_tool
from tools.inicializar_repositorio_git import inicializar_repositorio_git_tool
from tools.generar_estructura import generar_estructura_tool

custom_tool = escribir_archivo_readme_tool

class AgentesGeneradoresPoC:
    def __init__(self):
        # Configuración de LLMs con timeout aumentado a 1200 segundos (20 minutos)
        self.llm_rapido = LLM(
            model="ollama/deepseek7b:latest",
            timeout=1200,  # 20 minutos
        )
        self.llm_potente = LLM(
            model="ollama/qwen7b:latest",
            timeout=1200,  # 20 minutos
        )
    def agente_arquitecto(self):
        return Agent(
            role="Arquitecto de Sistemas Senior y Documentador Técnico",
            backstory=dedent(f"""
                            Experto en arquitecturas backend modernas y patrones de diseño
                            con alta especialización en el ecosistema Python y frameworks modernos.
                            Especialista en el análisis de requisitos funcionales y su transformación 
                            en especificaciones técnicas detalladas. Experto en la definición de scaffolding 
                            (estructuras de carpetas) siguiendo estándares de separación de 
                            responsabilidades, asegurando que el diseño sea intuitivo para el equipo
                            de desarrollo y fácil de versionar en Git. Especialista en la creación 
                            de documentación clara y profesional para desarrolladores, asegurando que la 
                            visión del producto se traduzca fielmente en una estructura de archivos lógica y
                            una guía de inicio exhaustiva."""),
            
            goal=dedent(f"""   
                        Diseñar y documentar PoCs backend en FastAPI de forma simple y profesional.
                        
                        Cuando diseñes:
                        - Interpreta requisitos funcionales
                        - Define arquitectura simple (sin capas enterprise)
                        - Documenta en README con secciones: Descripción, Instalación, Ejecución
                        - Incluye JSON estructural en sección "## ESTRUCTURA_JSON_AUTOGENERADA (NO MODIFICAR)"
                        
                        Cuando materialices:
                        - Lee el README generado
                        - Extrae el JSON exacto
                        - Crea la estructura sin modificar nada
                        
                        Cada tarea te indicará qué hacer específicamente."""),
            tools=[],
            allow_delegation=False,
            verbose=True,
            llm=self.llm_potente,
        )

    '''
    def agente_revisor(self):
        return Agent(
            role="Revisor de Documentación Técnica",
            backstory=dedent(f"""
                            Especialista en validación de documentación técnica y control de calidad.
                            Experto en verificar que todos los componentes clave están presentes y bien documentados.
                            Detallista y exigente, asegura que no falte ningún detalle importante en la documentación.
                            Si encuentra deficiencias, proporciona retroalimentación clara y específica."""),
            
            goal=dedent(f"""
                        1. Revisar el README.md que ha sido generado por el arquitecto.
                        2. Verificar que contenga TODAS las secciones obligatorias con contenido adecuado.
                        3. Si falta alguna sección o está incompleta, comunicar qué hace falta.
                        4. Si el README es completo, confirmar que está listo para usar."""),
            tools=[leer_archivo_readme_tool],
            allow_delegation=False,
            verbose=True,
            llm=self.llm_rapido,
        )
    '''

    def agente_integrador_git(self):
        return Agent(
            role="Integrador Git y Especialista en Control de Versiones",
            backstory=dedent(f"""
                            Experto en gestión de repositorios Git y control de versiones.
                            Especialista en la inicialización de proyectos, configuración de repositorios
                            y creación de commits estructurados siguiendo convenciones profesionales.
                            Mantiene historial limpio y organizado, asegurando que cada cambio esté
                            debidamente documentado. Conoce las mejores prácticas para archivos .gitignore,
                            estructura de commits y mensajes descriptivos que facilitan la colaboración
                            en equipo."""),
            
            goal=dedent(f"""
                        1. Inicializar un repositorio Git en el directorio del proyecto.
                        2. Crear un archivo .gitignore apropiado para proyectos Python/FastAPI.
                        3. Realizar el commit inicial con todos los archivos generados por el arquitecto.
                        4. Asegurar que el README.md validado por el revisor esté incluido en el repositorio.
                        5. Documentar el estado del repositorio y confirmar que está listo para desarrollo."""),
            tools=[],
            allow_delegation=False,
            verbose=False,
            llm=self.llm_potente,
        )

    '''
    def agent_3_name(self):
        return Agent(
            role="Define agent 3 role here",
            backstory=dedent(f"""Define agent 3 backstory here"""),
            goal=dedent(f"""Define agent 3 goal here"""),
            # tools=[tool_1, tool_2],
            allow_delegation=False,
            verbose=True,
            llm=self.llm_rapido,
        )
    
    def agent_4_name(self):
        return Agent(
            role="Define agent 4 role here",
            backstory=dedent(f"""Define agent 4 backstory here"""),
            goal=dedent(f"""Define agent 4 goal here"""),
            # tools=[tool_1, tool_2],
            allow_delegation=False,
            verbose=True,
            llm=self.llm_rapido,
        )
    '''
