"""Reporte diario en Excel a partir de la bitácora de cálculos.

    python -m scripts.reporte_diario                      # ayer (UTC)
    python -m scripts.reporte_diario --fecha 2026-01-15
    python -m scripts.reporte_diario --s3-bucket mi-bucket  # además lo sube a S3 (requiere boto3)

Es lo que reemplaza el armado manual: en AWS lo dispara EventBridge Scheduler como tarea de ECS.
"""
import argparse
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from app.db import motor
from app.models import CalculoLog


def construir(fecha: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    ini = datetime.combine(fecha, time.min, tzinfo=timezone.utc)
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
        )
        .where(CalculoLog.creado >= ini, CalculoLog.creado < fin)
        .order_by(CalculoLog.id),
        motor,
    )
    if df.empty:
        return pd.DataFrame({"mensaje": [f"Sin cálculos el {fecha}"]}), df
    if getattr(df["creado"].dt, "tz", None) is not None:  # Excel no admite zonas horarias
        df["creado"] = df["creado"].dt.tz_convert(None)
    resumen = (
        df.groupby(["producto", "decision"])
        .agg(casos=("id", "count"), score_promedio=("score", "mean"), monto_total=("monto_aprobado", "sum"))
        .round(3)
        .reset_index()
    )
    return resumen, df


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fecha", type=date.fromisoformat, default=datetime.now(timezone.utc).date() - timedelta(days=1))
    p.add_argument("--salida", type=Path, default=Path("reportes"))
    p.add_argument("--s3-bucket", default=None)
    args = p.parse_args()

    resumen, detalle = construir(args.fecha)
    args.salida.mkdir(parents=True, exist_ok=True)
    ruta = args.salida / f"reporte_{args.fecha}.xlsx"
    with pd.ExcelWriter(ruta, engine="openpyxl") as xlsx:
        resumen.to_excel(xlsx, sheet_name="Resumen", index=False)
        if not detalle.empty:
            detalle.to_excel(xlsx, sheet_name="Detalle", index=False)
    print(f"{ruta} ({len(detalle)} cálculos)")

    if args.s3_bucket:
        import boto3  # solo si se pide subir a S3

        clave = f"reportes/{args.fecha:%Y/%m}/{ruta.name}"
        boto3.client("s3").upload_file(str(ruta), args.s3_bucket, clave, ExtraArgs={"ServerSideEncryption": "aws:kms"})
        print(f"subido a s3://{args.s3_bucket}/{clave}")


if __name__ == "__main__":
    main()
