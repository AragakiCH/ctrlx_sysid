"""
Pre-posicionado de la planta y rearme de la escritura entre ensayos.

Dos cosas que pidió el usuario del sintonizador:

1. "Sólo debo hacer coincidir el valor inicial del actuador en el PLC antes de
   iniciar la simulación" — que lo haga la app, no el operador a mano.
2. "El botón de reinicio no funciona del todo: me permite iniciar un nuevo
   ensayo, pero no genera nuevamente la señal del actuador."

El segundo era efecto de soltar el actuador al terminar cada ensayo: correcto
para devolverle la variable a la HMI, pero dejaba los ensayos siguientes sin
escritura porque no se distinguía la INTENCIÓN del usuario del escritor activo.
"""

import time

import pytest

from application.services.test_config_service import TestConfigService
from application.services.test_runner_service import (
    PHASE_PRESET,
    TestRunnerService,
)


@pytest.fixture
def config():
    c = TestConfigService(sample_period_s=0.02)
    c.set_scales({"actuator": "pct", "sensor": "pct", "setpoint": "pct"})
    c.set_step_config(
        step_from=10.0,
        step_to=40.0,
        duration_s=1.0,
        delay_s=0.2,
        sample_period_s=0.02,
    )
    return c


def esperar_fin(runner, limite=10.0):
    t0 = time.monotonic()
    while runner.is_running() and time.monotonic() - t0 < limite:
        time.sleep(0.01)


# --------------------------------------------------------------------------- #
# Rearme entre ensayos
# --------------------------------------------------------------------------- #


class TestRearme:
    def test_el_segundo_ensayo_tambien_escribe(self, config):
        """El bug del botón de reinicio: el ensayo 2 salía sin señal."""
        escrituras = []
        runner = TestRunnerService(test_config_service=config)
        runner.arm(lambda: escrituras.append)

        runner.start()
        esperar_fin(runner)
        primera = len(escrituras)

        escrituras.clear()
        runner.start()
        esperar_fin(runner)

        assert primera > 0
        assert len(escrituras) == primera

    def test_la_intencion_sobrevive_al_final_del_ensayo(self, config):
        runner = TestRunnerService(test_config_service=config)
        runner.arm(lambda: (lambda v: None))

        runner.start()
        esperar_fin(runner)

        # El escritor se soltó (la HMI recupera la variable)...
        assert runner.writes_enabled is False
        # ...pero la intención del usuario sigue en pie.
        assert runner.write_intent is True

    def test_desarmar_es_definitivo(self, config):
        escrituras = []
        runner = TestRunnerService(test_config_service=config)
        runner.arm(lambda: escrituras.append)
        runner.disarm()

        runner.start()
        esperar_fin(runner)

        assert escrituras == []
        assert runner.write_intent is False

    def test_la_fabrica_se_invoca_en_cada_arranque(self, config):
        """
        Se guarda una fábrica y no el escritor ya construido: si la sesión OPC UA
        se cierra y se reabre, un closure viejo escribiría contra un lector muerto.
        """
        invocaciones = []

        def fabrica():
            invocaciones.append(1)
            return lambda v: None

        runner = TestRunnerService(test_config_service=config)
        runner.arm(fabrica)          # 1ª: al armar

        runner.start()
        esperar_fin(runner)
        runner.start()               # 2ª: rearme del ensayo siguiente
        esperar_fin(runner)

        assert len(invocaciones) == 2


# --------------------------------------------------------------------------- #
# Pre-posicionado
# --------------------------------------------------------------------------- #


