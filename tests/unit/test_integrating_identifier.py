import pytest

from domain.services.integrating_identifier import IntegratingIdentifier


def test_identifica_ganancia_integradora(integrating_series):
    result = IntegratingIdentifier().identify(
        integrating_series["time"],
        integrating_series["actuator"],
        integrating_series["sensor"],
    )

    assert result.model.model_type == "integrating"
    assert result.model.gain == pytest.approx(integrating_series["k"], rel=0.1)


def test_dead_time_no_es_siempre_cero(integrating_series):
    """Regresión: antes dead_time se calculaba como t[i] - t[i], siempre 0."""
    result = IntegratingIdentifier().identify(
        integrating_series["time"],
        integrating_series["actuator"],
        integrating_series["sensor"],
    )

    assert result.model.dead_time > 0.0
    assert result.model.dead_time == pytest.approx(
        integrating_series["dead_time"], abs=2.0
    )


def test_buen_ajuste_en_datos_sinteticos(integrating_series):
    result = IntegratingIdentifier().identify(
        integrating_series["time"],
        integrating_series["actuator"],
        integrating_series["sensor"],
    )

    assert result.fit_quality > 0.95


def test_denominador_es_s_puro(integrating_series):
    result = IntegratingIdentifier().identify(
        integrating_series["time"],
        integrating_series["actuator"],
        integrating_series["sensor"],
    )

    assert result.model.denominator == [1.0, 0.0]


def test_estimate_dead_time_sin_movimiento_devuelve_cero():
    time_data = [i * 1.0 for i in range(30)]
    sensor = [5.0] * 30

    assert IntegratingIdentifier.estimate_dead_time(time_data, sensor, 5) == 0.0


def test_simulacion_tiene_la_misma_longitud(integrating_series):
    simulated = IntegratingIdentifier.simulate_response(
        time_data=integrating_series["time"],
        gain=0.05,
        dead_time=4.0,
        initial_u=4.0,
        initial_y=10.0,
        actuator_data=integrating_series["actuator"],
    )

    assert len(simulated) == len(integrating_series["time"])
