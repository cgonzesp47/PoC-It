# To know more about the Task class, visit: https://docs.crewai.com/concepts/tasks
from crewai import Task
from textwrap import dedent

class TareasGeneracionPoC:
    def __tip_section(self):
        return "If you do your BEST WORK, I'll give you a $10,000 commission!"

    def tarea_diseño_arq(self, agente_arquitecto, datos_plantilla, herramienta_escritura):
        return Task(
            description=dedent(
                f"""
                **Tarea**: Diseño de Aquitectura y Documentación Técnica
                **Descripción**: Analizar la plantilla de requisitos funcionales
                proporcionada por el usuario y diseñar la estructura de archivos
                (scaffolding) de una PoC en FastAPI. El objetivo es traducir las 
                acciones y entidades en una jerarquía de carpetas lógica y profesional.
                Como resultado, se debe generar un esquema técnico y redactar un
                archivo README.md completo que sirva de guía base para el proyecto.
                
                **INSTRUCCIÓN CRÍTICA**: Una vez que hayas diseñado el README.md, 
                DEBES usar obligatoriamente la herramienta 'escribir_archivo_readme' para 
                guardar el archivo físicamente. No te limites a responder con el 
                texto; ejecuta la acción de escritura.

                **FORMATO OBLIGATORIO**:
                Action: escribir_archivo_readme
                Action Input: {"contenido": "<README COMPLETO, NO SOLO EL ESQUEMA>"}

                **CONTENIDO MÍNIMO DEL README** (obligatorio):
                - Título del proyecto
                - Descripción
                - Instalación (crear venv + pip install)
                - Ejecución
                - Estructura del proyecto (árbol de directorios)
                - Principales entidades y reglas de negocio
                
                **Parámetros**:
                - Plantilla de Requisitos: {datos_plantilla}

                ** Entregables Esperados**:
                1. Un esquema (formato lista o JSON) con la estructura de directorio propuesta.
                2. El contenido completo del archivo README.md (incluyendo descripción,
                instalación y estructura).
                3. Uso de herramientas correspondiente para persistir el README.md en 
                el sistema de archivos.

                **Nota**: {self.__tip_section()}
                """
            ),
            expected_output="Esquema de directorios y contenido completo del README.md.",
            agent=agente_arquitecto,
            tools=[herramienta_escritura],
        )

    def task_2_name(self, agent):
        return Task(
            description=dedent(
                f"""
            Take the input from task 1 and do something with it.
                                       
            {self.__tip_section()}

            Make sure to do something else.
        """
            ),
            expected_output="The expected output of the task",
            agent=agent,
        )
