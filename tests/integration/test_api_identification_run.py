"""Contrato de POST /api/identification/run — re-identificar a demanda."""

import math

import pytest
from fastapi.testclient import TestClient

import main


def fill_buffer(step_from=20.0, step_to=60.0, n=400, dt=0.5, delay=10.0):
    """Llena el buffer con un FOPDT conocido: K=1.5, tau=8, L=2."""
    main.reset_runtime_state()

    k, tau, dead_time, y0 = 1.5, 8.0, 2.0, 10.0
    du = step_to - step_from

    for i in range(n):
        t = i * dt
        u = step_from if t < delay else step_to
        te = t - delay - dead_time
        y = y0 if te <= 0 else y0 + k * du * (1.0 - math.exp(-te / tau))

        main.realtime_service.add_sample(
            {
                "time": t,
                "actuator": u,
                "sensor": y,
                "setpoint": step_to,
                "signal_type": 0,
                "raw": {},
            }
        )


@pytest.fixture
def client():
    service = main.app.state.test_config_service
    service.set_scales({"actuator": "pct", "sensor": "pct", "setpoint": "pct"})
    service.set_step_config(
        step_from=20.0,
        step_to=60.0,
        duration_s=100,
        delay_s=10,
        order="auto",
        sample_period_s=0.5,
    )
    main.reset_runtime_state()
    return TestClient(main.app)


def test_buffer_vacio_devuelve_400(client):
    response = client.post("/api/identification/run")

    assert response.status_code == 400
    assert "muestras en el buffer" in response.json()["detail"]


def test_identifica_con_el_buffer_actual(client):
    """Forzando FOPDT se deben recuperar los parámetros exactos del proceso."""
    fill_buffer()
    client.post("/api/test/config", json={"order": "1"})
    body = client.post("/api/identification/run").json()

    modelo = body["models"][0]
    assert modelo["model_type"] == "fopdt"
    assert modelo["gain"] == pytest.approx(1.5, abs=0.05)
    assert modelo["tau"] == pytest.approx(8.0, abs=0.3)
    assert modelo["dead_time"] == pytest.approx(2.0, abs=0.3)
    assert modelo["fit_quality"] > 0.99


def test_auto_compara_los_tres_modelos(client):
    fill_buffer()
    body = client.post("/api/identification/run").json()

    assert body["order"] == "auto"
    assert {m["model_type"] for m in body["models"]} == {
        "fopdt",
        "sopdt",
        "integrating",
    }


@pytest.mark.parametrize(
    "combo, esperado",
    [("1", "fopdt"), ("2", "sopdt"), ("0", "integrating")],
)
def test_el_orden_forzado_ajusta_solo_ese_modelo(client, combo, esperado):
    fill_buffer()
    client.post("/api/test/config", json={"order": combo})
    body = client.post("/api/identification/run").json()

    assert body["order"] == esperado
    assert [m["model_type"] for m in body["models"]] == [esperado]


def test_cambiar_el_orden_cambia_el_resultado_sin_repetir_el_ensayo(client):
    fill_buffer()

    auto = client.post("/api/identification/run").json()
    client.post("/api/test/config", json={"order": "0"})
    forzado = client.post("/api/identification/run").json()

    assert auto["winner"] != "integrating"
    assert forzado["winner"] == "integrating"
    # El integrador no describe este proceso: su R² debe ser peor.
    assert forzado["models"][0]["fit_quality"] < auto["models"][0]["fit_quality"]


def test_el_resultado_queda_disponible_en_latest(client):
    fill_buffer()
    corrido = client.post("/api/identification/run").json()
    ultimo = client.get("/api/identification/latest").json()

    assert ultimo["winner"] == corrido["winner"]


def test_ventana_incompleta_identifica_igual_y_lo_marca(client):
    """
    Caso real: el programa del PLC cicla cada 20 s, así que después del último
    escalón nunca hay tantas muestras como pide una duración larga. Un proceso
    de primer orden ya está definido en 3-4 constantes de tiempo, así que se
    identifica igual, pero avisando que la ventana quedó corta.
    """
    fill_buffer(n=100)  # 50 s, escalón en t=10 s -> 80 muestras después
    client.post("/api/test/config", json={"duration_s": 100, "delay_s": 10})

    body = client.post("/api/identification/run").json()

    assert body["truncated"] is True
    assert body["requested_post_samples"] == 180
    assert body["window"]["count"] < 180 + 20
    assert body["models"][0]["fit_quality"] > 0.9


def test_allow_partial_false_exige_la_ventana_completa(client):
    fill_buffer(n=100)
    client.post("/api/test/config", json={"duration_s": 100, "delay_s": 10})

    response = client.post("/api/identification/run", params={"allow_partial": False})
    detail = response.json()["detail"]

    assert response.status_code == 400
    assert "Se detectó el escalón" in detail
    assert "180" in detail


def test_ajustar_la_duracion_al_ciclo_del_plc_no_trunca(client):
    fill_buffer(n=100)

    # Con 100 muestras totales y el escalón en la 20, quedan 80 después.
    client.post("/api/test/config", json={"duration_s": 26, "delay_s": 10})
    body = client.post("/api/identification/run").json()

    assert body["truncated"] is False
    assert body["models"][0]["fit_quality"] > 0.9


def test_muy_pocas_muestras_tras_el_escalon_devuelve_400(client):
    """Por debajo del piso duro no se identifica ni con allow_partial."""
    fill_buffer(n=45)  # escalón en la muestra 20 -> solo 25 después

    response = client.post("/api/identification/run")

    assert response.status_code == 400
    assert "al menos 30" in response.json()["detail"]


def test_sin_escalon_devuelve_400(client):
    main.reset_runtime_state()
    for i in range(200):
        main.realtime_service.add_sample(
            {
                "time": i * 0.5,
                "actuator": 20.0,  # constante: no hay escalón
                "sensor": 10.0,
                "setpoint": 20.0,
                "signal_type": 0,
                "raw": {},
            }
        )

    response = client.post("/api/identification/run")

    assert response.status_code == 400
    assert "No se detectó ningún escalón" in response.json()["detail"]
