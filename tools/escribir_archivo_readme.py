from pydantic import BaseModel, Field
from crewai.tools.base_tool import BaseTool


class EscribirArchivoReadmeArgs(BaseModel):
    # CrewAI necesita saber qué argumentos espera la tool, por eso creamos una clase
    # que hereda de BaseModel (define un esquema de validación para los argumentos)
    # En nuestro coso, solo necesitamos un argumento: el contenido del README.md
    contenido: str = Field(..., description="Contenido del archivo README.md")


class EscribirArchivoReadmeTool(BaseTool):
    name: str = "escribir_archivo_readme"
    description: str = "Guarda el contenido del README.md en el disco."
    # CrewAI espera la clase del esquema. Por eso el tipo es type[BaseModel]: se espera
    # una clase que herede de BaseModel, no una instancia. En este caso, 
    # EscribirArchivoReadmeArgs es la clase que define el esquema de los argumentos.
    args_schema: type[BaseModel] = EscribirArchivoReadmeArgs

    def _run(self, contenido: str) -> str:
        try:
            with open("README.md", "w", encoding="utf-8") as f:
                f.write(contenido)
            return "Archivo README.md guardado exitosamente."
        except Exception as e:
            return f"Error al guardar: {str(e)}"


escribir_archivo_readme_tool = EscribirArchivoReadmeTool()