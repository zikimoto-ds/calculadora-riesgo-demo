"""Cliente del proveedor externo de score (simulado).

En producción esto sería una llamada HTTPS al proveedor, con timeout corto, reintentos
y bitácora de consumo; aquí se simula con una fórmula determinista sobre la entrada.
La variable de entorno SCORE_EXTERNO=off simula que el proveedor no responde: la
calculadora degrada esos casos a revisión manual en lugar de decidir sin el dato.
"""
import math
import os

PROVEEDOR = "score-externo-demo"


def disponible() -> bool:
    return os.environ.get("SCORE_EXTERNO", "on").lower() not in {"off", "0", "no"}


def consultar(d: dict) -> dict | None:
    """Devuelve {"decil": 1-10, "proveedor": ...} o None si el proveedor no responde.

    Decil 1 = mayor riesgo, 10 = menor riesgo. La fórmula es inventada y distinta
    de la del modelo interno, para que ambos scores aporten señal diferente.
    """
    if not disponible():
        return None
    deuda_ingreso = d["deuda_mensual"] / d["ingreso_mensual"]
    z = (
        0.7 * math.log(d["ingreso_mensual"] / 10000.0)
        - 0.05 * d["mora_promedio_dias"]
        + 0.002 * d["antiguedad_meses"]
        - 2.0 * deuda_ingreso
        + 0.08 * d["creditos_previos"]
        - 0.01 * abs(d["edad"] - 45)
    )
    p = 1.0 / (1.0 + math.exp(-z))
    return {"decil": min(10, max(1, int(p * 10) + 1)), "proveedor": PROVEEDOR}
