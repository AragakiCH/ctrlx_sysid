"""Ensayo de punta a punta: REST para controlar, WebSocket para escuchar."""

import time

import pytest
from fastapi.testclient import TestClient

import main

# Ensayo comprimido: mismo perfil que el real pero en ~1 s.
ENSAYO_RAPIDO = {
    "step_from": 8.0,
    "step_to": 12.0,
    "duration_s": 1.0,
    "delay_s": 0.3,
    "sample_period_s": 0.01,
}


@pytest.fixture
def client():
    """
    Se usa como context manager para que corra el `startup`.

    Sin eso `event_loop` queda en None, `broadcast_from_thread` descarta todo y
    el WebSocket no recibe ni un mensaje.
    """
    with TestClient(main.app) as c:
        c.post("/api/test/config", json=ENSAYO_RAPIDO)
        try:
            yield c
        finally:
            c.post("/api/test/stop")


def drain(ws, hasta="test_finished", limite=400):
    """Consume mensajes hasta ver `hasta`. Devuelve los recibidos."""
    recibidos = []
    for _ in range(limite):
        msg = ws.receive_json()
        recibidos.append(msg)
        if msg["type"] == hasta:
            break
    return recibidos


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


def test_plan_tiene_una_entrada_por_muestra(client):
    plan = client.get("/api/test/plan").json()

    assert plan["samples"] == 100
    assert len(plan["time"]) == plan["samples"]
    assert plan["step_at_s"] == 0.3


def test_sin_ensayo_no_esta_corriendo(client):
    # No se afirma `status == "idle"`: el servicio es global al módulo `main`,
    # así que un test anterior puede haberlo dejado en "finished" o "stopped".
    # Ambos son estados legítimos de reposo. El caso "recién creado es idle"
    # se cubre en el test unitario, con una instancia limpia.
    body = client.get("/api/test/run").json()

    assert body["running"] is False
    assert body["status"] in ("idle", "finished", "stopped")
    assert body["writes_enabled"] is False


def test_start_devuelve_estado_y_plan(client):
    body = client.post("/api/test/start").json()

    assert body["status"] == "running"
    assert body["plan"]["samples"] == 100


def test_arrancar_dos_veces_da_400(client):
    client.post("/api/test/start")
    r = client.post("/api/test/start")

    assert r.status_code == 400
    assert "en curso" in r.json()["detail"]


def test_stop_es_idempotente_y_no_falla_sin_ensayo(client):
    assert client.post("/api/test/stop").status_code == 200
    assert client.post("/api/test/stop").status_code == 200


def test_config_invalida_no_arranca_el_ensayo(client):
    """delay >= duration deja el ensayo sin respuesta que identificar."""
    r = client.post(
        "/api/test/config",
        json={**ENSAYO_RAPIDO, "delay_s": 2.0},
    )

    assert r.status_code == 400
    assert client.get("/api/test/run").json()["running"] is False


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


def test_emite_started_ticks_y_finished(client):
    with client.websocket_connect("/ws") as ws:
        client.post("/api/test/start")
        mensajes = drain(ws)

    tipos = [m["type"] for m in mensajes]

    assert tipos[0] == "test_started"
    assert tipos[-1] == "test_finished"
    assert tipos.count("test_tick") == 100


def test_el_evento_inicial_trae_el_plan_completo(client):
    with client.websocket_connect("/ws") as ws:
        client.post("/api/test/start")
        started = ws.receive_json()

    assert started["type"] == "test_started"
    assert started["data"]["plan"]["samples"] == 100
    assert len(started["data"]["plan"]["actuator"]) == 100


def test_los_ticks_llevan_el_valor_comandado(client):
    with client.websocket_connect("/ws") as ws:
        client.post("/api/test/start")
        mensajes = drain(ws)

    ticks = [m["data"] for m in mensajes if m["type"] == "test_tick"]

    assert ticks[0]["actuator_cmd"] == 8.0
    assert ticks[0]["actuator_cmd_pct"] == pytest.approx(25.0)
    assert ticks[-1]["actuator_cmd"] == 12.0
    assert ticks[-1]["actuator_cmd_pct"] == pytest.approx(50.0)


def test_el_salto_ocurre_en_el_instante_configurado(client):
    with client.websocket_connect("/ws") as ws:
        client.post("/api/test/start")
        mensajes = drain(ws)

    ticks = [m["data"] for m in mensajes if m["type"] == "test_tick"]
    salto = next(t for t in ticks if t["phase"] == "step")

    assert salto["elapsed_s"] == pytest.approx(0.3, abs=0.05)


def test_stop_emite_test_stopped(client):
    with client.websocket_connect("/ws") as ws:
        client.post("/api/test/start")
        ws.receive_json()  # test_started
        time.sleep(0.1)
        client.post("/api/test/stop")

        mensajes = drain(ws, hasta="test_stopped", limite=200)

    assert mensajes[-1]["type"] == "test_stopped"
    assert mensajes[-1]["data"]["status"] == "stopped"


