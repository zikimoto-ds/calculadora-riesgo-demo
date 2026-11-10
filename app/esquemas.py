"""Esquemas de entrada de la API (validación con Pydantic)."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class EsquemaBase(BaseModel):
    """Rechaza NUL en cualquier campo de texto: PostgreSQL no lo admite y acabaría en un 500."""

    @field_validator("*", mode="before")
    @classmethod
    def _sin_nul(cls, v):
        if isinstance(v, str) and "\x00" in v:
            raise ValueError("carácter NUL no permitido")
        return v


class Credenciales(EsquemaBase):
    usuario: str = Field(min_length=1, max_length=50)
    clave: str = Field(min_length=1, max_length=200)


class Solicitud(EsquemaBase):
    cliente_id: str | None = Field(default=None, max_length=20)
    edad: int = Field(ge=18, le=100)
    ingreso_mensual: float = Field(ge=1, le=10_000_000)  # mínimo 1: evita ratios infinitos con ingresos ínfimos
    antiguedad_meses: int = Field(ge=0, le=720)
    mora_promedio_dias: float = Field(ge=0, le=365)
    creditos_previos: int = Field(ge=0, le=100)
    deuda_mensual: float = Field(ge=0, le=10_000_000)
    monto_solicitado: float = Field(ge=1, le=10_000_000)
    plazo_meses: int = Field(ge=1, le=120)


class ResolverRevision(EsquemaBase):
    decision: Literal["aprobada", "rechazada"]
    comentario: str | None = Field(default=None, max_length=500)


class NuevoUsuario(EsquemaBase):
    usuario: str = Field(min_length=3, max_length=50, pattern=r"^[a-z0-9._-]+$")
    clave: str = Field(min_length=12, max_length=200)
    rol: Literal["analista", "supervisor"] = "analista"


class ActivarUsuario(EsquemaBase):
    activo: bool


class CambioClave(EsquemaBase):
    clave_actual: str = Field(min_length=1, max_length=200)
    clave_nueva: str = Field(min_length=12, max_length=200)
