from pydantic import BaseModel, Field
from crewai.tools.base_tool import BaseTool


class EscribirArchivoReadmeArgs(BaseModel):
    # Esquema de entrada que CrewAI valida antes de llamar a la tool
    contenido: str = Field(..., description="Contenido del archivo README.md")


class EscribirArchivoReadmeTool(BaseTool):
    name: str = "escribir_archivo_readme"
    description: str = "Guarda el contenido del README.md en el disco."
    # CrewAI espera la clase del esquema, no una instancia
    args_schema: type[BaseModel] = EscribirArchivoReadmeArgs

    def _run(self, contenido: str) -> str:
        try:
            with open("README.md", "w", encoding="utf-8") as f:
                f.write(contenido)
            return "Archivo README.md guardado exitosamente."
        except Exception as e:
            return f"Error al guardar: {str(e)}"


escribir_archivo_readme_tool = EscribirArchivoReadmeTool()