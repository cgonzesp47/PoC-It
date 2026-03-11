import os
import time
from crewai import Agent, Task, Crew, Process
from textwrap import dedent
from agents import AgentesGeneradoresPoC
from tools.escribir_archivo_readme import escribir_archivo_readme_tool
from tools.inicializar_repositorio_git import inicializar_repositorio_git_tool
from tools.leer_archivo_readme import leer_archivo_readme_tool
from tools.generar_estructura import generar_estructura_tool
from tasks import TareasGeneracionPoC

# Evitar error de CrewAI buscando OPENAI_API_KEY
os.environ["OPENAI_API_KEY"] = "not-needed"

# This is the main class that you will use to define your custom crew.
# You can define as many agents and tasks as you want in agents.py and tasks.py


class CustomCrew:
    def __init__(self, nombre, objetivo, actores, funcionalidades, restricciones):
        self.nombre = nombre
        self.objetivo = objetivo
        self.actores = actores
        self.funcionalidades = funcionalidades
        self.restricciones = restricciones

    def run(self):
        # Define your custom agents and tasks in agents.py and tasks.py
        agents = AgentesGeneradoresPoC()
        tasks = TareasGeneracionPoC()

        # Define your custom agents and tasks here
        agente_arquitecto = agents.agente_arquitecto()
        agente_integrador = agents.agente_integrador_git()
        #agente_revisor = agents.agente_revisor()

        # Custom tasks include agent name and variables as input
        datos_plantilla = [self.nombre, self.objetivo, self.actores, self.funcionalidades, self.restricciones]
        
        # Fase 1: Generar README básico
        tarea_readme_basico = tasks.tarea_diseño_readme_basico(
            agente_arquitecto,
            datos_plantilla,
            self.nombre,
            escribir_archivo_readme_tool,
        )
        
        # Fase 2: Añadir JSON estructural al README
        tarea_json_estructura = tasks.tarea_diseño_json_estructura(
            agente_arquitecto,
            datos_plantilla,
            self.nombre,
            leer_archivo_readme_tool,
            escribir_archivo_readme_tool,
        )
        
        # Fase 3: Materializar estructura desde JSON
        tarea_materializar = tasks.tarea_materializar_estructura(
            agente_arquitecto,
            self.nombre,
            leer_archivo_readme_tool,
            generar_estructura_tool,
        )
        
        # Fase 4: Versionado Git
        tarea_git = tasks.tarea_integracion_git(
            agente_integrador,
            self.nombre,
            inicializar_repositorio_git_tool,
        )
        
        # Define your custom crew here
        crew = Crew(
            agents=[agente_arquitecto, agente_integrador],
            tasks=[tarea_readme_basico, tarea_json_estructura, tarea_materializar, tarea_git],
            verbose=True,
        )
        result = crew.kickoff()
        return result


if __name__ == "__main__":
    tiempo_inicio = time.time()
    
    print("## Generador de PoC ##")
    print("-------------------------------")

    nombre = input("1. Nombre de la PoC: ")
    objetivo = input("2. ¿Qué problema resuelve?: ")
    actores = input("3. ¿Quién utilizará el sistema?: ")
    funcionalidades = input("4. ¿Qué debería poder hacer el sistema?: ")
    restricciones = input("5. ¿Hay reglas o límites importantes?: ")


    custom_crew = CustomCrew(nombre, objetivo, actores, funcionalidades, restricciones)
    result = custom_crew.run()
    
    tiempo_final = time.time()
    duracion = tiempo_final - tiempo_inicio
    minutos = int(duracion // 60)
    segundos = int(duracion % 60)
    
    print("\n\n########################")
    print("\nProceso finalizado. Revisa la carpeta output/ para ver:")
    print(f"  - output/{nombre}/ (directorio del proyecto)")
    print(f"  - output/{nombre}/README.md (documentación)")
    print(f"  - output/{nombre}/.git/ (repositorio inicializado)")
    print(f"\nTiempo de ejecución: {minutos}m {segundos}s ({duracion:.2f}s)")
    print("########################\n")
    print(result)
