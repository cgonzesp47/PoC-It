from pydantic import BaseModel, Field
from crewai.tools.base_tool import BaseTool
import os


class LeerArchivoReadmeArgs(BaseModel):
    nombre_proyecto: str = Field(..., description="Nombre del proyecto para localizar el directorio")


class LeerArchivoReadmeTool(BaseTool):
    name: str = "leer_archivo_readme"
    description: str = "Lee el contenido del archivo README.md desde ./output/{nombre_proyecto}/."
    args_schema: type[BaseModel] = LeerArchivoReadmeArgs

    def _run(self, nombre_proyecto: str, **kwargs) -> str:
        try:
            readme_path = os.path.join("output", nombre_proyecto, "README.md")
            with open(readme_path, "r", encoding="utf-8") as f:
                contenido = f.read()
            return contenido
        except FileNotFoundError:
            return f"Error: El archivo README.md no existe en output/{nombre_proyecto}/"
        except Exception as e:
            return f"Error al leer el archivo: {str(e)}"


leer_archivo_readme_tool = LeerArchivoReadmeTool()
