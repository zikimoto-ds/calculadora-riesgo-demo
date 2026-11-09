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


def test_monto_se_limita_por_multiplo_del_ingreso():
    r = calculadora.calcular("consumo", {**BASE, "ingreso_mensual": 6000, "deuda_mensual": 500, "monto_solicitado": 50000})
    assert r["decision"] != "rechazo"
    assert r["monto_aprobado"] == 6000 * 4.0


def test_producto_desconocido():
    with pytest.raises(calculadora.ProductoDesconocido):
        calculadora.calcular("inexistente", BASE)


def test_mismo_dato_mismo_score():
    assert calculadora.calcular("consumo", BASE)["score"] == calculadora.calcular("consumo", BASE)["score"]


def test_decil_va_de_1_a_10_y_sube_con_el_score():
    peor = modelo.predecir({**{k: BASE[k] for k in ("edad", "ingreso_mensual", "antiguedad_meses", "mora_promedio_dias", "creditos_previos")}, "deuda_ingreso": 0.9})
    mejor = modelo.predecir({**{k: BASE[k] for k in ("edad", "ingreso_mensual", "antiguedad_meses", "mora_promedio_dias", "creditos_previos")}, "deuda_ingreso": 0.02})
    assert 1 <= peor[1] <= mejor[1] <= 10
    assert peor[0] < mejor[0]


def test_modelo_sintetico_discrimina():
    _, metricas, _ = modelo.cargar_modelo()
    assert metricas["auc"] >= 0.70
    assert metricas["ks"] >= 0.30
