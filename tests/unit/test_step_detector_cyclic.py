"""
La ventana de identificación no debe cruzar el escalón anterior.

Caso real: un programa de PLC que cicla (4 mA durante 5 s, 12 mA durante 15 s,
en bucle). Si el retardo configurado pide 10 s de línea base, la ventana se
estira hacia atrás hasta la fase alta del ciclo previo y el actuador termina
valiendo lo mismo al principio y al final: el identificador concluye que no
hubo escalón.
"""

import pytest

from application.services.step_detector_service import StepDetectorService
from domain.models.signals import SignalSeries


def plc_ciclico(n=600, dt=0.2, periodo=20.0, t_bajo=5.0, bajo=0.0, alto=50.0):
    """Reproduce el PLC_PRG del usuario, ya convertido a % de span."""
    time_data, actuator, sensor = [], [], []
    y = bajo

    for i in range(n):
        t = i * dt
        ciclo = t % periodo
        if ciclo < dt:
            y = bajo
        u = bajo if ciclo < t_bajo else alto
        y = y + (u - y) * 0.05

        time_data.append(t)
        actuator.append(u)
        sensor.append(y)

    return SignalSeries(
        time=time_data,
        actuator=actuator,
        sensor=sensor,
        setpoint=[alto] * n,
        signal_type=1,
    )


@pytest.fixture
def detector():
    return StepDetectorService(min_step_delta=25.0)


class TestFindPreviousStep:
    def test_encuentra_la_transicion_anterior(self, detector):
        series = plc_ciclico()
        ultimo = detector.find_latest_rising_step_index(series.actuator)
        anterior = detector.find_previous_step_index(series.actuator, ultimo)

        assert anterior is not None
        assert anterior < ultimo
        # La bajada 12 -> 4 del mismo ciclo, 5 s antes del escalón de subida.
        assert series.time[ultimo] - series.time[anterior] == pytest.approx(5.0)

    def test_sin_transicion_previa_devuelve_none(self, detector):
        series = SignalSeries(
            time=[i * 0.2 for i in range(100)],
            actuator=[0.0] * 20 + [50.0] * 80,
            sensor=[0.0] * 100,
            setpoint=[50.0] * 100,
            signal_type=1,
        )
        assert detector.find_previous_step_index(series.actuator, 20) is None

    def test_indice_cero_devuelve_none(self, detector):
        assert detector.find_previous_step_index([0.0, 50.0], 0) is None


class TestVentanaNoCruzaElEscalonAnterior:
    def test_la_linea_base_se_recorta(self, detector):
        series = plc_ciclico()
        step_index = detector.find_latest_rising_step_index(series.actuator)

        # 50 muestras = 10 s de línea base, más que los 5 s de la fase baja.
        window = detector.extract_window_from_step_index(
            series, step_index, pre_samples=50, post_samples=75
        )

        assert window is not None
        # Recortada a los 5 s reales de fase baja: 25 muestras antes del salto.
        assert len(window.time) == 100

    def test_el_actuador_si_cambia_dentro_de_la_ventana(self, detector):
        """Es exactamente lo que fallaba: primero y último valor eran iguales."""
        series = plc_ciclico()
        step_index = detector.find_latest_rising_step_index(series.actuator)

        window = detector.extract_window_from_step_index(
            series, step_index, pre_samples=50, post_samples=75
        )

        assert window.actuator[0] != window.actuator[-1]
        assert window.actuator[0] == pytest.approx(0.0)
        assert window.actuator[-1] == pytest.approx(50.0)

    def test_la_ventana_contiene_una_sola_transicion(self, detector):
        series = plc_ciclico()
        step_index = detector.find_latest_rising_step_index(series.actuator)

        window = detector.extract_window_from_step_index(
            series, step_index, pre_samples=50, post_samples=75
        )

        transiciones = sum(
            1
            for i in range(1, len(window.actuator))
            if abs(window.actuator[i] - window.actuator[i - 1]) >= 25.0
        )
        assert transiciones == 1

    def test_una_senal_no_ciclica_no_se_ve_afectada(self, detector):
        """Con un solo escalón la ventana debe seguir tomando toda la línea base."""
        series = SignalSeries(
            time=[i * 0.2 for i in range(200)],
            actuator=[0.0] * 100 + [50.0] * 100,
            sensor=[0.0] * 100 + [25.0] * 100,
            setpoint=[50.0] * 200,
            signal_type=1,
        )

        window = detector.extract_window_from_step_index(
            series, 100, pre_samples=50, post_samples=75
        )

        assert len(window.time) == 125  # 50 antes + 75 después, sin recorte


# --------------------------------------------------------------------------- #
# La ventana no cruza la transición SIGUIENTE
# --------------------------------------------------------------------------- #


def test_find_next_transition_detecta_la_vuelta_al_valor_inicial():
    from application.services.step_detector_service import StepDetectorService

    det = StepDetectorService(min_step_delta=12.5)
    u = [25.0] * 10 + [50.0] * 20 + [25.0] * 5

    assert det.find_next_transition_index(u, after_index=10) == 30


def test_find_next_transition_detecta_una_vuelta_en_rampa():
    """Con rate limiter el actuador no cae de golpe: se compara contra el máximo."""
    from application.services.step_detector_service import StepDetectorService

    det = StepDetectorService(min_step_delta=12.5)
    rampa = [50.0 - 2.5 * i for i in range(1, 11)]   # 47.5 ... 25.0
    u = [25.0] * 10 + [50.0] * 20 + rampa

    k = det.find_next_transition_index(u, after_index=10)
    assert k is not None
    assert u[k] <= 50.0 - 12.5
    assert u[k - 1] > 50.0 - 12.5


def test_find_next_transition_ignora_la_propia_subida_en_rampa():
    from application.services.step_detector_service import StepDetectorService

    det = StepDetectorService(min_step_delta=12.5)
    subida = [25.0 + 2.5 * i for i in range(1, 11)]
    u = [25.0] * 10 + subida + [50.0] * 20

    assert det.find_next_transition_index(u, after_index=10) is None


def test_la_ventana_se_corta_donde_el_actuador_vuelve():
    from application.services.step_detector_service import StepDetectorService
    from domain.models.signals import SignalSeries

    det = StepDetectorService(min_step_delta=12.5)
    n_pre, n_step, n_back = 20, 30, 10
    u = [25.0] * n_pre + [50.0] * n_step + [25.0] * n_back
    y = [20.0] * n_pre + [45.0] * n_step + [45.0] * n_back
    t = [0.2 * i for i in range(len(u))]
    series = SignalSeries(time=t, actuator=u, sensor=y, setpoint=[], signal_type=0)

    step = det.find_latest_rising_step_index(u)
    assert step == n_pre

    # Se piden más muestras de las que hay antes de la vuelta.
    win = det.extract_window_from_step_index(series, step, pre_samples=10, post_samples=n_step + 5)

    assert win is not None
    assert max(win.actuator) == 50.0
    assert win.actuator[-1] == 50.0, "la ventana no debe incluir la vuelta a 25"
    assert len(win.actuator) == 10 + n_step
