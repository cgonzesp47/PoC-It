import os
from crewai import Agent, Task, Crew, Process
from textwrap import dedent
from agents import AgentesGeneradoresPoC
from tools.escribir_archivo_readme import escribir_archivo_readme_tool
from tools.leer_archivo_readme import leer_archivo_readme_tool
from tasks import TareasGeneracionPoC

# Evitar error de CrewAI buscando OPENAI_API_KEY
os.environ["OPENAI_API_KEY"] = "not-needed"

# Ollama está configurado para ejecutarse localmente en http://localhost:11434
# Asegúrate de tener Ollama corriendo con: ollama serve

# This is the main class that you will use to define your custom crew.
# You can define as many agents and tasks as you want in agents.py and tasks.py


class CustomCrew:
    def __init__(self, nombre, objetivo, entidades, acciones, reglas):
        self.nombre = nombre
        self.objetivo = objetivo
        self.entidades = entidades
        self.acciones = acciones
        self.reglas = reglas

    def run(self):
        # Define your custom agents and tasks in agents.py and tasks.py
        agents = AgentesGeneradoresPoC()
        tasks = TareasGeneracionPoC()

        # Define your custom agents and tasks here
        agente_arquitecto = agents.agente_arquitecto()
        agente_revisor = agents.agente_revisor()
        herramienta_escritura = escribir_archivo_readme_tool

        # Custom tasks include agent name and variables as input
        datos_plantilla = [self.nombre, self.objetivo, self.entidades, self.acciones, self.reglas]
        tarea_diseño_arq = tasks.tarea_diseño_arq(
            agente_arquitecto,
            datos_plantilla,
            herramienta_escritura,
        )
        
        tarea_revision_readme = tasks.tarea_revision_readme(agente_revisor)

        # Define your custom crew here
        crew = Crew(
            agents=[agente_arquitecto, agente_revisor],
            tasks=[tarea_diseño_arq, tarea_revision_readme],
            verbose=True,
        )

        result = crew.kickoff()
        return result


if __name__ == "__main__":
    print("## Generador de PoC ##")
    print("-------------------------------")
    nombre = input("1. Nombre de la PoC: ")
    objetivo = input("2. Objetivo Principal: ")
    entidades = input("3. Entidades Clave: ")
    acciones = input("4. Acciones (Requisitos): ")
    reglas = input("5. Reglas de Negocio: ")

    custom_crew = CustomCrew(nombre, objetivo, entidades, acciones, reglas)
    result = custom_crew.run()
    print("\n\n########################")
    print("\nProceso finalizado. Revisa tu carpeta para ver el README.md.")    
    print("########################\n")
    print(result)
