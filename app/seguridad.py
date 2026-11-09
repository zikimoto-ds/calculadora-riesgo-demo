"""Hash de claves (scrypt, solo librería estándar) y tokens JWT."""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt

from . import config

_N, _R, _P = 2**14, 8, 1


def hashear_clave(clave: str) -> str:
    sal = os.urandom(16)
    h = hashlib.scrypt(clave.encode(), salt=sal, n=_N, r=_R, p=_P)
    return "scrypt$" + base64.b64encode(sal).decode() + "$" + base64.b64encode(h).decode()


def verificar_clave(clave: str, almacenado: str) -> bool:
    try:
        _, sal_b64, h_b64 = almacenado.split("$")
        sal = base64.b64decode(sal_b64)
        esperado = base64.b64decode(h_b64)
    except ValueError:
        return False
    h = hashlib.scrypt(clave.encode(), salt=sal, n=_N, r=_R, p=_P)
    return hmac.compare_digest(h, esperado)


def crear_token(usuario: str, rol: str) -> str:
    vence = datetime.now(timezone.utc) + timedelta(minutes=config.TOKEN_MINUTOS)
    return jwt.encode({"sub": usuario, "rol": rol, "exp": vence}, config.JWT_SECRET, algorithm="HS256")


def leer_token(token: str) -> dict:
    return jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
