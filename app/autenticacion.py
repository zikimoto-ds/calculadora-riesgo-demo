"""Módulo de autenticación: login con bloqueo temporal, verificación del token y roles."""
from datetime import timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from . import config, seguridad
from .db import get_db
from .esquemas import Credenciales
from .models import Usuario, ahora, auditar, utc

router = APIRouter(prefix="/api/auth", tags=["autenticación"])
bearer = HTTPBearer(auto_error=False)
_HASH_FALSO = seguridad.hashear_clave("no-es-una-clave-real")  # iguala el tiempo de respuesta ante usuarios inexistentes


def _bloqueado(usuario: Usuario) -> bool:
    return usuario.bloqueado_hasta is not None and ahora() < utc(usuario.bloqueado_hasta)


def usuario_actual(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> Usuario:
    if cred is None:
        raise HTTPException(401, "Falta el token")
    try:
        datos = seguridad.leer_token(cred.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "Token inválido o vencido")
    usuario = db.scalar(select(Usuario).where(Usuario.usuario == datos["sub"]))
    if usuario is None or not usuario.activo or _bloqueado(usuario):
        raise HTTPException(401, "Usuario inactivo")
    if datos["ver"] != usuario.version_token:  # emitido antes de un cambio de clave o de una (des)activación
        raise HTTPException(401, "Token inválido o vencido")
    return usuario


def registrar_intento_fallido(db: Session, usuario: Usuario) -> int | None:
    """Suma un intento fallido (mismo contador que el login) y bloquea la cuenta al llegar al límite.

    Devuelve el número de intento, o None si la cuenta ya estaba bloqueada.
    """
    intentos = db.execute(
        update(Usuario)
        .where(Usuario.id == usuario.id, or_(Usuario.bloqueado_hasta.is_(None), Usuario.bloqueado_hasta < ahora()))
        .values(intentos_fallidos=Usuario.intentos_fallidos + 1)
        .returning(Usuario.intentos_fallidos)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if intentos is not None and intentos >= config.MAX_INTENTOS:
        db.execute(
            update(Usuario)
            .where(Usuario.id == usuario.id)
            .values(bloqueado_hasta=ahora() + timedelta(minutes=config.BLOQUEO_MINUTOS), intentos_fallidos=0)
        )
    return intentos


def solo_supervisor(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
    if usuario.rol != "supervisor":
        raise HTTPException(403, "Requiere rol supervisor")
    return usuario


@router.post("/login")
def login(c: Credenciales, db: Session = Depends(get_db)):
    usuario = db.scalar(select(Usuario).where(Usuario.usuario == c.usuario))
    if usuario is None or not usuario.activo:
        seguridad.verificar_clave(c.clave, _HASH_FALSO)
        auditar(db, c.usuario, "login_rechazado", "inexistente o inactivo")
        raise HTTPException(401, "Credenciales inválidas")

    # Reserva el intento ANTES de verificar la clave: el UPDATE condicional no reserva
    # con la cuenta bloqueada y, en una ráfaga concurrente, solo los primeros
    # MAX_INTENTOS llegan a verificar contra el hash real; el resto responde igual
    # que una clave mala (verificación falsa para no cambiar el tiempo de respuesta).
    sin_bloqueo = or_(Usuario.bloqueado_hasta.is_(None), Usuario.bloqueado_hasta < ahora())
    intentos = db.execute(
        update(Usuario)
        .where(Usuario.id == usuario.id, sin_bloqueo)
        .values(intentos_fallidos=Usuario.intentos_fallidos + 1)
        .returning(Usuario.intentos_fallidos)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if intentos is None or intentos > config.MAX_INTENTOS:
        seguridad.verificar_clave(c.clave, _HASH_FALSO)
        auditar(db, usuario.usuario, "login_rechazado", "cuenta bloqueada")
        raise HTTPException(401, "Credenciales inválidas")

    if not seguridad.verificar_clave(c.clave, usuario.clave_hash):
        detalle = f"intento {intentos}"
        if intentos >= config.MAX_INTENTOS:
            db.execute(
                update(Usuario)
                .where(Usuario.id == usuario.id)
                .values(bloqueado_hasta=ahora() + timedelta(minutes=config.BLOQUEO_MINUTOS), intentos_fallidos=0)
            )
            detalle += f"; cuenta bloqueada {config.BLOQUEO_MINUTOS} min"
        auditar(db, usuario.usuario, "login_fallido", detalle)
        raise HTTPException(401, "Credenciales inválidas")

    # Reinicio condicional: si un bloqueo aterrizó mientras se verificaba la clave,
    # el login correcto no lo borra ni emite token.
    reinicio = db.execute(
        update(Usuario)
        .where(Usuario.id == usuario.id, or_(Usuario.bloqueado_hasta.is_(None), Usuario.bloqueado_hasta < ahora()))
        .values(intentos_fallidos=0, bloqueado_hasta=None)
        .execution_options(synchronize_session=False)
    ).rowcount
    if reinicio != 1:
        auditar(db, usuario.usuario, "login_rechazado", "cuenta bloqueada")
        raise HTTPException(401, "Credenciales inválidas")
    auditar(db, usuario.usuario, "login_ok")
    return {"token": seguridad.crear_token(usuario.usuario, usuario.rol, usuario.version_token), "rol": usuario.rol}


@router.get("/me")
def yo(usuario: Usuario = Depends(usuario_actual)):
    return {"usuario": usuario.usuario, "rol": usuario.rol}
