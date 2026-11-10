from app import calculadora

from .conftest import CLAVE_ANA, CLAVE_SUP, SOLICITUD_OK, encabezados


def _caso_en_revision() -> dict:
    """Busca una solicitud que caiga en la zona de revisión con las reglas actuales."""
    for ingreso in range(8000, 40001, 4000):
        for mora in (0, 6, 12, 18, 24):
            for ratio in (0.05, 0.15, 0.25, 0.35, 0.45):
                for previos in (0, 2, 4):
                    caso = {
                        **SOLICITUD_OK,
                        "ingreso_mensual": ingreso,
                        "mora_promedio_dias": mora,
                        "deuda_mensual": round(ingreso * ratio),
                        "creditos_previos": previos,
                        "antiguedad_meses": 24,
                        "monto_solicitado": 10000,
                    }
                    if calculadora.calcular("consumo", caso)["decision"] == "revision":
                        return caso
    raise AssertionError("No se encontró un caso de revisión: ajustar los cortes de config/reglas.json")


def test_health(cliente):
    assert cliente.get("/health").json() == {"estado": "ok"}


def test_login_correcto_e_incorrecto(cliente):
    assert cliente.post("/api/auth/login", json={"usuario": "ana", "clave": "mala"}).status_code == 401
    assert cliente.post("/api/auth/login", json={"usuario": "nadie", "clave": "x"}).status_code == 401
    r = cliente.post("/api/auth/login", json={"usuario": "ana", "clave": CLAVE_ANA})
    assert r.status_code == 200 and r.json()["rol"] == "analista"


def test_bloqueo_tras_intentos_fallidos(cliente):
    for _ in range(5):
        cliente.post("/api/auth/login", json={"usuario": "ana", "clave": "mala"})
    r = cliente.post("/api/auth/login", json={"usuario": "ana", "clave": CLAVE_ANA})
    assert r.status_code == 401


def test_rutas_protegidas_sin_token(cliente):
    assert cliente.get("/api/auth/me").status_code == 401
    assert cliente.post("/api/calcular/consumo", json=SOLICITUD_OK).status_code == 401
    assert cliente.get("/api/auth/me", headers={"Authorization": "Bearer basura"}).status_code == 401


def test_calcular_guarda_en_bitacora_y_aparece_en_dashboard(cliente):
    h = encabezados(cliente, "ana", CLAVE_ANA)
    r = cliente.post("/api/calcular/consumo", json=SOLICITUD_OK, headers=h)
    assert r.status_code == 200 and r.json()["decision"] == "aprobado"
    resumen = cliente.get("/api/dashboard/resumen", headers=h).json()
    assert resumen["total"] == 1
    assert resumen["por_producto"]["consumo"]["decisiones"]["aprobado"]["n"] == 1
    assert set(resumen["modelos"]) == {"regresion_logistica", "xgboost", "ensamble"}


def test_validacion_de_entrada(cliente):
    h = encabezados(cliente, "ana", CLAVE_ANA)
    assert cliente.post("/api/calcular/consumo", json={**SOLICITUD_OK, "edad": 12}, headers=h).status_code == 422
    assert cliente.post("/api/calcular/consumo", json={**SOLICITUD_OK, "ingreso_mensual": 0}, headers=h).status_code == 422
    assert cliente.post("/api/calcular/inexistente", json=SOLICITUD_OK, headers=h).status_code == 404


def test_lookup_cliente(cliente):
    h = encabezados(cliente, "ana", CLAVE_ANA)
    assert cliente.get("/api/lookup/cliente/CLI-000001", headers=h).json()["ingreso_mensual"] == 18000
    assert cliente.get("/api/lookup/cliente/NO-EXISTE", headers=h).status_code == 404


def test_flujo_de_revision_manual(cliente):
    ana = encabezados(cliente, "ana", CLAVE_ANA)
    sup = encabezados(cliente, "sup", CLAVE_SUP)
    r = cliente.post("/api/calcular/consumo", json=_caso_en_revision(), headers=ana)
    assert r.json()["decision"] == "revision"

    pendientes = cliente.get("/api/revisiones", headers=ana).json()
    assert len(pendientes) == 1
    rid = pendientes[0]["id"]

    assert cliente.post(f"/api/revisiones/{rid}/resolver", json={"decision": "aprobada"}, headers=ana).status_code == 403
    ok = cliente.post(f"/api/revisiones/{rid}/resolver", json={"decision": "aprobada", "comentario": "ok"}, headers=sup)
    assert ok.status_code == 200 and ok.json()["estado"] == "aprobada"
    assert cliente.post(f"/api/revisiones/{rid}/resolver", json={"decision": "rechazada"}, headers=sup).status_code == 409
    assert cliente.get("/api/revisiones", headers=ana).json() == []


def test_rechaza_nul_e_ingresos_degenerados(cliente):
    h = encabezados(cliente, "ana", CLAVE_ANA)
    assert cliente.post("/api/auth/login", json={"usuario": "a\u0000b", "clave": "x"}).status_code == 422
    assert cliente.post("/api/calcular/consumo", json={**SOLICITUD_OK, "ingreso_mensual": 1e-320}, headers=h).status_code == 422
    rev = {"decision": "aprobada", "comentario": "con\u0000nul"}
    assert cliente.post("/api/revisiones/1/resolver", json=rev, headers=encabezados(cliente, "sup", CLAVE_SUP)).status_code == 422


