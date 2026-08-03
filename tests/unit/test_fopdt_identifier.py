import pytest

from domain.services.fopdt_identifier import FOPDTIdentifier


def test_identifica_ganancia_correcta(fopdt_series):
    result = FOPDTIdentifier().identify(
        fopdt_series["time"],
        fopdt_series["actuator"],
        fopdt_series["sensor"],
    )

    assert result.model.model_type == "fopdt"
    assert result.model.gain == pytest.approx(fopdt_series["k"], rel=0.05)


def test_identifica_tau_y_dead_time(fopdt_series):
    result = FOPDTIdentifier().identify(
        fopdt_series["time"],
        fopdt_series["actuator"],
        fopdt_series["sensor"],
    )

    assert result.model.tau == pytest.approx(fopdt_series["tau"], rel=0.15)
    assert result.model.dead_time == pytest.approx(fopdt_series["dead_time"], abs=1.5)


def test_ajuste_casi_perfecto_en_datos_sinteticos(fopdt_series):
    result = FOPDTIdentifier().identify(
        fopdt_series["time"],
        fopdt_series["actuator"],
        fopdt_series["sensor"],
    )

    assert result.fit_quality > 0.98
    assert len(result.simulated) == len(fopdt_series["sensor"])


def test_denominador_es_tau_s_mas_uno(fopdt_series):
    result = FOPDTIdentifier().identify(
        fopdt_series["time"],
        fopdt_series["actuator"],
        fopdt_series["sensor"],
    )

    assert result.model.denominator == [result.model.tau, 1.0]


def test_rechaza_ventana_sin_escalon():
    time_data = [i * 0.5 for i in range(50)]
    actuator = [4.0] * 50
    sensor = [8.0] * 50

    with pytest.raises(ValueError):
        FOPDTIdentifier().identify(time_data, actuator, sensor)


def test_rechaza_pocas_muestras():
    time_data = [0.0, 1.0, 2.0]
    actuator = [4.0, 12.0, 12.0]
    sensor = [0.0, 1.0, 2.0]

    with pytest.raises(ValueError):
        FOPDTIdentifier().identify(time_data, actuator, sensor)
