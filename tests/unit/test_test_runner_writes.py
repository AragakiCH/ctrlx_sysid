"""Escritura al PLC durante el ensayo: retorno seguro y aborto por fallos."""

import time

import pytest

from application.services.test_config_service import TestConfigService
from application.services.test_runner_service import (
    STATUS_ABORTED,
    STATUS_FINISHED,
    STATUS_STOPPED,
    TestRunnerService,
)

STEP_FROM = 8.0
STEP_TO = 12.0


def make_config(period=0.01, duration_s=1.0, delay_s=0.3):
    config = TestConfigService(sample_period_s=period)
    config.set_step_config(
        step_from=STEP_FROM,
        step_to=STEP_TO,
        duration_s=duration_s,
        delay_s=delay_s,
        sample_period_s=period,
    )
    return config


@pytest.fixture
def runner():
    eventos = []
    service = TestRunnerService(make_config(), on_event=eventos.append)
    service.eventos = eventos
    try:
        yield service
    finally:
        service.stop()


def run_to_completion(service, timeout=5.0):
    deadline = time.monotonic() + timeout
    while service.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)


# --------------------------------------------------------------------------- #
# Armado
# --------------------------------------------------------------------------- #


def test_sin_armar_no_se_escribe(runner):
    runner.start()
    run_to_completion(runner)

    assert runner.writes_enabled is False
    ticks = [e["data"] for e in runner.eventos if e["type"] == "test_tick"]
    assert all(t["written"] is False for t in ticks)


def test_desarmar_deja_de_escribir(runner):
    escrituras = []
    runner.set_writer(escrituras.append)
    assert runner.writes_enabled is True

    runner.set_writer(None)
    assert runner.writes_enabled is False

    runner.start()
    run_to_completion(runner)

    assert escrituras == []


# --------------------------------------------------------------------------- #
# Retorno a step_from
# --------------------------------------------------------------------------- #


def test_al_terminar_vuelve_al_valor_inicial(runner):
    """
    El proceso tiene que quedar donde estaba antes del ensayo: si no, el
    siguiente arrancaría desde otro punto de operación y el escalón medido no
    sería el configurado.
    """
    escrituras = []
    runner.set_writer(escrituras.append)

    runner.start()
    run_to_completion(runner)

    assert runner.get_state()["status"] == STATUS_FINISHED
    assert escrituras[-1] == STEP_FROM
    # 100 ticks + la escritura de retorno
    assert len(escrituras) == 101


def test_al_detener_tambien_vuelve_al_valor_inicial(runner):
    escrituras = []
    runner.set_writer(escrituras.append)

    runner.start()
    time.sleep(0.5)  # ya pasó el escalón: está comandando STEP_TO
    runner.stop()
    time.sleep(0.05)

    assert runner.get_state()["status"] == STATUS_STOPPED
    assert STEP_TO in escrituras          # llegó a comandar el escalón
    assert escrituras[-1] == STEP_FROM    # pero terminó devolviéndolo


def test_sin_writer_el_retorno_no_revienta(runner):
    runner.start()
    time.sleep(0.1)
    runner.stop()

    assert runner.get_state()["status"] == STATUS_STOPPED


def test_un_fallo_al_devolver_no_lanza(runner):
    """Si falla la última escritura ya no queda nada que intentar: se registra."""
    llamadas = []

    def writer(value):
        llamadas.append(value)
        if len(llamadas) > 100:  # falla solo en el retorno
            raise RuntimeError("sin conexión")

    runner.set_writer(writer)
    runner.start()
    run_to_completion(runner)

    state = runner.get_state()
    assert state["status"] == STATUS_FINISHED
    assert "No se pudo devolver el actuador" in state["last_write_error"]


# --------------------------------------------------------------------------- #
# Aborto por pérdida de control
# --------------------------------------------------------------------------- #


def test_aborta_tras_cinco_fallos_seguidos(runner):
    def roto(value):
        raise RuntimeError("PLC no responde")

    runner.set_writer(roto)
    runner.start()
    run_to_completion(runner)

    state = runner.get_state()

    assert state["status"] == STATUS_ABORTED
    assert state["consecutive_write_errors"] == runner.MAX_CONSECUTIVE_WRITE_ERRORS
    assert "PLC no responde" in state["abort_reason"]


def test_el_aborto_corta_de_inmediato(runner):
    """No se siguen capturando datos de un ensayo que ya no gobierna la planta."""

    def roto(value):
        raise RuntimeError("PLC no responde")

    runner.set_writer(roto)
    runner.start()
    run_to_completion(runner)

    ticks = [e for e in runner.eventos if e["type"] == "test_tick"]

    assert len(ticks) == runner.MAX_CONSECUTIVE_WRITE_ERRORS
    assert runner.eventos[-1]["type"] == "test_aborted"


def test_un_fallo_intermitente_no_aborta(runner):
    """Un hipo de red no debe cortar un ensayo de dos minutos."""
    n = [0]

    def flaky(value):
        n[0] += 1
        if n[0] % 3 == 0:
            raise RuntimeError("hipo")

    runner.set_writer(flaky)
    runner.start()
    run_to_completion(runner)

    state = runner.get_state()

    assert state["status"] == STATUS_FINISHED
    assert state["write_errors"] > 0          # hubo fallos
    assert state["consecutive_write_errors"] == 0  # pero nunca 5 seguidos


def test_una_escritura_buena_reinicia_la_racha(runner):
    """4 fallos y un acierto no deben acumularse hacia el aborto."""
    n = [0]

    def casi(value):
        n[0] += 1
        if n[0] % 5 != 0:
            raise RuntimeError("falla")

    runner.set_writer(casi)
    runner.start()
    run_to_completion(runner)

    assert runner.get_state()["status"] == STATUS_FINISHED


def test_el_estado_arranca_limpio_en_cada_ensayo(runner):
    def roto(value):
        raise RuntimeError("PLC no responde")

    runner.set_writer(roto)
    runner.start()
    run_to_completion(runner)
    assert runner.get_state()["status"] == STATUS_ABORTED

    # Un ensayo nuevo no puede heredar el aborto del anterior.
    runner.set_writer(lambda v: None)
    runner.start()

    state = runner.get_state()
    assert state["abort_reason"] is None
    assert state["write_errors"] == 0
    assert state["consecutive_write_errors"] == 0
