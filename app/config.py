"""Configuración desde variables de entorno. No hay secretos en el código."""
import os

from sqlalchemy.engine import URL

TOKEN_MINUTOS = int(os.getenv("TOKEN_MINUTOS", "60"))
MAX_INTENTOS = int(os.getenv("MAX_INTENTOS", "5"))
BLOQUEO_MINUTOS = int(os.getenv("BLOQUEO_MINUTOS", "15"))

_SECRETOS_DE_EJEMPLO = {"cambia-esto", "changeme", "secret", "secreto"}


def database_url() -> str | URL:
    """DATABASE_URL directa, o las piezas sueltas (DB_HOST, DB_PASSWORD…) unidas con
    URL.create, para que una contraseña con '@' o '/' no rompa la cadena de conexión."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    if os.getenv("DB_HOST"):
        return URL.create(
            "postgresql+psycopg",
            username=os.getenv("DB_USER", "calculadora"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "calculadora"),
        )
    return "sqlite:///./demo.db"


def jwt_secret() -> str:
    """Secreto para firmar tokens. Falla si falta, si es el del ejemplo o si es corto:
    un secreto generado por proceso rompería los tokens al correr con varios workers,
    y uno de ejemplo permitiría a cualquiera firmar tokens válidos."""
    s = os.getenv("JWT_SECRET", "")
    if not s:
        raise RuntimeError(
            'Falta JWT_SECRET. Genera uno: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    if s.lower() in _SECRETOS_DE_EJEMPLO or len(s) < 32:
        raise RuntimeError("JWT_SECRET es el del ejemplo o demasiado corto (mínimo 32 caracteres).")
    return s
