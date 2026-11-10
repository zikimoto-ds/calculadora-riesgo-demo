"""Conexión a la base de datos (PostgreSQL en despliegue, SQLite para desarrollo y pruebas)."""
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config


def crear_motor(url: str | URL | None = None):
    url = make_url(url if url is not None else config.database_url())
    opciones = {"pool_pre_ping": True}
    if url.get_backend_name() == "sqlite":
        opciones["connect_args"] = {"check_same_thread": False}
    elif url.get_backend_name() == "postgresql":
        # Sesión en UTC: los cortes de día del dashboard no dependen de la zona del servidor.
        opciones["connect_args"] = {"options": "-c TimeZone=UTC"}
    return create_engine(url, **opciones)


motor = crear_motor()
SesionLocal = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SesionLocal()
    try:
        yield db
    finally:
        db.close()
