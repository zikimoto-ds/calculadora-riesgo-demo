"""Crea las tablas, dos usuarios de ejemplo, clientes sintéticos y un histórico de cálculos.

    python -m scripts.sembrar_datos --clientes 500 --historico 300

Las claves de los usuarios se generan al azar y se muestran una sola vez.
"""
import argparse
import random
import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import calculadora, modelo
from app.db import Base, motor
from app.models import CalculoLog, RefCliente, Revision, Usuario, ahora
from app.seguridad import hashear_clave


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--clientes", type=int, default=500)
    p.add_argument("--historico", type=int, default=300, help="cálculos de los últimos 30 días para poblar el dashboard")
    args = p.parse_args()

    Base.metadata.create_all(motor)
    datos = modelo.generar_datos(n=max(args.clientes, args.historico), semilla=11)
    azar = random.Random(11)

    with Session(motor) as db:
        for usuario, rol in (("analista1", "analista"), ("supervisor1", "supervisor")):
            if db.scalar(select(Usuario).where(Usuario.usuario == usuario)) is None:
                clave = secrets.token_urlsafe(12)
                db.add(Usuario(usuario=usuario, clave_hash=hashear_clave(clave), rol=rol))
                print(f"usuario {usuario} ({rol}) · clave: {clave}   (no se vuelve a mostrar)")

        if db.scalar(select(func.count()).select_from(RefCliente)) == 0:
            for i, fila in datos.head(args.clientes).iterrows():
                db.add(
                    RefCliente(
                        cliente_id=f"CLI-{i + 1:06d}",
                        edad=int(fila["edad"]),
                        ingreso_mensual=float(fila["ingreso_mensual"]),
                        antiguedad_meses=int(fila["antiguedad_meses"]),
                        mora_promedio_dias=float(fila["mora_promedio_dias"]),
                        creditos_previos=int(fila["creditos_previos"]),
                        deuda_mensual=float(fila["deuda_mensual"]),
                    )
                )
            print(f"{args.clientes} clientes sintéticos")

        if db.scalar(select(func.count()).select_from(CalculoLog)) == 0:
            for i, fila in datos.head(args.historico).iterrows():
                producto = azar.choice(["consumo", "microempresa"])
                entrada = {
                    "cliente_id": f"CLI-{i + 1:06d}",
                    "edad": int(fila["edad"]),
                    "ingreso_mensual": float(fila["ingreso_mensual"]),
                    "antiguedad_meses": int(fila["antiguedad_meses"]),
                    "mora_promedio_dias": float(fila["mora_promedio_dias"]),
                    "creditos_previos": int(fila["creditos_previos"]),
                    "deuda_mensual": float(fila["deuda_mensual"]),
                    "monto_solicitado": float(azar.choice(range(5000, 90001, 5000))),
                    "plazo_meses": azar.choice([6, 12, 18, 24, 36]),
                }
                r = calculadora.calcular(producto, entrada)
                creado = ahora() - timedelta(days=azar.random() * 30)
                log = CalculoLog(
                    creado=creado,
                    usuario="analista1",
                    producto=producto,
                    cliente_id=entrada["cliente_id"],
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
                    db.add(Revision(calculo_id=log.id, creado=creado))
            print(f"{args.historico} cálculos históricos")
        db.commit()


if __name__ == "__main__":
    main()
