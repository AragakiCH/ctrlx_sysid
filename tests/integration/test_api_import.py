"""Flujo de importación completo: parse -> load -> identificar -> clear."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures_data_sinto.trace.csv"

MAPEO = {
    "actuator": "Main_Control.PID_OUT",
    "sensor": "Main_Control.Velocidad_Scaled",
    "setpoint": "DB_HMI.HMI_SP_Local_Automatico",
}


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        try:
            yield c
        finally:
            # Cada test deja el backend en tiempo real, como estaba.
            c.post("/api/import/clear")


def do_parse(client):
    with FIXTURE.open("rb") as f:
        return client.post(
            "/api/import/parse",
            files={"file": ("data_sinto_05_08.trace.csv", f, "text/csv")},
        )


def do_load(client, token, mapping=None, scale="pct"):
    return client.post(
        "/api/import/load",
        json={"token": token, "mapping": mapping or MAPEO, "scale": scale},
    )


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #


def test_parse_devuelve_variables_y_token(client):
    r = do_parse(client)

    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "codesys-trace"
    assert body["samples"] == 1517
    assert len(body["variables"]) == 3
    assert body["token"]


def test_parse_sugiere_un_mapeo_razonable(client):
    sugerido = do_parse(client).json()["suggested_mapping"]

    assert sugerido["actuator"] == "Main_Control.PID_OUT"
    assert sugerido["sensor"] == "Main_Control.Velocidad_Scaled"
    assert sugerido["setpoint"] == "DB_HMI.HMI_SP_Local_Automatico"


def test_parse_archivo_vacio_da_400(client):
    r = client.post("/api/import/parse", files={"file": ("v.csv", b"", "text/csv")})
    assert r.status_code == 400


def test_parse_no_toca_el_buffer(client):
    do_parse(client)
    assert main.realtime_service.source == "realtime"


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #


def test_load_llena_el_buffer_y_activa_el_modo(client):
    token = do_parse(client).json()["token"]
    r = do_load(client, token)

    assert r.status_code == 200
    assert r.json()["buffer_size"] == 1517
    assert main.realtime_service.source == "imported"


def test_load_ajusta_el_periodo_de_muestreo_al_del_archivo(client):
    """Sin esto, las ventanas de identificación quedan dimensionadas para 0.2 s."""
    token = do_parse(client).json()["token"]
    do_load(client, token)

    config = client.get("/api/test/config").json()
    assert config["sample_period_s"] == pytest.approx(0.020)


def test_en_modo_importado_el_plc_no_entra(client):
    token = do_parse(client).json()["token"]
    do_load(client, token)

    antes = main.realtime_service.get_buffer_size()
    main.on_sample({"time": 999.0, "actuator": 4.0, "sensor": 4.0, "setpoint": 12.0})

    assert main.realtime_service.get_buffer_size() == antes


def test_load_rechaza_actuador_igual_a_sensor(client):
    token = do_parse(client).json()["token"]
    r = do_load(
        client,
        token,
        mapping={
            "actuator": "Main_Control.PID_OUT",
            "sensor": "Main_Control.PID_OUT",
        },
    )

    assert r.status_code == 400
    assert "misma variable" in r.json()["detail"]


def test_load_rechaza_variable_inexistente(client):
    token = do_parse(client).json()["token"]
    r = do_load(client, token, mapping={"actuator": "NoExiste", "sensor": "Main_Control.PID_OUT"})

    assert r.status_code == 400
    assert "no existe" in r.json()["detail"]


def test_load_con_token_vencido_da_400(client):
    r = do_load(client, "deadbeef")
    assert r.status_code == 400
    assert "token" in r.json()["detail"].lower() or "expiró" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# Identificación sobre lo importado
# --------------------------------------------------------------------------- #


def test_identifica_sobre_el_archivo_real(client):
    """El objetivo de todo el flujo: el pipeline corre igual que en vivo."""
    token = do_parse(client).json()["token"]
    do_load(client, token)

    r = client.post("/api/identification/run")

    assert r.status_code == 200
    body = r.json()
    assert body["winner"] in ("fopdt", "sopdt", "integrating")
    assert body["models"][0]["fit_quality"] > 0.95
    assert body["models"][0]["pid_tunings"]


# --------------------------------------------------------------------------- #
# Clear / status
# --------------------------------------------------------------------------- #


def test_status_refleja_el_modo(client):
    assert client.get("/api/import/status").json()["active"] is False

    token = do_parse(client).json()["token"]
    do_load(client, token)

    status = client.get("/api/import/status").json()
    assert status["active"] is True
    assert status["source_name"] == "data_sinto_05_08.trace.csv"
    assert status["mapping"]["actuator"] == "Main_Control.PID_OUT"


def test_clear_vuelve_a_tiempo_real(client):
    token = do_parse(client).json()["token"]
    do_load(client, token)

    r = client.post("/api/import/clear")
    assert r.json()["active"] is False

    main.on_sample({"time": 1.0, "actuator": 4.0, "sensor": 4.0, "setpoint": 12.0})
    assert main.realtime_service.get_buffer_size() == 1


def test_clear_es_idempotente(client):
    assert client.post("/api/import/clear").status_code == 200
    assert client.post("/api/import/clear").status_code == 200


def test_series_expone_lo_importado(client):
    token = do_parse(client).json()["token"]
    do_load(client, token)

    serie = client.get("/api/test/series").json()

    assert serie["count"] == 1517
    assert serie["time"][0] == pytest.approx(0.0)  # re-basado
    assert serie["actuator"][0] == pytest.approx(25.0)