def test_datos_del_caso_solo_para_supervisor_o_quien_capturo(cliente):
    ana = encabezados(cliente, "ana", CLAVE_ANA)
    sup = encabezados(cliente, "sup", CLAVE_SUP)
    caso = {**_caso_en_revision(), "cliente_id": "CLI-000001"}
    assert cliente.post("/api/calcular/consumo", json=caso, headers=ana).json()["decision"] == "revision"

    otro = {"usuario": "otra.analista", "clave": "clave-larga-de-prueba", "rol": "analista"}
    assert cliente.post("/api/usuarios", json=otro, headers=sup).status_code == 201
    tercera = encabezados(cliente, "otra.analista", "clave-larga-de-prueba")

    for h, ve in ((ana, True), (sup, True), (tercera, False)):
        fila = cliente.get("/api/revisiones", headers=h).json()[0]
        assert fila["capturado_por"] == "ana"
        assert (fila["entrada"] is not None) is ve and (fila["cliente_id"] is not None) is ve


def test_supervisor_no_resuelve_su_propio_calculo(cliente):
    sup = encabezados(cliente, "sup", CLAVE_SUP)
    r = cliente.post("/api/calcular/consumo", json=_caso_en_revision(), headers=sup)
    assert r.json()["decision"] == "revision"
    rid = cliente.get("/api/revisiones", headers=sup).json()[0]["id"]
    assert cliente.post(f"/api/revisiones/{rid}/resolver", json={"decision": "aprobada"}, headers=sup).status_code == 403


def test_token_sin_claims_obligatorios_es_rechazado(cliente):
    import os

    import jwt as pyjwt

    sin_exp = pyjwt.encode({"sub": "ana"}, os.environ["JWT_SECRET"], algorithm="HS256")
    sin_sub = pyjwt.encode({"rol": "supervisor"}, os.environ["JWT_SECRET"], algorithm="HS256")
    for t in (sin_exp, sin_sub):
        assert cliente.get("/api/auth/me", headers={"Authorization": "Bearer " + t}).status_code == 401


def test_modulo_usuarios_listado_y_activacion(cliente):
    ana = encabezados(cliente, "ana", CLAVE_ANA)
    sup = encabezados(cliente, "sup", CLAVE_SUP)

    assert cliente.get("/api/usuarios", headers=ana).status_code == 403
    lista = cliente.get("/api/usuarios", headers=sup).json()
    assert {u["usuario"] for u in lista} == {"ana", "sup"}
    assert all("clave_hash" not in u and u["activo"] for u in lista)

    assert cliente.patch("/api/usuarios/sup/activo", json={"activo": False}, headers=sup).status_code == 403
    assert cliente.patch("/api/usuarios/nadie/activo", json={"activo": False}, headers=sup).status_code == 404
    assert cliente.patch("/api/usuarios/ana/activo", json={"activo": False}, headers=sup).status_code == 200
    assert cliente.get("/api/auth/me", headers=ana).status_code == 401  # el token vigente muere al desactivar
    assert cliente.post("/api/auth/login", json={"usuario": "ana", "clave": CLAVE_ANA}).status_code == 401
    assert cliente.patch("/api/usuarios/ana/activo", json={"activo": True}, headers=sup).status_code == 200
    assert cliente.post("/api/auth/login", json={"usuario": "ana", "clave": CLAVE_ANA}).status_code == 200


def test_cambio_de_clave_propia(cliente):
    ana = encabezados(cliente, "ana", CLAVE_ANA)
    nueva = "una-clave-nueva-larga-1"
    mal = {"clave_actual": "equivocada", "clave_nueva": nueva}
    assert cliente.post("/api/usuarios/me/clave", json=mal, headers=ana).status_code == 403
    assert cliente.post("/api/usuarios/me/clave", json={"clave_actual": CLAVE_ANA, "clave_nueva": "corta"}, headers=ana).status_code == 422
    ok = {"clave_actual": CLAVE_ANA, "clave_nueva": nueva}
    assert cliente.post("/api/usuarios/me/clave", json=ok, headers=ana).status_code == 200
    assert cliente.post("/api/auth/login", json={"usuario": "ana", "clave": CLAVE_ANA}).status_code == 401
    assert cliente.post("/api/auth/login", json={"usuario": "ana", "clave": nueva}).status_code == 200


def test_crear_usuario_solo_supervisor_y_clave_larga(cliente):
    ana = encabezados(cliente, "ana", CLAVE_ANA)
    sup = encabezados(cliente, "sup", CLAVE_SUP)
    nuevo = {"usuario": "luis", "clave": "una-clave-bien-larga-1", "rol": "analista"}
    assert cliente.post("/api/usuarios", json=nuevo, headers=ana).status_code == 403
    assert cliente.post("/api/usuarios", json={**nuevo, "clave": "corta"}, headers=sup).status_code == 422
    assert cliente.post("/api/usuarios", json=nuevo, headers=sup).status_code == 201
    assert cliente.post("/api/usuarios", json=nuevo, headers=sup).status_code == 409
    assert encabezados(cliente, "luis", "una-clave-bien-larga-1")
