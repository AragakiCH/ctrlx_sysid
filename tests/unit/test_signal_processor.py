import pytest

from domain.services.signal_processor import SignalProcessor


def test_ma_a_porcentaje():
    assert SignalProcessor.ma_to_percent([4.0, 12.0, 20.0]) == pytest.approx(
        [0.0, 50.0, 100.0]
    )


def test_porcentaje_a_ma():
    assert SignalProcessor.percent_to_ma([0.0, 50.0, 100.0]) == pytest.approx(
        [4.0, 12.0, 20.0]
    )


def test_conversion_ida_y_vuelta():
    original = [4.0, 7.3, 15.9, 20.0]
    ida = SignalProcessor.ma_to_percent(original)
    vuelta = SignalProcessor.percent_to_ma(ida)
    assert vuelta == pytest.approx(original)


def test_normalize_lleva_a_cero_uno():
    assert SignalProcessor.normalize([10.0, 15.0, 20.0]) == pytest.approx(
        [0.0, 0.5, 1.0]
    )


def test_normalize_serie_constante_no_divide_por_cero():
    assert SignalProcessor.normalize([7.0, 7.0, 7.0]) == [0.0, 0.0, 0.0]


def test_normalize_lista_vacia():
    assert SignalProcessor.normalize([]) == []


def test_detecta_indice_e_instante_del_escalon():
    time_data = [i * 0.5 for i in range(20)]
    actuator = [4.0] * 8 + [12.0] * 12
    sensor = [0.0] * 8 + [float(i) for i in range(12)]

    info = SignalProcessor.detect_step_info(time_data, actuator, sensor)

    assert info.step_index == 8
    assert info.step_time == pytest.approx(4.0)
    assert info.delta_u == pytest.approx(8.0)


def test_step_info_calcula_deltas():
    time_data = [i * 1.0 for i in range(10)]
    actuator = [4.0] * 3 + [10.0] * 7
    sensor = [2.0] * 3 + [5.0] * 7

    info = SignalProcessor.detect_step_info(time_data, actuator, sensor)

    assert info.initial_u == 4.0
    assert info.final_u == 10.0
    assert info.initial_y == 2.0
    assert info.final_y == 5.0
    assert info.delta_y == pytest.approx(3.0)


def test_validate_exige_veinte_muestras():
    time_data = [i * 1.0 for i in range(10)]
    actuator = [4.0] * 5 + [12.0] * 5
    sensor = [0.0] * 5 + [1.0] * 5

    with pytest.raises(ValueError, match="20 muestras"):
        SignalProcessor.validate_identification_window(time_data, actuator, sensor)


def test_validate_exige_misma_longitud():
    time_data = [i * 1.0 for i in range(30)]
    actuator = [4.0] * 30
    sensor = [0.0] * 25

    with pytest.raises(ValueError, match="misma longitud"):
        SignalProcessor.validate_identification_window(time_data, actuator, sensor)


def test_validate_exige_respuesta_en_el_sensor():
    time_data = [i * 1.0 for i in range(30)]
    actuator = [4.0] * 10 + [12.0] * 20
    sensor = [3.0] * 30

    with pytest.raises(ValueError, match="sensor"):
        SignalProcessor.validate_identification_window(time_data, actuator, sensor)
