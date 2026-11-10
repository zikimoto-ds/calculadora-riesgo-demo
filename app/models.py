"""Tablas. Cada cálculo queda guardado con su entrada, su resultado y la versión de reglas usada."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def utc(dt: datetime | None) -> datetime | None:
    """Añade la zona UTC a fechas naive: SQLite guarda y devuelve sin zona; PostgreSQL con ella."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    clave_hash: Mapped[str] = mapped_column(String(300))
    rol: Mapped[str] = mapped_column(String(20), default="analista")  # analista | supervisor
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)


class RefCliente(Base):
    """Tabla de referencia para autocompletar la captura (datos sintéticos)."""

    __tablename__ = "ref_cliente"

    cliente_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    edad: Mapped[int] = mapped_column(Integer)
    ingreso_mensual: Mapped[float] = mapped_column(Float)
    antiguedad_meses: Mapped[int] = mapped_column(Integer)
    mora_promedio_dias: Mapped[float] = mapped_column(Float)
    creditos_previos: Mapped[int] = mapped_column(Integer)
    deuda_mensual: Mapped[float] = mapped_column(Float)


class CalculoLog(Base):
    __tablename__ = "calculo_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora, index=True)
    usuario: Mapped[str] = mapped_column(String(50))
    producto: Mapped[str] = mapped_column(String(30))
    cliente_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    entrada: Mapped[dict] = mapped_column(JSON)
    score: Mapped[float] = mapped_column(Float)
    decil: Mapped[int] = mapped_column(Integer)
    decil_externo: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = el proveedor no respondió
    modelos: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # probabilidad y decil por modelo del ensamble
    decision: Mapped[str] = mapped_column(String(20))  # aprobado | revision | rechazo
    motivos: Mapped[list] = mapped_column(JSON)
    monto_aprobado: Mapped[float] = mapped_column(Float)
    version_reglas: Mapped[str] = mapped_column(String(30))


class Revision(Base):
    """Casos en zona gris que un supervisor resuelve a mano."""

    __tablename__ = "revision_manual"

    id: Mapped[int] = mapped_column(primary_key=True)
    calculo_id: Mapped[int] = mapped_column(ForeignKey("calculo_log.id"), index=True)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente", index=True)  # pendiente | aprobada | rechazada
    comentario: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resuelto_por: Mapped[str | None] = mapped_column(String(50), nullable=True)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora)
    resuelto_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Auditoria(Base):
    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ahora, index=True)
    usuario: Mapped[str | None] = mapped_column(String(50), nullable=True)
    accion: Mapped[str] = mapped_column(String(50))
    detalle: Mapped[str] = mapped_column(String(300), default="")


def auditar(db, usuario: str | None, accion: str, detalle: str = "") -> None:
    """Agrega la entrada de bitácora y confirma la transacción completa."""
    db.add(Auditoria(usuario=usuario, accion=accion, detalle=detalle[:300]))
    db.commit()