class TestPreposicionado:
    def test_lleva_el_actuador_al_valor_inicial_antes_de_grabar(self, config):
        escrituras = []
        runner = TestRunnerService(
            test_config_service=config,
            sensor_provider=lambda: 10.0,   # sensor ya quieto
        )
        runner.arm(lambda: escrituras.append)
        runner._settle_min_s = 0.05
        runner._settle_window_s = 0.1
        runner._settle_poll_s = 0.01

        runner.start(presettle=True)
        esperar_fin(runner)

        assert escrituras[0] == 10.0  # step_from, antes de cualquier grabación

    def test_espera_a_que_el_sensor_deje_de_moverse(self, config):
        """Mientras el sensor deriva no se arranca la grabación."""
        lecturas = iter([0.0, 3.0, 6.0, 8.0, 9.5, 10.0, 10.0, 10.0, 10.0, 10.0])
        ultimo = [10.0]

        def sensor():
            try:
                ultimo[0] = next(lecturas)
            except StopIteration:
                pass
            return ultimo[0]

        runner = TestRunnerService(
            test_config_service=config, sensor_provider=sensor
        )
        runner.arm(lambda: (lambda v: None))
        runner._settle_min_s = 0.01
        runner._settle_window_s = 0.05
        runner._settle_poll_s = 0.01
        runner._settle_tolerance_pct = 0.5

        runner.start(presettle=True)
        esperar_fin(runner)

        assert runner.get_state()["status"] == "finished"

    def test_limpia_el_buffer_al_empezar_a_grabar_no_antes(self, config):
        """
        Lo capturado durante el pre-posicionado es la planta viajando al punto
        de partida. Si entrara en la ventana sería justo el problema que esta
        fase viene a evitar.
        """
        limpiezas = []
        runner = TestRunnerService(
            test_config_service=config,
            sensor_provider=lambda: 10.0,
            on_capture_start=lambda: limpiezas.append(time.monotonic()),
        )
        runner.arm(lambda: (lambda v: None))
        runner._settle_min_s = 0.05
        runner._settle_window_s = 0.1
        runner._settle_poll_s = 0.01

        arranque = time.monotonic()
        runner.start(presettle=True)
        esperar_fin(runner)

        assert len(limpiezas) == 1
        assert limpiezas[0] - arranque >= 0.05  # después de asentar

    def test_sin_escritura_no_hay_pre_posicionado(self, config):
        """
        Sin writer la app no gobierna el actuador: no hay nada que
        pre-posicionar, y limpiar el buffer aquí pisaría un `clear_buffer=false`.
        """
        limpiezas = []
        runner = TestRunnerService(
            test_config_service=config,
            sensor_provider=lambda: 10.0,
            on_capture_start=lambda: limpiezas.append(1),
        )

        runner.start(presettle=True)
        esperar_fin(runner)

        assert limpiezas == []

    def test_desactivarlo_arranca_de_inmediato(self, config):
        limpiezas = []
        runner = TestRunnerService(
            test_config_service=config,
            sensor_provider=lambda: 10.0,
            on_capture_start=lambda: limpiezas.append(1),
        )
        runner.arm(lambda: (lambda v: None))

        runner.start(presettle=False)
        esperar_fin(runner)

        assert limpiezas == []
        assert runner.get_state()["presettle"] is False

    def test_no_se_queda_esperando_para_siempre(self, config):
        """Una planta con deriva propia nunca bajaría de la tolerancia."""
        deriva = [0.0]

        def sensor():
            deriva[0] += 5.0   # nunca se estabiliza
            return deriva[0]

        runner = TestRunnerService(
            test_config_service=config, sensor_provider=sensor
        )
        runner.arm(lambda: (lambda v: None))
        runner._settle_min_s = 0.01
        runner._settle_window_s = 0.05
        runner._settle_poll_s = 0.01
        runner._settle_timeout_s = 0.3

        t0 = time.monotonic()
        runner.start(presettle=True)
        esperar_fin(runner, limite=5.0)

        assert not runner.is_running()
        assert time.monotonic() - t0 < 3.0
        assert runner.get_state()["status"] == "finished"

    def test_se_puede_cancelar_durante_el_asentado(self, config):
        runner = TestRunnerService(
            test_config_service=config, sensor_provider=lambda: 0.0
        )
        runner.arm(lambda: (lambda v: None))
        runner._settle_min_s = 5.0
        runner._settle_poll_s = 0.01

        runner.start(presettle=True)
        time.sleep(0.05)
        assert runner.get_state()["phase"] in (PHASE_PRESET, "baseline")

        runner.stop()
        esperar_fin(runner, limite=2.0)

        assert not runner.is_running()
