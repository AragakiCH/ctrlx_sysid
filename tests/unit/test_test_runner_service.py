import time

import pytest

from application.services.test_config_service import TestConfigService
from application.services.test_runner_service import (
    PHASE_BASELINE,
    PHASE_STEP,
    STATUS_FINISHED,
    STATUS_IDLE,
    STATUS_RUNNING,
    STATUS_STOPPED,
    TestRunnerService,
)


def make_config(duration_s=1.0, delay_s=0.3, period=0.01, step_from=8.0, step_to=12.0):
    """Ensayo rápido: mismo perfil que el real, pero en ~1 segundo."""
    config = TestConfigService(sample_period_s=period)
    config.set_step_config(
        step_from=step_from,
        step_to=step_to,
        duration_s=duration_s,
        delay_s=delay_s,
        sample_period_s=period,
    )
    return config


@pytest.fixture
def runner():
    eventos = []
    service = TestRunnerService(make_config(), on_event=eventos.append)
    service.eventos = eventos  # cómodo para los asserts
    try:
        yield service
    finally:
        service.stop()


def run_to_completion(service, timeout=5.0):
    deadline = time.monotonic() + timeout
    while service.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    # margen para que se emita test_finished
    time.sleep(0.05)


# --------------------------------------------------------------------------- #
# Perfil
# --------------------------------------------------------------------------- #


def test_el_plan_tiene_una_entrada_por_muestra():
    service = TestRunnerService(make_config(duration_s=2.0, delay_s=0.5, period=0.01))
    plan = service.build_plan()

    assert plan["samples"] == 200
    assert len(plan["time"]) == len(plan["actuator"]) == len(plan["actuator_pct"]) == 200


def test_el_plan_salta_en_el_instante_configurado():
    service = TestRunnerService(make_config(duration_s=2.0, delay_s=0.5, period=0.01))
    plan = service.build_plan()

    idx = int(plan["step_at_s"] / plan["sample_period_s"])

    assert plan["actuator"][idx - 1] == plan["from_value"]
    assert plan["actuator"][idx] == plan["to_value"]


def test_el_plan_convierte_a_porcentaje_de_span():
    """8 mA y 12 mA sobre 4-20 mA son 25 % y 50 %."""
    service = TestRunnerService(make_config(step_from=8.0, step_to=12.0))
    plan = service.build_plan()

    assert plan["from_value_pct"] == pytest.approx(25.0)
    assert plan["to_value_pct"] == pytest.approx(50.0)
    assert plan["unit"] == "mA"


def test_el_plan_respeta_un_escalon_descendente():
    service = TestRunnerService(make_config(step_from=16.0, step_to=6.0))
    plan = service.build_plan()

    assert plan["from_value"] > plan["to_value"]
    assert plan["actuator"][-1] == 6.0


# --------------------------------------------------------------------------- #
# Ciclo de vida
# --------------------------------------------------------------------------- #


def test_estado_inicial_es_idle(runner):
    state = runner.get_state()

    assert state["status"] == STATUS_IDLE
    assert state["running"] is False
    assert state["actuator_cmd"] is None


def test_start_devuelve_el_plan_y_arranca(runner):
    result = runner.start()

    assert result["status"] == STATUS_RUNNING
    assert result["plan"]["samples"] == 100
    assert runner.is_running()


def test_no_se_puede_arrancar_dos_veces(runner):
    runner.start()

    with pytest.raises(ValueError, match="en curso"):
        runner.start()


def test_recorre_todas_las_muestras_y_termina(runner):
    runner.start()
    run_to_completion(runner)

    state = runner.get_state()

    assert state["status"] == STATUS_FINISHED
    assert state["index"] == state["total"] - 1
    assert state["progress"] == 1.0


def test_emite_started_ticks_y_finished(runner):
    runner.start()
    run_to_completion(runner)

    tipos = [e["type"] for e in runner.eventos]

    assert tipos[0] == "test_started"
    assert tipos[-1] == "test_finished"
    assert tipos.count("test_tick") == 100


def test_el_evento_inicial_incluye_el_plan(runner):
    runner.start()

    assert "plan" in runner.eventos[0]["data"]


def test_la_fase_cambia_de_baseline_a_step(runner):
    runner.start()
    run_to_completion(runner)

    ticks = [e["data"] for e in runner.eventos if e["type"] == "test_tick"]
    fases = [t["phase"] for t in ticks]

    assert fases[0] == PHASE_BASELINE
    assert fases[-1] == PHASE_STEP
    # una sola transición: nunca vuelve atrás
    assert fases.count(PHASE_BASELINE) + fases.count(PHASE_STEP) == len(fases)
    assert fases.index(PHASE_STEP) == 30  # delay 0.3 s / periodo 0.01 s


def test_el_comando_solo_toma_dos_valores(runner):
    runner.start()
    run_to_completion(runner)

    ticks = [e["data"] for e in runner.eventos if e["type"] == "test_tick"]

    assert sorted({t["actuator_cmd"] for t in ticks}) == [8.0, 12.0]


def test_stop_corta_el_ensayo(runner):
    runner.start()
    time.sleep(0.1)
    state = runner.stop()

    assert state["status"] == STATUS_STOPPED
    assert not runner.is_running()
    assert state["index"] < state["total"] - 1


