"""Modelo de ejemplo: regresión logística entrenada con datos inventados.

No hay archivos .pkl en el repositorio: el modelo se entrena al arrancar (menos de 2 s) con semilla fija.
Con datos reales, el corte train/test debe ser temporal y respetar la maduración del target;
aquí los datos no tienen fecha, así que el corte es simplemente por posición.
"""
from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

VARIABLES = [
    "edad",
    "ingreso_mensual",
    "antiguedad_meses",
    "mora_promedio_dias",
    "creditos_previos",
    "deuda_ingreso",
]


def generar_datos(n: int = 8000, semilla: int = 7) -> pd.DataFrame:
    """Población sintética. `pago` = 1 si el cliente pagó (bueno)."""
    rng = np.random.default_rng(semilla)
    edad = rng.integers(21, 70, n)
    ingreso = np.round(rng.lognormal(mean=9.6, sigma=0.5, size=n), -2)
    antiguedad = rng.integers(0, 240, n)
    mora = np.clip(rng.gamma(1.2, 6.0, n), 0, 120).round(1)
    previos = rng.integers(0, 8, n)
    deuda_ingreso = rng.beta(2, 6, n)
    z = (
        1.2
        + 0.015 * (edad - 40)
        + 0.9 * np.log(ingreso / 15000)
        + 0.004 * antiguedad
        - 0.05 * mora
        + 0.12 * previos
        - 2.2 * deuda_ingreso
    )
    pago = rng.random(n) < 1 / (1 + np.exp(-z))
    return pd.DataFrame(
        {
            "edad": edad,
            "ingreso_mensual": ingreso,
            "antiguedad_meses": antiguedad,
            "mora_promedio_dias": mora,
            "creditos_previos": previos,
            "deuda_ingreso": deuda_ingreso,
            "deuda_mensual": np.round(ingreso * deuda_ingreso, -1),
            "pago": pago.astype(int),
        }
    )


@lru_cache(maxsize=1)
def cargar_modelo():
    """Devuelve (modelo, métricas en test, cortes de decil calculados sobre train)."""
    datos = generar_datos()
    corte = int(len(datos) * 0.75)
    train, test = datos.iloc[:corte], datos.iloc[corte:]
    modelo = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    modelo.fit(train[VARIABLES], train["pago"])

    prob_test = modelo.predict_proba(test[VARIABLES])[:, 1]
    fpr, tpr, _ = roc_curve(test["pago"], prob_test)
    metricas = {"auc": float(roc_auc_score(test["pago"], prob_test)), "ks": float(np.max(tpr - fpr))}

    prob_train = modelo.predict_proba(train[VARIABLES])[:, 1]
    cortes_decil = np.quantile(prob_train, np.linspace(0.1, 0.9, 9))
    return modelo, metricas, cortes_decil


def predecir(valores: dict) -> tuple[float, int]:
    """Probabilidad de pago y decil (1 = mayor riesgo, 10 = menor riesgo)."""
    modelo, _, cortes = cargar_modelo()
    X = pd.DataFrame([valores])[VARIABLES]
    prob = float(modelo.predict_proba(X)[0, 1])
    decil = int(np.searchsorted(cortes, prob, side="right")) + 1
    return prob, decil


if __name__ == "__main__":
    _, metricas, _ = cargar_modelo()
    print({k: round(v, 3) for k, v in metricas.items()})
