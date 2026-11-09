"""Decisión: reglas duras primero, después modelo y cortes. Los umbrales viven en config/reglas.json."""
import json
from functools import lru_cache
from pathlib import Path

from . import modelo

RUTA_REGLAS = Path(__file__).resolve().parent.parent / "config" / "reglas.json"


class ProductoDesconocido(KeyError):
    pass


@lru_cache(maxsize=1)
def cargar_reglas() -> dict:
    return json.loads(RUTA_REGLAS.read_text(encoding="utf-8"))


def calcular(producto: str, d: dict) -> dict:
    reglas = cargar_reglas()
    if producto not in reglas["productos"]:
        raise ProductoDesconocido(producto)
    cfg = reglas["productos"][producto]
    duras = cfg["reglas_duras"]

    deuda_ingreso = d["deuda_mensual"] / d["ingreso_mensual"]
    motivos = []
    if not duras["edad_min"] <= d["edad"] <= duras["edad_max"]:
        motivos.append("EDAD_FUERA_DE_RANGO")
    if d["mora_promedio_dias"] > duras["mora_max_dias"]:
        motivos.append("MORA_ALTA")
    if deuda_ingreso > duras["deuda_ingreso_max"]:
        motivos.append("ENDEUDAMIENTO_ALTO")
    if not cfg["monto_min"] <= d["monto_solicitado"] <= cfg["monto_max"]:
        motivos.append("MONTO_FUERA_DE_POLITICA")
    if d["plazo_meses"] > cfg["plazo_max_meses"]:
        motivos.append("PLAZO_FUERA_DE_POLITICA")

    score, decil = modelo.predecir(
        {
            "edad": d["edad"],
            "ingreso_mensual": d["ingreso_mensual"],
            "antiguedad_meses": d["antiguedad_meses"],
            "mora_promedio_dias": d["mora_promedio_dias"],
            "creditos_previos": d["creditos_previos"],
            "deuda_ingreso": deuda_ingreso,
        }
    )

    if motivos:
        decision = "rechazo"
    elif score < cfg["cortes"]["rechazo_bajo"]:
        decision, motivos = "rechazo", ["SCORE_BAJO"]
    elif score < cfg["cortes"]["revision_bajo"]:
        decision, motivos = "revision", ["SCORE_EN_ZONA_DE_REVISION"]
    else:
        decision = "aprobado"

    monto = 0.0
    if decision != "rechazo":
        tope = d["ingreso_mensual"] * cfg["multiplo_ingreso_max"]
        monto = round(min(d["monto_solicitado"], tope, cfg["monto_max"]), 2)

    return {
        "decision": decision,
        "score": round(score, 4),
        "decil": decil,
        "motivos": motivos,
        "monto_aprobado": monto,
        "deuda_ingreso": round(deuda_ingreso, 3),
        "version_reglas": reglas["version"],
    }
