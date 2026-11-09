import os

# Antes de importar la app: sin archivos en disco y con un secreto solo para pruebas.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "secreto-solo-para-pruebas-0123456789abcdef"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import RefCliente, Usuario
from app.seguridad import hashear_clave

CLAVE_ANA = "clave-de-prueba-ana-1"
CLAVE_SUP = "clave-de-prueba-sup-1"

SOLICITUD_OK = {
    "cliente_id": None,
    "edad": 35,
    "ingreso_mensual": 25000,
    "antiguedad_meses": 60,
    "mora_promedio_dias": 2,
    "creditos_previos": 3,
    "deuda_mensual": 4000,
    "monto_solicitado": 20000,
    "plazo_meses": 12,
}


@pytest.fixture()
def cliente():
    motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(motor)
    Sesion = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)

    def _get_db():
        db = Sesion()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    with Sesion() as db:
        db.add_all(
            [
                Usuario(usuario="ana", clave_hash=hashear_clave(CLAVE_ANA), rol="analista"),
                Usuario(usuario="sup", clave_hash=hashear_clave(CLAVE_SUP), rol="supervisor"),
                RefCliente(
                    cliente_id="CLI-000001",
                    edad=40,
                    ingreso_mensual=18000,
                    antiguedad_meses=90,
                    mora_promedio_dias=1.5,
                    creditos_previos=2,
                    deuda_mensual=3000,
                ),
            ]
        )
        db.commit()
    yield TestClient(app)  # sin `with`: no corre el lifespan sobre la base por defecto
    app.dependency_overrides.clear()
    motor.dispose()


def encabezados(cliente, usuario, clave):
    r = cliente.post("/api/auth/login", json={"usuario": usuario, "clave": clave})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}
