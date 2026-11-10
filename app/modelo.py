"""Ensamble de modelos de ejemplo: regresión logística + XGBoost sobre datos inventados.

El score final es el promedio de las probabilidades de ambos modelos; cada modelo
aporta además su decil (1 = mayor riesgo, 10 = menor riesgo) calculado con cortes
de cuantiles sobre el train. No hay archivos .pkl en el repositorio: todo se entrena
al arrancar (unos segundos) con semilla fija.

Con datos reales, el corte train/test debe ser temporal y respetar la maduración del
target; aquí los datos no tienen fecha, así que el corte es simplemente por posición.
"""
from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

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


def _auc_ks(y, prob) -> dict:
    fpr, tpr, _ = roc_curve(y, prob)
    return {"auc": float(roc_auc_score(y, prob)), "ks": float(np.max(tpr - fpr))}


@lru_cache(maxsize=1)
def cargar_modelos():
    """Devuelve (modelos, métricas en test, cortes de decil sobre train) por nombre.

    Nombres: "regresion_logistica", "xgboost" y "ensamble" (promedio de probabilidades;
    en métricas y cortes solamente, no es un tercer modelo).
    """
    datos = generar_datos()
    corte = int(len(datos) * 0.75)
    train, test = datos.iloc[:corte], datos.iloc[corte:]

    rl = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    rl.fit(train[VARIABLES], train["pago"])
    xgb = XGBClassifier(
        n_estimators=150,
        max_depth=2,
        learning_rate=0.08,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=10,
        min_child_weight=20,  # los datos sintéticos vienen de un proceso logístico: sin regularización fuerte el XGB sobreajusta
        random_state=7,
        n_jobs=2,
        eval_metric="logloss",
    )
    xgb.fit(train[VARIABLES], train["pago"])
    modelos = {"regresion_logistica": rl, "xgboost": xgb}

    metricas, cortes = {}, {}
    prob_train, prob_test = {}, {}
    for nombre, m in modelos.items():
        prob_train[nombre] = m.predict_proba(train[VARIABLES])[:, 1]
        prob_test[nombre] = m.predict_proba(test[VARIABLES])[:, 1]
        metricas[nombre] = _auc_ks(test["pago"], prob_test[nombre])
        cortes[nombre] = np.quantile(prob_train[nombre], np.linspace(0.1, 0.9, 9))

    blend_train = np.mean(list(prob_train.values()), axis=0)
    blend_test = np.mean(list(prob_test.values()), axis=0)
    metricas["ensamble"] = _auc_ks(test["pago"], blend_test)
    cortes["ensamble"] = np.quantile(blend_train, np.linspace(0.1, 0.9, 9))
    return modelos, metricas, cortes


def _decil(prob: float, cortes: np.ndarray) -> int:
    return int(np.searchsorted(cortes, prob, side="right")) + 1


def predecir(valores: dict) -> dict:
    """Score del ensamble (promedio de probabilidades) con el detalle por modelo."""
    modelos, _, cortes = cargar_modelos()
    X = pd.DataFrame([valores])[VARIABLES]
    detalle = {}
    for nombre, m in modelos.items():
        prob = float(m.predict_proba(X)[0, 1])
        detalle[nombre] = {"prob": round(prob, 4), "decil": _decil(prob, cortes[nombre])}
    blend = float(np.mean([d["prob"] for d in detalle.values()]))
    return {"score": blend, "decil": _decil(blend, cortes["ensamble"]), "modelos": detalle}


if __name__ == "__main__":
    _, metricas, _ = cargar_modelos()
    for nombre, m in metricas.items():
        print(nombre, {k: round(v, 3) for k, v in m.items()})
