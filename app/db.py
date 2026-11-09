"""Conexión a la base de datos (PostgreSQL en despliegue, SQLite para desarrollo y pruebas)."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config


def crear_motor(url: str = config.DATABASE_URL):
    opciones = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        opciones["connect_args"] = {"check_same_thread": False}
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
