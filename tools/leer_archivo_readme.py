from pydantic import BaseModel
from crewai.tools.base_tool import BaseTool


class LeerArchivoReadmeArgs(BaseModel):
    # Esta herramienta no necesita argumentos, pero CrewAI requiere un esquema
    # Dejamos la clase vacía con pass o sin campos
    pass


class LeerArchivoReadmeTool(BaseTool):
    name: str = "leer_archivo_readme"
    description: str = "Lee el contenido del archivo README.md que se ha generado."
    args_schema: type[BaseModel] = LeerArchivoReadmeArgs

    def _run(self) -> str:
        try:
            with open("README.md", "r", encoding="utf-8") as f:
                contenido = f.read()
            return contenido
        except FileNotFoundError:
            return "Error: El archivo README.md no existe aún."
        except Exception as e:
            return f"Error al leer el archivo: {str(e)}"


leer_archivo_readme_tool = LeerArchivoReadmeTool()