def test_stop_es_idempotente(runner):
    assert runner.stop()["status"] == STATUS_IDLE
    assert runner.stop()["status"] == STATUS_IDLE


def test_stop_emite_evento_solo_si_habia_ensayo(runner):
    runner.stop()
    assert runner.eventos == []


# --------------------------------------------------------------------------- #
# Comando vigente (lo que consume on_sample)
# --------------------------------------------------------------------------- #


def test_sin_ensayo_no_hay_comando(runner):
    assert runner.current_command() is None


def test_durante_el_ensayo_hay_comando(runner):
    runner.start()
    time.sleep(0.05)

    command = runner.current_command()

    assert command is not None
    assert set(command) == {
        "actuator_cmd",
        "actuator_cmd_pct",
        "test_phase",
        "test_elapsed_s",
    }
    assert command["actuator_cmd"] in (8.0, 12.0)


def test_al_terminar_deja_de_haber_comando(runner):
    """Fuera de ensayo las muestras no deben etiquetarse con un comando viejo."""
    runner.start()
    run_to_completion(runner)

    assert runner.current_command() is None


# --------------------------------------------------------------------------- #
# Escritura al PLC (fase 2)
# --------------------------------------------------------------------------- #


def test_sin_writer_no_se_escribe_nada(runner):
    runner.start()
    run_to_completion(runner)

    assert runner.writes_enabled is False
    assert all(
        e["data"]["written"] is False
        for e in runner.eventos
        if e["type"] == "test_tick"
    )


def test_con_writer_se_llama_una_vez_por_muestra(runner):
    escrituras = []
    runner.set_writer(escrituras.append)

    runner.start()
    run_to_completion(runner)

    # 100 ticks + la escritura de retorno a step_from al terminar.
    assert len(escrituras) == 101
    assert sorted(set(escrituras)) == [8.0, 12.0]


def test_al_terminar_se_suelta_el_actuador(runner):
    """
    La última escritura debe ser el valor inicial, y después de esa el ensayo
    deja de gobernar el actuador.

    La variable del actuador suele ser una que también escribe la HMI. Si el
    escritor quedara armado al terminar, habría dos dueños del mismo dato y el
    operador no recuperaría el control.
    """
    escrituras = []
    runner.set_writer(escrituras.append)

    runner.start()
    run_to_completion(runner)

    assert escrituras[-1] == 8.0          # step_from
    assert runner.writes_enabled is False  # ya no se comanda nada


def test_el_stop_tambien_suelta_el_actuador(runner):
    escrituras = []
    runner.set_writer(escrituras.append)

    runner.start()
    runner.stop()

    assert escrituras[-1] == 8.0
    assert runner.writes_enabled is False


def test_soltar_el_actuador_se_anuncia(runner):
    runner.set_writer(lambda v: None)
    runner.start()
    run_to_completion(runner)

    liberado = [e for e in runner.eventos if e["type"] == "writer_released"]

    assert len(liberado) == 1
    assert liberado[0]["data"]["enabled"] is False


def test_sin_writer_no_se_anuncia_liberacion(runner):
    """No se armó nada, así que no hay nada que soltar ni que anunciar."""
    runner.start()
    run_to_completion(runner)

    assert not [e for e in runner.eventos if e["type"] == "writer_released"]


def test_un_fallo_suelto_no_aborta_el_ensayo(runner):
    """
    Un hipo de red no puede cortar un ensayo de dos minutos.

    El aborto solo llega tras varios fallos SEGUIDOS: ver
    tests/unit/test_test_runner_writes.py, donde se cubre el caso contrario.
    """
    n = [0]

    def writer_intermitente(value):
        n[0] += 1
        if n[0] % 4 == 0:
            raise RuntimeError("hipo de red")

    runner.set_writer(writer_intermitente)
    runner.start()
    run_to_completion(runner)

    state = runner.get_state()

    assert state["status"] == STATUS_FINISHED
    assert state["write_errors"] > 0
    assert "hipo de red" in state["last_write_error"]


# --------------------------------------------------------------------------- #
# Reloj
# --------------------------------------------------------------------------- #


def test_el_ensayo_dura_lo_configurado(runner):
    inicio = time.monotonic()
    runner.start()
    run_to_completion(runner)
    transcurrido = time.monotonic() - inicio

    # 1.0 s configurado; se tolera el arranque del hilo y el sondeo del test
    assert 0.9 < transcurrido < 1.6


def test_no_acumula_deriva():
    """
    El periodo se respeta contra un deadline acumulado. Si se durmiera el
    periodo fijo en cada vuelta, el coste de cada tick se iría sumando.
    """
    eventos = []
    service = TestRunnerService(
        make_config(duration_s=1.0, delay_s=0.3, period=0.01),
        on_event=eventos.append,
    )

    try:
        service.start()
        run_to_completion(service)
    finally:
        service.stop()

    ticks = [e["data"] for e in eventos if e["type"] == "test_tick"]

    # El último tick debería caer cerca de 0.99 s, no bastante después.
    assert ticks[-1]["elapsed_s"] == pytest.approx(0.99, abs=0.15)
