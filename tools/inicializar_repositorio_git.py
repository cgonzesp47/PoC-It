from pydantic import BaseModel, Field
from crewai.tools.base_tool import BaseTool
import subprocess
import os


class InicializarRepositorioGitArgs(BaseModel):
    nombre_proyecto: str = Field(..., description="Nombre del proyecto para el mensaje del commit inicial")


class InicializarRepositorioGitTool(BaseTool):
    name: str = "inicializar_repositorio_git"
    description: str = "Inicializa un repositorio Git en ./output/{nombre_proyecto}/ y realiza el commit inicial."
    args_schema: type[BaseModel] = InicializarRepositorioGitArgs

    def _run(self, nombre_proyecto: str, **kwargs) -> str:
        original_dir = os.getcwd()
        try:
            # Crear directorio de salida si no existe
            output_dir = os.path.join("output", nombre_proyecto)
            os.makedirs(output_dir, exist_ok=True)
            
            # Cambiar al directorio del proyecto
            os.chdir(output_dir)
            
            # Verificar si ya existe un repositorio Git
            if os.path.exists(".git"):
                # Si ya existe, solo hacer commit de cambios nuevos
                subprocess.run(["git", "add", "."], check=True, capture_output=True, text=True)
                
                # Verificar si hay cambios para commitear
                status_result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    check=True,
                    capture_output=True,
                    text=True
                )
                
                if status_result.stdout.strip():
                    # Hay cambios, hacer commit
                    commit_message = f"Update: {nombre_proyecto}"
                    subprocess.run(
                        ["git", "commit", "-m", commit_message],
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    ruta_repositorio = os.path.abspath(".")
                    os.chdir(original_dir)
                    return f"Repositorio Git ya existía. Cambios commiteados: '{commit_message}' en {ruta_repositorio}"
                else:
                    # No hay cambios
                    ruta_repositorio = os.path.abspath(".")
                    os.chdir(original_dir)
                    return f"Repositorio Git ya existía. No hay cambios para commitear en {ruta_repositorio}"
            
            # Si no existe, inicializar nuevo repositorio
            subprocess.run(["git", "init"], check=True, capture_output=True, text=True)
            subprocess.run(["git", "add", "."], check=True, capture_output=True, text=True)
            
            commit_message = f"Initial commit: {nombre_proyecto}"
            subprocess.run(
                ["git", "commit", "-m", commit_message],
                check=True,
                capture_output=True,
                text=True
            )
            
            ruta_repositorio = os.path.abspath(".")
            os.chdir(original_dir)
            return f"Repositorio Git inicializado exitosamente en {ruta_repositorio}. Commit: '{commit_message}'"
        
        except subprocess.CalledProcessError as e:
            os.chdir(original_dir)
            return f"Error ejecutando comando Git: {e.stderr if e.stderr else str(e)}"
        except Exception as e:
            os.chdir(original_dir)
            return f"Error al inicializar el repositorio: {str(e)}"


inicializar_repositorio_git_tool = InicializarRepositorioGitTool()
