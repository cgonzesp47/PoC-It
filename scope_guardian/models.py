"""
Modelos de dominio internos del ScopeGuardian.
Responsabilidad única: estructuras de datos.
"""

from dataclasses import dataclass
from enum import Enum
from pydantic import BaseModel, Field


class PlantillaUsuario(BaseModel):
    nombre: str = Field(...)
    problema: str = Field(...)
    usuarios: str = Field(...)
    funcionalidades: str = Field(...)
    limites: str = Field(...)
    tecnologias: str = Field(...)


class ModoGeneracion(str, Enum):
    COMPLETO = "COMPLETO"
    PARCIAL = "PARCIAL"
    ASESOR = "ASESOR"


@dataclass
class ResultadoViabilidad:
    modo: ModoGeneracion
    arquitectura: str
    opciones: list[str]
