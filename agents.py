from crewai import Agent
from textwrap import dedent
from langchain.llms import OpenAI, Ollama
from langchain_openai import ChatOpenAI


class CustomAgents:
    def __init__(self):
        self.OpenAIGPT35 = ChatOpenAI(model_name="gpt-3.5-turbo", temperature=0.7)
        self.OpenAIGPT4 = ChatOpenAI(model_name="gpt-4", temperature=0.7)
        self.Ollama = Ollama(model="openhermes")

    def agente_arquitecto(self):
        return Agent(
            role="Arquitecto de Sistemas Senior",
            backstory=dedent(f"""
                            Experto en arquitecturas backend modernas y patrones de diseño
                            con alta especialización en el ecosistema Python y frameworks modernos.
                            Enfocado en la transformación de requisitos funcionales en arquitecturas
                            técnicas escalables y organizadas. Experto en la definición de scaffolding 
                            (estructuras de carpetas) siguiendo estándares de separación de 
                            responsabilidades, asegurando que el diseño sea intuitivo para el equipo
                            de desarrollo y fácil de versionar en Git."""),
            
            goal=dedent(f"""   
                        1. Analizar la descripción funcional proporcionada.
                        2. Diseñar el árbol de directorios detallado para la PoC.
                        3. Generar un archivo README.md que documente la estructura del proyecto.
                        4. Incluir en el README una guía de instalacion paso a paso (creación de 
                        entorno virtual y comando pip).
                        5. Detallar las instrucciones básicas para lanzar la aplicación."""),
            # tools=[tool_1, tool_2],
            allow_delegation=False,
            verbose=True,
            llm=self.OpenAIGPT35,
        )

    def agent_2_name(self):
        return Agent(
            role="Define agent 2 role here",
            backstory=dedent(f"""Define agent 2 backstory here"""),
            goal=dedent(f"""Define agent 2 goal here"""),
            # tools=[tool_1, tool_2],
            allow_delegation=False,
            verbose=True,
            llm=self.OpenAIGPT35,
        )
    
    def agent_3_name(self):
        return Agent(
            role="Define agent 3 role here",
            backstory=dedent(f"""Define agent 3 backstory here"""),
            goal=dedent(f"""Define agent 3 goal here"""),
            # tools=[tool_1, tool_2],
            allow_delegation=False,
            verbose=True,
            llm=self.OpenAIGPT35,
        )
    
    def agent_4_name(self):
        return Agent(
            role="Define agent 4 role here",
            backstory=dedent(f"""Define agent 4 backstory here"""),
            goal=dedent(f"""Define agent 4 goal here"""),
            # tools=[tool_1, tool_2],
            allow_delegation=False,
            verbose=True,
            llm=self.OpenAIGPT35,
        )
