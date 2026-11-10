"""API de la calculadora de riesgo (demo con datos y reglas inventados).

Módulos: autenticacion (login/token/roles), usuarios (administración), calculadora
(reglas + ensamble de modelos + score externo), revisiones (zona gris con
maker-checker) y dashboard. Las rutas de cálculo y revisión viven aquí.
"""
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from . import autenticacion, calculadora, modelo, usuarios
from .autenticacion import solo_supervisor, usuario_actual
from .db import Base, get_db, motor
from .esquemas import ResolverRevision, Solicitud
from .models import Auditoria, CalculoLog, RefCliente, Revision, Usuario, ahora, auditar, utc

RAIZ = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(motor)
    modelo.cargar_modelos()
    yield


app = FastAPI(title="Calculadora de riesgo (demo)", version="0.3.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")
app.include_router(autenticacion.router)
app.include_router(usuarios.router)


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


@app.get("/api/lookup/cliente/{cliente_id}")
def lookup_cliente(cliente_id: str, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    c = db.get(RefCliente, cliente_id)
    if c is None:
        raise HTTPException(404, "Cliente no encontrado")
    auditar(db, usuario.usuario, "lookup_cliente", cliente_id)  # la bitácora registra quién consultó a quién
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
        decil_externo=r["decil_externo"],
        modelos=r["modelos"],
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
    usuario: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db),
):
    filas = db.execute(
        select(Revision, CalculoLog)
        .join(CalculoLog, CalculoLog.id == Revision.calculo_id)
        .where(Revision.estado == estado)
        .order_by(Revision.id.desc())
        .limit(limite)
    ).all()

    def ve_el_caso(c: CalculoLog) -> bool:
        # Los datos del caso solo los ve quien resuelve (supervisor) o quien lo capturó.
        return usuario.rol == "supervisor" or c.usuario == usuario.usuario

    return [
        {
            "id": r.id,
            "calculo_id": c.id,
            "producto": c.producto,
            "capturado_por": c.usuario,
            "cliente_id": c.cliente_id if ve_el_caso(c) else None,
            "entrada": c.entrada if ve_el_caso(c) else None,
            "score": c.score,
            "decil": c.decil,
            "decil_externo": c.decil_externo,
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
    _, metricas, _ = modelo.cargar_modelos()
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
        "modelos": {n: {k: round(v, 3) for k, v in m.items()} for n, m in metricas.items()},
    }
