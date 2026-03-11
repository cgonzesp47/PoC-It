from pydantic import BaseModel, Field
from crewai.tools.base_tool import BaseTool
import os
import json


class GenerarEstructuraArgs(BaseModel):
    nombre_proyecto: str = Field(
        ...,
        description="Nombre del proyecto dentro de la carpeta output/"
    )
    estructura: str = Field(
        ...,
        description=(
            "Estructura exacta del proyecto en formato JSON válido. "
            "Debe representar jerarquía de carpetas y archivos."
        )
    )


class GenerarEstructuraTool(BaseTool):
    name: str = "generar_estructura"
    description: str = (
        "Crea exactamente la estructura indicada en formato JSON dentro de "
        "./output/{nombre_proyecto}/ sin modificar ni añadir elementos extra."
    )
    args_schema: type[BaseModel] = GenerarEstructuraArgs

    def _crear_recursivo(self, ruta_base: str, estructura_dict: dict):
        """
        Crea carpetas y archivos recursivamente según el diccionario recibido.
        Reglas:
        - dict  -> carpeta
        - str   -> archivo con contenido
        - None  -> archivo vacío
        """

        for nombre, contenido in estructura_dict.items():
            ruta_actual = os.path.join(ruta_base, nombre)

            # Si es diccionario → carpeta
            if isinstance(contenido, dict):
                os.makedirs(ruta_actual, exist_ok=True)
                self._crear_recursivo(ruta_actual, contenido)

            # Si es string → archivo con contenido
            elif isinstance(contenido, str):
                os.makedirs(os.path.dirname(ruta_actual), exist_ok=True)
                with open(ruta_actual, "w", encoding="utf-8") as f:
                    f.write(contenido)

            # Si es None → archivo vacío
            elif contenido is None:
                os.makedirs(os.path.dirname(ruta_actual), exist_ok=True)
                open(ruta_actual, "a", encoding="utf-8").close()

            else:
                raise ValueError(
                    f"Tipo no soportado para '{nombre}'. "
                    "Debe ser dict (carpeta), str (archivo con contenido) o None (archivo vacío)."
                )

    def _run(self, nombre_proyecto: str, estructura: str, **kwargs) -> str:
        """
        Recibe la estructura en formato JSON (string),
        la convierte a dict y crea exactamente esa estructura.
        """

        try:
            # Convertir JSON string a dict
            estructura_dict = json.loads(estructura)

            if not isinstance(estructura_dict, dict):
                return "Error: La estructura debe ser un JSON con objeto raíz tipo diccionario."

            ruta_base = os.path.join("output", nombre_proyecto)
            os.makedirs(ruta_base, exist_ok=True)

            self._crear_recursivo(ruta_base, estructura_dict)

            return (
                f"Estructura creada correctamente en: "
                f"{os.path.abspath(ruta_base)}"
            )

        except json.JSONDecodeError:
            return "Error: La estructura proporcionada no es un JSON válido."

        except Exception as e:
            return f"Error al generar la estructura: {str(e)}"


generar_estructura_tool = GenerarEstructuraTool()
