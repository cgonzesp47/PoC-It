"""
Modelos de dominio internos del ScopeGuardian.
Responsabilidad única: estructuras de datos.
"""

from dataclasses import dataclass
from pydantic import BaseModel, Field


class PlantillaUsuario(BaseModel):
    nombre: str = Field(...)
    problema: str = Field(...)
    usuarios: str = Field(...)
    funcionalidades: str = Field(...)
    limites: str = Field(...)
    tecnologias: str = Field(...)


@dataclass
class ResultadoViabilidad:
    puede_generarse_automaticamente: bool
    arquitectura: str
    opciones: list[str]
