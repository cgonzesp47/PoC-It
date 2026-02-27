from pydantic import BaseModel, Field
from crewai.tools.base_tool import BaseTool
import os


class EscribirArchivoReadmeArgs(BaseModel):
    contenido: str = Field(..., description="Contenido del archivo README.md")
    nombre_proyecto: str = Field(..., description="Nombre del proyecto para crear el directorio")


class EscribirArchivoReadmeTool(BaseTool):
    name: str = "escribir_archivo_readme"
    description: str = "Guarda el contenido del README.md en el directorio ./output/{nombre_proyecto}/."
    args_schema: type[BaseModel] = EscribirArchivoReadmeArgs

    def _run(self, contenido: str, nombre_proyecto: str, **kwargs) -> str:
        try:
            # Crear directorio de salida si no existe
            output_dir = os.path.join("output", nombre_proyecto)
            os.makedirs(output_dir, exist_ok=True)
            
            # Escribir el README en el directorio de salida
            readme_path = os.path.join(output_dir, "README.md")
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(contenido)
            
            ruta_absoluta = os.path.abspath(readme_path)
            return f"Archivo README.md guardado exitosamente en {ruta_absoluta}"
        except Exception as e:
            return f"Error al guardar: {str(e)}"


escribir_archivo_readme_tool = EscribirArchivoReadmeTool()