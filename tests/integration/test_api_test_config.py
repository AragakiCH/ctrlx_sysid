"""Contrato de /api/test — escalas por rol y condiciones de ensayo."""

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client():
    # El servicio es un singleton del proceso: se restaura para que el orden
    # de los tests no importe.
    service = main.app.state.test_config_service
    service.set_scales({"actuator": "ma", "sensor": "ma", "setpoint": "ma"})
    service.set_step_config(
        step_from=8.0,
        step_to=12.0,
        duration_s=120,
        delay_s=10,
        order="auto",
        sample_period_s=0.2,
    )
    return TestClient(main.app)


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #


def test_snapshot_trae_todo_de_una_vez(client):
    body = client.get("/api/test").json()

    assert body["ok"] is True
    assert set(body["scales"]) == {"actuator", "sensor", "setpoint"}
    assert {s["key"] for s in body["available"]} == {"ma", "pct", "v"}
    assert body["orders"] == ["auto", "fopdt", "sopdt", "integrating"]
    assert "derived" in body["step"]


# --------------------------------------------------------------------------- #
# Escalas
# --------------------------------------------------------------------------- #


def test_get_scales_devuelve_las_vigentes(client):
    body = client.get("/api/test/scales").json()

    assert body["scales"]["actuator"] == "ma"
    assert body["detail"]["actuator"]["min"] == 4.0
    assert body["detail"]["actuator"]["max"] == 20.0


def test_post_scales_cambia_solo_los_roles_enviados(client):
    body = client.post("/api/test/scales", json={"scales": {"sensor": "v"}}).json()

    assert body["scales"]["sensor"] == "v"
    assert body["scales"]["actuator"] == "ma"
    assert body["detail"]["sensor"]["unit"] == "V"


def test_post_scales_rechaza_escala_desconocida(client):
    response = client.post("/api/test/scales", json={"scales": {"actuator": "bar"}})
    assert response.status_code == 422


def test_post_scales_reinterpreta_el_buffer_ya_capturado(client):
    import main

    main.reset_runtime_state()
    for i in range(20):
        main.realtime_service.add_sample(
            {
                "time": i * 0.2,
                "actuator": 12.0,
                "sensor": 12.0,
                "setpoint": 12.0,
                "signal_type": 1,
                "raw": {},
            }
        )

    # Con el actuador en mA, 12 son el 50 % del span.
    assert main.realtime_service.get_latest_sample()["actuator_pct"] == pytest.approx(50.0)

    body = client.post("/api/test/scales", json={"scales": {"actuator": "pct"}}).json()

    assert body["recomputed_samples"] == 20
    # Declarado como porcentaje, 12 son el 12 %.
    assert main.realtime_service.get_latest_sample()["actuator_pct"] == pytest.approx(12.0)


# --------------------------------------------------------------------------- #
# Configuración del ensayo
# --------------------------------------------------------------------------- #


def test_config_calcula_los_derivados(client):
    body = client.post(
        "/api/test/config",
        json={
            "step_from": 8.0,
            "step_to": 12.0,
            "duration_s": 60,
            "delay_s": 10,
            "sample_period_s": 0.2,
            "order": "auto",
        },
    ).json()

    d = body["derived"]
    assert d["step_from_pct"] == pytest.approx(25.0)
    assert d["step_to_pct"] == pytest.approx(50.0)
    assert d["delta_pct"] == pytest.approx(25.0)
    assert d["step_threshold_pct"] == pytest.approx(12.5)
    assert d["pre_samples"] == 50
    assert d["post_samples"] == 250
    assert d["direction"] == "up"


def test_config_traduce_el_orden_del_combo(client):
    assert client.post("/api/test/config", json={"order": "1"}).json()["order"] == "fopdt"
    assert client.post("/api/test/config", json={"order": "2"}).json()["order"] == "sopdt"
    assert (
        client.post("/api/test/config", json={"order": "0"}).json()["order"]
        == "integrating"
    )


def test_config_es_parcial(client):
    client.post("/api/test/config", json={"duration_s": 90})
    body = client.get("/api/test/config").json()

    assert body["duration_s"] == pytest.approx(90.0)
    assert body["step_from"] == pytest.approx(8.0)


@pytest.mark.parametrize(
    "payload, fragmento",
    [
        ({"step_from": 10.0, "step_to": 10.0}, "no hay escalón"),
        ({"duration_s": 30, "delay_s": 30}, "menor que la duración"),
        ({"duration_s": 12, "delay_s": 10}, "muestras después"),
    ],
)
def test_config_invalida_devuelve_400(client, payload, fragmento):
    response = client.post("/api/test/config", json=payload)

    assert response.status_code == 400
    assert fragmento in response.json()["detail"]


def test_valor_fuera_de_la_escala_del_actuador_devuelve_400(client):
    client.post("/api/test/scales", json={"scales": {"actuator": "v"}})
    response = client.post("/api/test/config", json={"step_from": 2.0, "step_to": 12.0})

    assert response.status_code == 400
    assert "fuera de la escala" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Conversión
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value, origen, destino, esperado",
    [
        (12.0, "ma", "pct", 50.0),
        (8.0, "ma", "pct", 25.0),
        (5.0, "v", "ma", 12.0),
        (50.0, "pct", "v", 5.0),
        (20.0, "ma", "v", 10.0),
    ],
)
def test_convert(client, value, origen, destino, esperado):
    body = client.post(
        "/api/test/convert",
        json={"value": value, "from_scale": origen, "to_scale": destino},
    ).json()

    assert body["result"] == pytest.approx(esperado)


def test_convert_rechaza_escala_desconocida(client):
    response = client.post(
        "/api/test/convert", json={"value": 1.0, "from_scale": "psi", "to_scale": "pct"}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Vista previa
# --------------------------------------------------------------------------- #


def test_preview_devuelve_series_alineadas(client):
    client.post(
        "/api/test/config",
        json={"step_from": 8.0, "step_to": 12.0, "duration_s": 100, "delay_s": 20},
    )
    body = client.get("/api/test/preview", params={"points": 11}).json()

    assert len(body["time"]) == len(body["actuator"]) == 11
    assert body["unit"] == "mA"
    assert body["actuator"][0] == pytest.approx(8.0)
    assert body["actuator"][-1] == pytest.approx(12.0)
    assert body["step_at_s"] == pytest.approx(20.0)


def test_preview_en_otra_escala(client):
    body = client.get("/api/test/preview", params={"points": 5, "scale": "pct"}).json()

    assert body["unit"] == "%"
    assert body["from_value"] == pytest.approx(25.0)
    assert body["to_value"] == pytest.approx(50.0)


def test_preview_valida_el_rango_de_points(client):
    assert client.get("/api/test/preview", params={"points": 1}).status_code == 422
    assert client.get("/api/test/preview", params={"points": 9999}).status_code == 422
