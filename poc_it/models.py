"""
Modelos de dominio internos del ScopeGuardian.
Responsabilidad única: estructuras de datos.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class PlantillaUsuario(BaseModel):
    nombre: str = Field(...)
    problema: str = Field(...)
    usuarios: str = Field(...)
    funcionalidades: str = Field(...)
    limites: str = Field(...)
    tecnologias: str = Field(...)


class ContratoAPIRequest(BaseModel):
    type: str = Field(default="none", description="json | multipart | query | none")
    schema_hint: Dict[str, Any] = Field(default_factory=dict)
    evidence: str = ""
    assumption: str = ""


class ContratoAPIResponse(BaseModel):
    json_example: Dict[str, Any] = Field(default_factory=dict)
    evidence: str = ""


class ContratoAPI(BaseModel):
    method: str
    path: str
    request: ContratoAPIRequest = Field(default_factory=ContratoAPIRequest)
    response: ContratoAPIResponse = Field(default_factory=ContratoAPIResponse)
    notes: str = ""


class ContextoNormalizado(BaseModel):
    """
    Representa el contexto técnico estructurado derivado de la plantilla.
    Es la versión formal y normalizada que utilizará el sistema.
    """

    objetivo_tecnico: str
    actores_principales: List[str] = Field(default_factory=list)
    funcionalidades_clave: List[str] = Field(default_factory=list)
    integraciones_externas: List[str] = Field(default_factory=list)
    restricciones_tecnicas: List[str] = Field(default_factory=list)
    requisitos_no_funcionales: List[str] = Field(default_factory=list)
    riesgos_inherentes: List[str] = Field(default_factory=list)
    complejidad_inferida: str = "MEDIA"
    modo_recomendado: str = "PARCIAL"

    # Contratos estructurados (fuente de verdad para request/response cuando exista evidencia)
    contratos_api: List[ContratoAPI] = Field(default_factory=list)


class ModoGeneracion(str, Enum):
    COMPLETO = "COMPLETO"
    PARCIAL = "PARCIAL"
    ASESOR = "ASESOR"


@dataclass
class ResultadoViabilidad:
    modo: ModoGeneracion
    arquitectura: str
    opciones: list[str]
    contexto_proyecto: "ProjectContext"
    t_clasificacion_inicio: float | None = None
    t_clasificacion_fin: float | None = None


class Decision(BaseModel):
    """
    Representa una decisión técnica tomada durante la ejecución.
    Permite trazar por qué se eligió una arquitectura o enfoque concreto.
    """
    descripcion: str
    justificacion: str
    impacto: Optional[str] = None


class ProjectContext(BaseModel):
    """
    Contexto central de ejecución.
    Se inicializa con la plantilla del usuario y se va enriqueciendo
    progresivamente en cada fase del sistema multiagente.
    """

    # Entrada inicial
    plantilla: Optional[PlantillaUsuario] = None
    raw_input: Optional[str] = None

    # Fases de análisis
    clasificacion: Optional[str] = None
    viabilidad: Optional[ResultadoViabilidad] = None
    arquitectura: Optional[str] = None
    estimacion_esfuerzo: Optional[str] = None

    # Contexto técnico formal (nuevo modelo normalizado)
    contexto_normalizado: Optional[ContextoNormalizado] = None

    # Generación
    artefactos_generados: Dict[str, str] = Field(default_factory=dict)

    # Conocimiento estructurado acumulado
    decisiones: List[Decision] = Field(default_factory=list)
    riesgos: List[str] = Field(default_factory=list)
    supuestos: List[str] = Field(default_factory=list)

    # Metadatos de ejecución
    modelo_por_fase: Dict[str, str] = Field(default_factory=dict)

    def agregar_decision(self, descripcion: str, justificacion: str, impacto: Optional[str] = None) -> None:
        self.decisiones.append(
            Decision(
                descripcion=descripcion,
                justificacion=justificacion,
                impacto=impacto
            )
        )

    def registrar_modelo(self, fase: str, modelo: str) -> None:
        """
        Permite registrar qué modelo se utilizó en cada fase.
        Facilita trazabilidad y futura optimización.
        """
        self.modelo_por_fase[fase] = modelo
