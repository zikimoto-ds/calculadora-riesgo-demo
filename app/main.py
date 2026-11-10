"""API de la calculadora de riesgo (demo con datos y reglas inventados)."""
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Literal

import jwt
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import calculadora, config, modelo, seguridad
from .db import Base, get_db, motor
from .models import Auditoria, CalculoLog, RefCliente, Revision, Usuario, ahora, utc

RAIZ = Path(__file__).resolve().parent.parent
_HASH_FALSO = seguridad.hashear_clave("no-es-una-clave-real")  # iguala el tiempo de respuesta ante usuarios inexistentes


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(motor)
    modelo.cargar_modelo()
    yield


app = FastAPI(title="Calculadora de riesgo (demo)", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")
bearer = HTTPBearer(auto_error=False)


# ---------- esquemas ----------
class EsquemaBase(BaseModel):
    """Rechaza NUL en cualquier campo de texto: PostgreSQL no lo admite y acabaría en un 500."""

    @field_validator("*", mode="before")
    @classmethod
    def _sin_nul(cls, v):
        if isinstance(v, str) and "\x00" in v:
            raise ValueError("carácter NUL no permitido")
        return v


class Credenciales(EsquemaBase):
    usuario: str = Field(min_length=1, max_length=50)
    clave: str = Field(min_length=1, max_length=200)


class Solicitud(EsquemaBase):
    cliente_id: str | None = Field(default=None, max_length=20)
    edad: int = Field(ge=18, le=100)
    ingreso_mensual: float = Field(ge=1, le=10_000_000)  # mínimo 1: evita ratios infinitos con ingresos ínfimos
    antiguedad_meses: int = Field(ge=0, le=720)
    mora_promedio_dias: float = Field(ge=0, le=365)
    creditos_previos: int = Field(ge=0, le=100)
    deuda_mensual: float = Field(ge=0, le=10_000_000)
    monto_solicitado: float = Field(ge=1, le=10_000_000)
    plazo_meses: int = Field(ge=1, le=120)


class ResolverRevision(EsquemaBase):
    decision: Literal["aprobada", "rechazada"]
    comentario: str | None = Field(default=None, max_length=500)


class NuevoUsuario(EsquemaBase):
    usuario: str = Field(min_length=3, max_length=50, pattern=r"^[a-z0-9._-]+$")
    clave: str = Field(min_length=12, max_length=200)
    rol: Literal["analista", "supervisor"] = "analista"


# ---------- seguridad ----------
def _auditar(db: Session, usuario: str | None, accion: str, detalle: str = "") -> None:
    db.add(Auditoria(usuario=usuario, accion=accion, detalle=detalle[:300]))
    db.commit()


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
    return usuario


def solo_supervisor(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
    if usuario.rol != "supervisor":
        raise HTTPException(403, "Requiere rol supervisor")
    return usuario


# ---------- rutas ----------
@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Lo consulta el balanceador de carga."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "Base de datos no disponible")
    return {"estado": "ok"}


@app.get("/", include_in_schema=False)
def inicio():
    return FileResponse(RAIZ / "static" / "index.html")


@app.post("/api/auth/login")
def login(c: Credenciales, db: Session = Depends(get_db)):
    usuario = db.scalar(select(Usuario).where(Usuario.usuario == c.usuario))
    if usuario is None or not usuario.activo or _bloqueado(usuario):
        seguridad.verificar_clave(c.clave, _HASH_FALSO)
        _auditar(db, c.usuario, "login_rechazado", "inexistente, inactivo o bloqueado")
        raise HTTPException(401, "Credenciales inválidas")
    if not seguridad.verificar_clave(c.clave, usuario.clave_hash):
        # Incremento atómico en la base: con peticiones concurrentes un += en memoria pierde intentos.
        intentos = db.execute(
            update(Usuario)
            .where(Usuario.id == usuario.id)
            .values(intentos_fallidos=Usuario.intentos_fallidos + 1)
            .returning(Usuario.intentos_fallidos)
        ).scalar_one()
        detalle = f"intento {intentos}"
        if intentos >= config.MAX_INTENTOS:
            db.execute(
                update(Usuario)
                .where(Usuario.id == usuario.id)
                .values(bloqueado_hasta=ahora() + timedelta(minutes=config.BLOQUEO_MINUTOS), intentos_fallidos=0)
            )
            detalle += f"; cuenta bloqueada {config.BLOQUEO_MINUTOS} min"
        _auditar(db, usuario.usuario, "login_fallido", detalle)
        raise HTTPException(401, "Credenciales inválidas")
    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    _auditar(db, usuario.usuario, "login_ok")
    return {"token": seguridad.crear_token(usuario.usuario, usuario.rol), "rol": usuario.rol}


@app.get("/api/auth/me")
def yo(usuario: Usuario = Depends(usuario_actual)):
    return {"usuario": usuario.usuario, "rol": usuario.rol}


@app.get("/api/lookup/cliente/{cliente_id}")
def lookup_cliente(cliente_id: str, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    c = db.get(RefCliente, cliente_id)
    if c is None:
        raise HTTPException(404, "Cliente no encontrado")
    _auditar(db, usuario.usuario, "lookup_cliente", cliente_id)  # la bitácora registra quién consultó a quién
    campos = ("edad", "ingreso_mensual", "antiguedad_meses", "mora_promedio_dias", "creditos_previos", "deuda_mensual")
    return {"cliente_id": c.cliente_id, **{k: getattr(c, k) for k in campos}}


@app.post("/api/calcular/{producto}")
def calcular(
    producto: str,
    s: Solicitud,
    usuario: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db),
):
    entrada = s.model_dump()
    try:
        r = calculadora.calcular(producto, entrada)
    except calculadora.ProductoDesconocido:
        raise HTTPException(404, f"Producto desconocido: {producto}")
    log = CalculoLog(
        usuario=usuario.usuario,
        producto=producto,
        cliente_id=s.cliente_id,
        entrada=entrada,
        score=r["score"],
        decil=r["decil"],
        decision=r["decision"],
        motivos=r["motivos"],
        monto_aprobado=r["monto_aprobado"],
        version_reglas=r["version_reglas"],
    )
    db.add(log)
    db.flush()
    if r["decision"] == "revision":
        db.add(Revision(calculo_id=log.id))
    db.add(Auditoria(usuario=usuario.usuario, accion="calcular", detalle=f"{producto}: {r['decision']} (id {log.id})"))
    db.commit()
    return {"id": log.id, **r}


@app.get("/api/revisiones")
def listar_revisiones(
    estado: Literal["pendiente", "aprobada", "rechazada"] = "pendiente",
    limite: int = Query(50, ge=1, le=200),
    _: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db),
):
    filas = db.execute(
        select(Revision, CalculoLog)
        .join(CalculoLog, CalculoLog.id == Revision.calculo_id)
        .where(Revision.estado == estado)
        .order_by(Revision.id.desc())
        .limit(limite)
    ).all()
    return [
        {
            "id": r.id,
            "calculo_id": c.id,
            "producto": c.producto,
            "capturado_por": c.usuario,
            "cliente_id": c.cliente_id,
            "entrada": c.entrada,  # el supervisor resuelve viendo los datos del caso, no solo el score
            "score": c.score,
            "decil": c.decil,
            "monto_sugerido": c.monto_aprobado,
            "motivos": c.motivos,
            "estado": r.estado,
            "creado": utc(r.creado),
        }
        for r, c in filas
    ]


@app.post("/api/revisiones/{revision_id}/resolver")
def resolver_revision(
    revision_id: int,
    cuerpo: ResolverRevision,
    supervisor: Usuario = Depends(solo_supervisor),
    db: Session = Depends(get_db),
):
    r = db.get(Revision, revision_id)
    if r is None:
        raise HTTPException(404, "Revisión no encontrada")
    calculo = db.get(CalculoLog, r.calculo_id)
    if calculo is not None and calculo.usuario == supervisor.usuario:
        raise HTTPException(403, "No puedes resolver una revisión de un cálculo capturado por ti")
    # UPDATE condicional: si dos supervisores resuelven a la vez, solo uno modifica la fila.
    resueltas = db.execute(
        update(Revision)
        .where(Revision.id == revision_id, Revision.estado == "pendiente")
        .values(
            estado=cuerpo.decision,
            comentario=cuerpo.comentario,
            resuelto_por=supervisor.usuario,
            resuelto_en=ahora(),
        )
    ).rowcount
    if resueltas != 1:
        db.rollback()
        raise HTTPException(409, "La revisión ya fue resuelta")
    db.add(Auditoria(usuario=supervisor.usuario, accion="resolver_revision", detalle=f"{revision_id}: {cuerpo.decision}"))
    db.commit()
    return {"id": revision_id, "estado": cuerpo.decision}


@app.get("/api/dashboard/resumen")
def resumen(
    dias: int = Query(30, ge=1, le=365),
    _: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db),
):
    desde = ahora() - timedelta(days=dias)
    por_decision = db.execute(
        select(CalculoLog.producto, CalculoLog.decision, func.count(), func.avg(CalculoLog.score))
        .where(CalculoLog.creado >= desde)
        .group_by(CalculoLog.producto, CalculoLog.decision)
    ).all()
    dia = func.date(CalculoLog.creado)
    por_dia = db.execute(
        select(dia, func.count()).where(CalculoLog.creado >= desde).group_by(dia).order_by(dia)
    ).all()
    pendientes = db.scalar(select(func.count()).select_from(Revision).where(Revision.estado == "pendiente"))
    resueltas = dict(
        db.execute(
            select(Revision.estado, func.count()).where(Revision.estado != "pendiente").group_by(Revision.estado)
        ).all()
    )

    productos: dict = {}
    for producto, decision, n, score_prom in por_decision:
        p = productos.setdefault(producto, {"total": 0, "decisiones": {}})
        p["decisiones"][decision] = {"n": n, "score_promedio": round(float(score_prom), 4)}
        p["total"] += n
    return {
        "dias": dias,
        "total": sum(p["total"] for p in productos.values()),
        "por_producto": productos,
        "por_dia": [{"dia": str(d), "calculos": n} for d, n in por_dia],
        "revisiones_pendientes": pendientes,
        "revisiones_resueltas": {
            "aprobada": resueltas.get("aprobada", 0),
            "rechazada": resueltas.get("rechazada", 0),
        },
    }


@app.post("/api/usuarios", status_code=201)
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
