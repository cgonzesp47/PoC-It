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
                Action Input: {{"contenido": "<README COMPLETO, NO SOLO EL ESQUEMA>"}}

                **CONTENIDO MÍNIMO DEL README** (TODAS LAS SECCIONES SON OBLIGATORIAS):
                
                # [NOMBRE DEL PROYECTO]
                
                ## DESCRIPCIÓN
                [Explicar qué hace el sistema y su propósito]
                
                ## INSTALACIÓN
                Debes explicar paso a paso:
                - Crear entorno virtual (venv)
                - Instalar dependencias con poetry install
                - Configuración adicional si es necesaria
                
                ## EJECUCIÓN
                Debes explicar:
                - Comando para iniciar el servidor FastAPI
                - Puerto y URL de acceso
                - Comando para ver documentación interactiva
                
                ## ESTRUCTURA DEL PROYECTO
                [Árbol de directorios completo y detallado]
                
                ## ENTIDADES Y REGLAS DE NEGOCIO
                [Listar entidades con sus campos y reglas de validación]
                
                IMPORTANTE: El README DEBE incluir TODAS estas secciones completamente desarrolladas. Si falta alguna, la tarea se considera incompleta.
                
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

    def tarea_revision_readme(self, agente_revisor):
        return Task(
            description=dedent(
                f"""
                **Tarea**: Revisión y Validación del README.md
                **Descripción**: Revisar el archivo README.md que ha sido generado por el arquitecto
                y verificar que contenga TODAS las secciones obligatorias con contenido adecuado.
                
                **INSTRUCCIÓN CRÍTICA**: Debes usar obligatoriamente la herramienta 'leer_archivo_readme' 
                para leer el contenido del archivo. No supongas qué contiene; léelo completamente.

                **SECCIONES OBLIGATORIAS QUE DEBES VALIDAR**:
                1. DESCRIPCIÓN - Explica qué hace el sistema
                2. INSTALACIÓN - Pasos para instalar (venv, poetry install, etc.)
                3. EJECUCIÓN - Comandos para ejecutar (uvicorn, puerto de acceso, documentación)
                4. ESTRUCTURA DEL PROYECTO - Árbol de directorios o estructura de carpetas
                5. ENTIDADES Y REGLAS DE NEGOCIO - Listado de entidades y reglas de validación

                **RESULTADO ESPERADO**:
                Si TODAS las 5 secciones están presentes y tienen contenido adecuado:
                - Responde: "APROBADO: El README contiene todas las secciones obligatorias."
                
                Si FALTA alguna sección:
                - Responde: "RECHAZADO: Faltan las siguientes secciones: [listar cuáles]"
                
                **Nota**: {self.__tip_section()}
                """
            ),
            expected_output="Resultado de validación: APROBADO o RECHAZADO con detalles",
            agent=agente_revisor,
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
