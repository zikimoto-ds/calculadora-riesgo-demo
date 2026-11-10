"""Módulo de administración de usuarios: alta, listado, activar/desactivar y cambio de clave."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config, seguridad
from .autenticacion import (
    _bloqueado,
    bloquear_al_limite,
    reservar_intento,
    sin_bloqueo_vigente,
    solo_supervisor,
    usuario_actual,
)
from .db import get_db
from .esquemas import ActivarUsuario, CambioClave, NuevoUsuario
from .models import Auditoria, Usuario, auditar, utc

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])


@router.post("", status_code=201)
def crear_usuario(
    nuevo: NuevoUsuario, supervisor: Usuario = Depends(solo_supervisor), db: Session = Depends(get_db)
):
    if db.scalar(select(Usuario).where(Usuario.usuario == nuevo.usuario)) is not None:
        raise HTTPException(409, "El usuario ya existe")
    db.add(Usuario(usuario=nuevo.usuario, clave_hash=seguridad.hashear_clave(nuevo.clave), rol=nuevo.rol))
    db.add(Auditoria(usuario=supervisor.usuario, accion="crear_usuario", detalle=f"{nuevo.usuario} ({nuevo.rol})"))
    try:
        db.commit()
    except IntegrityError:  # dos peticiones simultáneas con el mismo nombre: la segunda choca con el índice único
        db.rollback()
        raise HTTPException(409, "El usuario ya existe")
    return {"usuario": nuevo.usuario, "rol": nuevo.rol}


@router.get("")
def listar_usuarios(_: Usuario = Depends(solo_supervisor), db: Session = Depends(get_db)):
    """Listado para administración. Nunca devuelve el hash de la clave."""
    usuarios = db.scalars(select(Usuario).order_by(Usuario.usuario)).all()
    return [
        {
            "usuario": u.usuario,
            "rol": u.rol,
            "activo": u.activo,
            "bloqueado": _bloqueado(u),
            "creado": utc(u.creado),
        }
        for u in usuarios
    ]


@router.patch("/{nombre}/activo")
def activar_desactivar(
    nombre: str,
    cuerpo: ActivarUsuario,
    supervisor: Usuario = Depends(solo_supervisor),
    db: Session = Depends(get_db),
):
    """Desactivar o reactivar sube la versión de token: los tokens emitidos antes quedan inválidos."""
    if nombre == supervisor.usuario:
        raise HTTPException(403, "No puedes desactivar tu propia cuenta")
    u = db.scalar(select(Usuario).where(Usuario.usuario == nombre))
    if u is None:
        raise HTTPException(404, "Usuario no encontrado")
    # Incremento atómico en la base: dos cambios simultáneos no pierden una revocación.
    db.execute(
        update(Usuario)
        .where(Usuario.id == u.id)
        .values(activo=cuerpo.activo, version_token=Usuario.version_token + 1)
        .execution_options(synchronize_session=False)
    )
    auditar(db, supervisor.usuario, "activar_usuario" if cuerpo.activo else "desactivar_usuario", nombre)
    return {"usuario": u.usuario, "activo": cuerpo.activo}


@router.post("/me/clave")
def cambiar_mi_clave(
    cuerpo: CambioClave, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)
):
    # Mismo contador y misma reserva previa que el login: un token robado no da
    # intentos ilimitados contra la clave actual, ni siquiera en ráfaga concurrente.
    intentos = reservar_intento(db, usuario)
    if intentos is None or intentos > config.MAX_INTENTOS:
        auditar(db, usuario.usuario, "cambio_clave_rechazado", "cuenta bloqueada")
        raise HTTPException(403, "La clave actual no coincide")
    if not seguridad.verificar_clave(cuerpo.clave_actual, usuario.clave_hash):
        bloquear_al_limite(db, usuario, intentos)
        auditar(db, usuario.usuario, "cambio_clave_rechazado", "clave actual incorrecta")
        raise HTTPException(403, "La clave actual no coincide")
    # Cambio condicional: exige el hash que se verificó (dos cambios simultáneos: uno gana),
    # sin bloqueo vigente, e incrementa la versión de token de forma atómica.
    version = db.execute(
        update(Usuario)
        .where(Usuario.id == usuario.id, Usuario.clave_hash == usuario.clave_hash, sin_bloqueo_vigente())
        .values(
            clave_hash=seguridad.hashear_clave(cuerpo.clave_nueva),
            version_token=Usuario.version_token + 1,
            intentos_fallidos=0,
        )
        .returning(Usuario.version_token)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if version is None:
        db.rollback()
        auditar(db, usuario.usuario, "cambio_clave_rechazado", "conflicto concurrente o bloqueo")
        raise HTTPException(409, "La clave cambió en otra sesión; vuelve a iniciar")
    auditar(db, usuario.usuario, "cambio_clave")
    return {
        "usuario": usuario.usuario,
        "detalle": "clave actualizada",
        "token": seguridad.crear_token(usuario.usuario, usuario.rol, version),
    }
