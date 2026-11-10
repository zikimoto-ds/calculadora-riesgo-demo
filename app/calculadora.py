"""Decisión: reglas duras primero, después el ensamble de modelos y el score externo.

Los umbrales viven en config/reglas.json y se cargan una vez por proceso:
cambiar el archivo exige reiniciar la API (la versión aplicada queda en cada cálculo).

Orden de evaluación:
1. Reglas duras (edad, mora, endeudamiento, política de monto y plazo) → rechazo.
2. Score del ensamble (regresión logística + XGBoost) contra los cortes del producto.
3. Decil del proveedor externo: si no responde, el caso va a revisión manual en vez
   de decidirse sin el dato; si viene bajo, también va a revisión.
4. Oferta final: el monto se ajusta por un factor según el decil interno.
"""
import json
from functools import lru_cache
from pathlib import Path

from . import modelo, score_externo

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

    m = modelo.predecir(
        {
            "edad": d["edad"],
            "ingreso_mensual": d["ingreso_mensual"],
            "antiguedad_meses": d["antiguedad_meses"],
            "mora_promedio_dias": d["mora_promedio_dias"],
            "creditos_previos": d["creditos_previos"],
            "deuda_ingreso": deuda_ingreso,
        }
    )
    score, decil = m["score"], m["decil"]
    externo = score_externo.consultar(d)
    decil_externo = externo["decil"] if externo else None

    if motivos:
        decision = "rechazo"
    elif score < cfg["cortes"]["rechazo_bajo"]:
        decision, motivos = "rechazo", ["SCORE_BAJO"]
    elif externo is None:
        decision, motivos = "revision", ["SIN_SCORE_EXTERNO"]
    elif decil_externo <= cfg["externo"]["decil_revision"]:
        decision, motivos = "revision", ["SCORE_EXTERNO_EN_ZONA_BAJA"]
    elif score < cfg["cortes"]["revision_bajo"]:
        decision, motivos = "revision", ["SCORE_EN_ZONA_DE_REVISION"]
    else:
        decision = "aprobado"

    monto = 0.0
    if decision != "rechazo":
        tope = d["ingreso_mensual"] * cfg["multiplo_ingreso_max"]
        factor = cfg["factor_oferta_decil"][decil - 1]
        monto = round(min(d["monto_solicitado"], tope * factor, cfg["monto_max"]), 2)

    return {
        "decision": decision,
        "score": round(score, 4),
        "decil": decil,
        "decil_externo": decil_externo,
        "modelos": m["modelos"],
        "motivos": motivos,
        "monto_aprobado": monto,
        "deuda_ingreso": round(deuda_ingreso, 3),
        "version_reglas": reglas["version"],
    }