def test_get_test_state_responde_sin_ensayo(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "get_test_state"})
        msg = ws.receive_json()

    assert msg["type"] == "test_state"
    assert msg["data"]["running"] is False
    # El plan solo viaja si hay ensayo en curso: fuera de él no hay línea
    # objetivo que redibujar.
    assert "plan" not in msg["data"]


def test_get_test_state_trae_el_plan_si_hay_ensayo(client):
    """Al recargar la página hay que poder redibujar la línea objetivo."""
    with client.websocket_connect("/ws") as ws:
        client.post("/api/test/start")

        ws.send_json({"type": "get_test_state"})

        for _ in range(300):
            msg = ws.receive_json()
            if msg["type"] == "test_state":
                break

    assert msg["data"]["running"] is True
    assert msg["data"]["plan"]["samples"] == 100


def test_un_cliente_nuevo_se_engancha_a_un_ensayo_en_curso(client):
    with client.websocket_connect("/ws") as ws1:
        client.post("/api/test/start")
        ws1.receive_json()

        with client.websocket_connect("/ws") as ws2:
            for _ in range(300):
                msg = ws2.receive_json()
                if msg["type"] == "test_state":
                    break

    assert msg["data"]["running"] is True
    assert "plan" in msg["data"]


# --------------------------------------------------------------------------- #
# actuator_cmd en las muestras
# --------------------------------------------------------------------------- #


def test_las_muestras_se_etiquetan_con_el_comando(client):
    """
    El comando va en campos aparte y NO pisa el actuator leído del PLC: es lo
    que permite comparar lo pedido contra lo que hizo la planta.
    """
    client.post("/api/test/start")
    time.sleep(0.05)

    muestra = {"time": 1.0, "actuator": 7.9, "sensor": 6.0, "setpoint": 12.0}
    main.on_sample(muestra)

    guardada = main.realtime_service.get_latest_sample()

    assert guardada["actuator"] == 7.9  # la lectura real, intacta
    assert guardada["actuator_cmd"] in (8.0, 12.0)  # lo que se comandó
    assert guardada["test_phase"] in ("baseline", "step")


def test_fuera_del_ensayo_las_muestras_no_llevan_comando(client):
    main.realtime_service.clear()
    main.on_sample({"time": 1.0, "actuator": 7.9, "sensor": 6.0, "setpoint": 12.0})

    guardada = main.realtime_service.get_latest_sample()

    assert "actuator_cmd" not in guardada


def test_start_limpia_el_buffer_por_defecto(client):
    main.on_sample({"time": 1.0, "actuator": 7.9, "sensor": 6.0, "setpoint": 12.0})
    assert main.realtime_service.get_buffer_size() > 0

    client.post("/api/test/start")

    # El ensayo nuevo no debe arrastrar muestras del anterior.
    assert main.realtime_service.get_buffer_size() == 0


def test_start_puede_conservar_el_buffer(client):
    main.realtime_service.clear()
    main.on_sample({"time": 1.0, "actuator": 7.9, "sensor": 6.0, "setpoint": 12.0})

    client.post("/api/test/start", params={"clear_buffer": "false"})

    assert main.realtime_service.get_buffer_size() == 1


# --------------------------------------------------------------------------- #
# Armado de la escritura al PLC
# --------------------------------------------------------------------------- #


def test_writer_arranca_desarmado(client):
    body = client.get("/api/test/writer").json()

    assert body["enabled"] is False
    assert body["role"] == "actuator"


def test_no_se_puede_armar_sin_sesion_opcua(client):
    r = client.post("/api/test/writer", json={"enabled": True})

    assert r.status_code == 400
    assert "sesión" in r.json()["detail"]
    # Y queda desarmado, no a medias.
    assert client.get("/api/test/writer").json()["enabled"] is False


def test_desarmar_siempre_funciona(client):
    r = client.post("/api/test/writer", json={"enabled": False})

    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_no_se_puede_cambiar_la_escritura_con_ensayo_en_curso(client):
    """Cambiar quién gobierna el actuador a mitad dejaría medio escalón aplicado."""
    client.post("/api/test/start")

    r = client.post("/api/test/writer", json={"enabled": False})

    assert r.status_code == 400
    assert "ensayo en curso" in r.json()["detail"]


def test_el_estado_del_ensayo_reporta_la_escritura(client):
    body = client.get("/api/test/run").json()

    assert body["writes_enabled"] is False
    assert body["consecutive_write_errors"] == 0
    assert body["abort_reason"] is None


def test_logout_desarma_la_escritura(client):
    client.post("/api/opcua/logout")

    assert client.get("/api/test/writer").json()["enabled"] is False


def test_cambiar_el_mapeo_desarma_la_escritura(client):
    """La variable nueva puede no ser escribible: rearmar es deliberado."""
    client.post("/api/opcua/mapping", json={"mapping": {"actuator": "OtraVar"}})

    assert client.get("/api/test/writer").json()["enabled"] is False
