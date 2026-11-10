"""Reporte diario en Excel a partir de la bitácora de cálculos.

    python -m scripts.reporte_diario                                   # ayer, corte UTC
    python -m scripts.reporte_diario --fecha 2026-01-15 --tz America/Mexico_City
    python -m scripts.reporte_diario --s3-bucket mi-bucket             # además lo sube a S3 (requiere boto3)

El corte del día usa la zona horaria de --tz: con una operación en CDMX, un cálculo
de las 19:00 locales pertenece a ese día, no al siguiente día UTC. `decision_final`
incorpora la resolución manual de los casos que cayeron en revisión.
Es lo que reemplaza el armado manual: en AWS lo dispara EventBridge Scheduler como tarea de ECS.
"""
import argparse
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select

from app.db import motor
from app.models import CalculoLog, Revision


def construir(fecha: date, tz: ZoneInfo) -> tuple[pd.DataFrame, pd.DataFrame]:
    ini = datetime.combine(fecha, time.min, tzinfo=tz)
    fin = ini + timedelta(days=1)
    df = pd.read_sql(
        select(
            CalculoLog.id,
            CalculoLog.creado,
            CalculoLog.usuario,
            CalculoLog.producto,
            CalculoLog.decision,
            CalculoLog.score,
            CalculoLog.decil,
            CalculoLog.monto_aprobado,
            Revision.estado.label("resolucion"),
            Revision.resuelto_por,
        )
        .join_from(CalculoLog, Revision, Revision.calculo_id == CalculoLog.id, isouter=True)
        .where(CalculoLog.creado >= ini, CalculoLog.creado < fin)
        .order_by(CalculoLog.id),
        motor,
    )
    if df.empty:
        return pd.DataFrame({"mensaje": [f"Sin cálculos el {fecha}"]}), df

    # Decisión final: la automática, sustituida por la resolución manual cuando la hubo.
    en_revision = df["decision"] == "revision"
    final = df["decision"].copy()
    final[en_revision & (df["resolucion"] == "aprobada")] = "aprobado (manual)"
    final[en_revision & (df["resolucion"] == "rechazada")] = "rechazo (manual)"
    final[en_revision & ~df["resolucion"].isin(["aprobada", "rechazada"])] = "revision pendiente"
    df.insert(5, "decision_final", final)

    creado = df["creado"]
    if getattr(creado.dt, "tz", None) is None:  # SQLite devuelve naive en UTC
        creado = creado.dt.tz_localize("UTC")
    df["creado"] = creado.dt.tz_convert(tz).dt.tz_localize(None)  # hora local del corte; Excel no admite zonas

    resumen = (
        df.groupby(["producto", "decision_final"])
        .agg(casos=("id", "count"), score_promedio=("score", "mean"), monto_total=("monto_aprobado", "sum"))
        .round(3)
        .reset_index()
    )
    return resumen, df


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fecha", type=date.fromisoformat, default=None, help="por defecto, ayer en la zona de --tz")
    p.add_argument("--tz", default="UTC", help="zona horaria del corte del día, p. ej. America/Mexico_City")
    p.add_argument("--salida", type=Path, default=Path("reportes"))
    p.add_argument("--s3-bucket", default=None)
    args = p.parse_args()

    tz = ZoneInfo(args.tz)
    fecha = args.fecha or (datetime.now(tz).date() - timedelta(days=1))
    resumen, detalle = construir(fecha, tz)
    args.salida.mkdir(parents=True, exist_ok=True)
    ruta = args.salida / f"reporte_{fecha}.xlsx"
    with pd.ExcelWriter(ruta, engine="openpyxl") as xlsx:
        resumen.to_excel(xlsx, sheet_name="Resumen", index=False)
        if not detalle.empty:
            detalle.to_excel(xlsx, sheet_name="Detalle", index=False)
    print(f"{ruta} ({len(detalle)} cálculos)")

    if args.s3_bucket:
        import boto3

        clave = f"reportes/{fecha:%Y/%m}/{ruta.name}"
        boto3.client("s3").upload_file(str(ruta), args.s3_bucket, clave, ExtraArgs={"ServerSideEncryption": "aws:kms"})
        print(f"subido a s3://{args.s3_bucket}/{clave}")


if __name__ == "__main__":
    main()
