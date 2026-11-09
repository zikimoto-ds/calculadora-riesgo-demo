"""Configuración desde variables de entorno. No hay secretos en el código."""
import os
import secrets
import warnings

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./demo.db")
TOKEN_MINUTOS = int(os.getenv("TOKEN_MINUTOS", "60"))
MAX_INTENTOS = int(os.getenv("MAX_INTENTOS", "5"))

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    JWT_SECRET = secrets.token_urlsafe(48)
    warnings.warn(
        "JWT_SECRET no está definido: se generó uno temporal "
        "(los tokens dejan de valer al reiniciar). En despliegues, léelo de un gestor de secretos."
    )
