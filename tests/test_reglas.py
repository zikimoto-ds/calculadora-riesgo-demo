import pytest

from app import calculadora, modelo

BASE = {
    "edad": 35,
    "ingreso_mensual": 25000,
    "antiguedad_meses": 60,
    "mora_promedio_dias": 2,
    "creditos_previos": 3,
    "deuda_mensual": 4000,
    "monto_solicitado": 20000,
    "plazo_meses": 12,
}


def test_caso_sano_se_aprueba():
    r = calculadora.calcular("consumo", BASE)
    assert r["decision"] == "aprobado"
    assert r["motivos"] == []
    assert r["monto_aprobado"] == 20000


@pytest.mark.parametrize(
    "cambio, motivo",
    [
        ({"edad": 19}, "EDAD_FUERA_DE_RANGO"),
        ({"mora_promedio_dias": 45}, "MORA_ALTA"),
        ({"deuda_mensual": 20000}, "ENDEUDAMIENTO_ALTO"),
        ({"monto_solicitado": 500}, "MONTO_FUERA_DE_POLITICA"),
        ({"plazo_meses": 60}, "PLAZO_FUERA_DE_POLITICA"),
    ],
)
def test_reglas_duras_rechazan(cambio, motivo):
    r = calculadora.calcular("consumo", {**BASE, **cambio})
    assert r["decision"] == "rechazo"
    assert motivo in r["motivos"]
    assert r["monto_aprobado"] == 0


def test_monto_se_limita_por_multiplo_del_ingreso_y_factor_de_decil():
    r = calculadora.calcular("consumo", {**BASE, "ingreso_mensual": 6000, "deuda_mensual": 500, "monto_solicitado": 50000})
    assert r["decision"] != "rechazo"
    factor = calculadora.cargar_reglas()["productos"]["consumo"]["factor_oferta_decil"][r["decil"] - 1]
    assert r["monto_aprobado"] == round(6000 * 4.0 * factor, 2)


def test_producto_desconocido():
    with pytest.raises(calculadora.ProductoDesconocido):
        calculadora.calcular("inexistente", BASE)


def test_mismo_dato_mismo_score():
    assert calculadora.calcular("consumo", BASE)["score"] == calculadora.calcular("consumo", BASE)["score"]


def test_decil_va_de_1_a_10_y_sube_con_el_score():
    vars_modelo = {k: BASE[k] for k in ("edad", "ingreso_mensual", "antiguedad_meses", "mora_promedio_dias", "creditos_previos")}
    peor = modelo.predecir({**vars_modelo, "deuda_ingreso": 0.9})
    mejor = modelo.predecir({**vars_modelo, "deuda_ingreso": 0.02})
    assert 1 <= peor["decil"] <= mejor["decil"] <= 10
    assert peor["score"] < mejor["score"]


def test_ensamble_promedia_regresion_y_xgboost():
    r = calculadora.calcular("consumo", BASE)
    assert set(r["modelos"]) == {"regresion_logistica", "xgboost"}
    probs = [m["prob"] for m in r["modelos"].values()]
    assert r["score"] == pytest.approx(sum(probs) / len(probs), abs=1e-3)
    assert 1 <= r["decil_externo"] <= 10


def test_sin_score_externo_el_caso_va_a_revision(monkeypatch):
    monkeypatch.setenv("SCORE_EXTERNO", "off")
    r = calculadora.calcular("consumo", BASE)
    assert r["decision"] == "revision"
    assert r["motivos"] == ["SIN_SCORE_EXTERNO"]
    assert r["decil_externo"] is None


def test_modelos_sinteticos_discriminan():
    _, metricas, _ = modelo.cargar_modelos()
    assert set(metricas) == {"regresion_logistica", "xgboost", "ensamble"}
    for m in metricas.values():
        assert m["auc"] >= 0.70
        assert m["ks"] >= 0.28
