from crewai import Agent
from textwrap import dedent

from tools.escribir_archivo_readme import escribir_archivo_readme_tool
from tools.leer_archivo_readme import leer_archivo_readme_tool
custom_tool = escribir_archivo_readme_tool

class AgentesGeneradoresPoC:
    def __init__(self):
        # CrewAI maneja Ollama con el formato de string "ollama/nombre_modelo"
        self.llm_rapido = "ollama/deepseek7b:latest"
        self.llm_potente = "ollama/mistral7b:latest"
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
                        1. Interpretar la plantilla de descripción funcional del usuario.
                        2. Diseñar el árbol de directorios detallado para la PoC.
                        3. Generar un archivo README.md que documente la estructura del proyecto.
                        4. Crear un archivo README.md profesional que sirva como "Manual de Identidad" 
                        del proyecto, incluyendo: descripción del sistema, guía de instalación paso a paso
                        (creación de entorno virtual y comando pip) y comandos de ejecución.
                        5. Detallar las instrucciones básicas para lanzar la aplicación."""),
            tools=[custom_tool],
            allow_delegation=False,
            verbose=True,
            llm=self.llm_potente,
        )

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

    def agent_2_name(self):
        return Agent(
            role="Define agent 2 role here",
            backstory=dedent(f"""Define agent 2 backstory here"""),
            goal=dedent(f"""Define agent 2 goal here"""),
            # tools=[tool_1, tool_2],
            allow_delegation=False,
            verbose=True,
            llm=self.llm_rapido,
        )
    
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
