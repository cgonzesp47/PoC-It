# To know more about the Task class, visit: https://docs.crewai.com/concepts/tasks
from crewai import Task
from textwrap import dedent

class TareasGeneracionPoC:
    def __tip_section(self):
        return "If you do your BEST WORK, I'll give you a $10,000 commission!"

    def tarea_diseño_readme_basico(self, agente_arquitecto, datos_plantilla, nombre_proyecto, herramienta_escritura):
        return Task(
            description=dedent(
                f"""
                Generar README.md básico para la PoC backend en FastAPI.

                DEBES:
                1. Crear README con secciones: Descripción, Instalación, Ejecución
                2. Usar la herramienta escribir_archivo_readme

                FORMATO DE ACCIÓN:
                Action: escribir_archivo_readme
                Action Input: {{
                    "contenido": "<README>",
                    "nombre_proyecto": "{nombre_proyecto}"
                }}

                README debe incluir:
                - # {nombre_proyecto}
                - ## Descripción (breve, basada en plantilla)
                - ## Instalación (venv + pip install -r requirements.txt)
                - ## Ejecución (uvicorn main:app --reload)

                NO incluyas sección de estructura JSON todavía.

                Plantilla: {datos_plantilla}
            """),

            expected_output="README básico generado.",
            agent=agente_arquitecto,
            tools=[herramienta_escritura],
    )

    def tarea_diseño_json_estructura(self, agente_arquitecto, datos_plantilla, nombre_proyecto, herramienta_lectura, herramienta_escritura):
        return Task(
            description=dedent(
                f"""
                Añadir sección JSON estructural al README existente.

                PASOS:
                1. Usa leer_archivo_readme para leer el README actual
                2. Diseña estructura simple del proyecto (solo archivos mínimos)
                3. Añade sección al final del README

                ESTRUCTURA SIMPLE:
                - Archivos básicos
                - Carpetas solo si son necesarias
                - Debe tener formato JSON claro y legible

                Añade al final del README:

                ---

                ## ESTRUCTURA_JSON_AUTOGENERADA (NO MODIFICAR)

                ```json
                <TU JSON AQUÍ>
                ```

                ---

                Plantilla: {datos_plantilla}
            """),

            expected_output="README actualizado con JSON estructural.",
            agent=agente_arquitecto,
            tools=[herramienta_lectura, herramienta_escritura],
    )

    def tarea_materializar_estructura(self, agente_arquitecto, nombre_proyecto, herramienta_lectura, herramienta_generar,):
        return Task(
            description=f"""
                Tu misión es materializar EXACTAMENTE la estructura definida en el README.md del proyecto.

                PASOS OBLIGATORIOS:

                1. Usa la herramienta leer_archivo_readme para obtener el contenido completo del README.md.
                2. Localiza la sección titulada: "## ESTRUCTURA_JSON_AUTOGENERADA (NO MODIFICAR)" y extrae exclusivamente el bloque JSON
                3. Extrae ese JSON EXACTAMENTE como aparece, sin modificarlo.
                4. Usa la herramienta generar_estructura pasando ese JSON sin alteraciones.

                REGLAS CRÍTICAS:

                - No rediseñar la estructura.
                - No simplificarla.
                - No añadir archivos adicionales.
                - No generar un nuevo JSON.
                - Debes usar EXACTAMENTE el JSON presente en el README.

                FORMATO DE ACCIÓN FINAL (obligatorio):

                Action: generar_estructura
                Action Input: {{
                    "nombre_proyecto": "{nombre_proyecto}",
                    "estructura": "<JSON exacto extraído del README>"
                }}

                No expliques nada.
                No generes texto adicional.
                Solo ejecuta la acción.
                """,

            expected_output="Estructura creada exactamente según el JSON del README.",
            agent=agente_arquitecto,
            tools=[herramienta_lectura, herramienta_generar],
    )


    def tarea_integracion_git(self, agente_integrador, nombre_proyecto, herramienta_git):
        return Task(
            description=dedent(
                f"""
                Inicializar versionado Git para la PoC generada.

                DEBES:
                1. Ejecutar la herramienta inicializar_repositorio_git.
                2. Crear .gitignore para Python.
                3. Realizar commit inicial.

                FORMATO DE ACCIÓN:
                Action: inicializar_repositorio_git
                Action Input: {{
                    "nombre_proyecto": "{nombre_proyecto}"
                }}

                No describas pasos. Ejecuta la herramienta.
                """),

            expected_output="Repositorio Git inicializado y commit creado.",
            agent=agente_integrador,
            tools=[herramienta_git],
        )
    
    '''
    def tarea_revision_readme(self, agente_revisor, nombre_proyecto, herramienta_lectura):
        return Task(
            description=dedent(
                f"""
                **Tarea**: Revisión y Validación del README.md
                **Descripción**: Revisar el archivo README.md que ha sido generado por el arquitecto
                y verificar que contenga TODAS las secciones obligatorias con contenido adecuado.
                
                **INSTRUCCIÓN CRÍTICA**: Debes EJECUTAR obligatoriamente la herramienta 'leer_archivo_readme' 
                para leer el contenido del archivo. No supongas qué contiene; léelo completamente.

                **FORMATO OBLIGATORIO**:
                Action: leer_archivo_readme
                Action Input: {{"nombre_proyecto": "{nombre_proyecto}"}}

                **SECCIONES OBLIGATORIAS QUE DEBES VALIDAR**:
                1. DESCRIPCIÓN - Explica qué hace el sistema
                2. INSTALACIÓN - Pasos para instalar (venv, poetry install, etc.)
                3. EJECUCIÓN - Comandos para ejecutar (uvicorn, puerto de acceso, documentación)
                4. ESTRUCTURA_JSON_AUTOGENERADA (NO MODIFICAR) - Árbol de directorios o estructura de carpetas
                5. ENTIDADES Y REGLAS DE NEGOCIO - Listado de entidades y reglas de validación

                **RESULTADO ESPERADO**:
                Si TODAS las 5 secciones están presentes y tienen contenido adecuado:
                - Responde: "APROBADO: El README contiene todas las secciones obligatorias."
                
                Si FALTA alguna sección:
                - Responde: "RECHAZADO: Faltan las siguientes secciones: [listar cuáles]"
                
                **Nota**: {self.__tip_section()}
                """
            ),
            expected_output="Resultado final de validación: APROBADO o RECHAZADO con detalles específicos",
            agent=agente_revisor,
            tools=[herramienta_lectura],
        )
    '''
    '''
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
    '''
