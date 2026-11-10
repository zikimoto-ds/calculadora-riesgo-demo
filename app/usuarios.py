"""Módulo de administración de usuarios: alta, listado, activar/desactivar y cambio de clave."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import seguridad
from .autenticacion import _bloqueado, solo_supervisor, usuario_actual
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
    """Desactivar también invalida los tokens vigentes del usuario (usuario_actual exige activo)."""
    if nombre == supervisor.usuario:
        raise HTTPException(403, "No puedes desactivar tu propia cuenta")
    u = db.scalar(select(Usuario).where(Usuario.usuario == nombre))
    if u is None:
        raise HTTPException(404, "Usuario no encontrado")
    u.activo = cuerpo.activo
    auditar(db, supervisor.usuario, "activar_usuario" if cuerpo.activo else "desactivar_usuario", nombre)
    return {"usuario": u.usuario, "activo": u.activo}


@router.post("/me/clave")
def cambiar_mi_clave(
    cuerpo: CambioClave, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)
):
    if not seguridad.verificar_clave(cuerpo.clave_actual, usuario.clave_hash):
        auditar(db, usuario.usuario, "cambio_clave_rechazado", "clave actual incorrecta")
        raise HTTPException(403, "La clave actual no coincide")
    usuario.clave_hash = seguridad.hashear_clave(cuerpo.clave_nueva)
    auditar(db, usuario.usuario, "cambio_clave")
    return {"usuario": usuario.usuario, "detalle": "clave actualizada"}
