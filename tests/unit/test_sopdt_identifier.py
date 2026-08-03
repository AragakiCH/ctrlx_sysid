import pytest

from domain.services.sopdt_identifier import SOPDTIdentifier


def test_identifica_sopdt_con_buen_ajuste(sopdt_series):
    result = SOPDTIdentifier().identify(
        sopdt_series["time"],
        sopdt_series["actuator"],
        sopdt_series["sensor"],
    )

    assert result.model.model_type == "sopdt"
    assert result.fit_quality > 0.98


def test_recupera_ganancia(sopdt_series):
    result = SOPDTIdentifier().identify(
        sopdt_series["time"],
        sopdt_series["actuator"],
        sopdt_series["sensor"],
    )

    assert result.model.gain == pytest.approx(sopdt_series["k"], rel=0.1)


def test_tau1_es_la_constante_dominante(sopdt_series):
    result = SOPDTIdentifier().identify(
        sopdt_series["time"],
        sopdt_series["actuator"],
        sopdt_series["sensor"],
    )

    assert result.model.tau1 >= result.model.tau2


def test_el_refinamiento_mejora_la_semilla(sopdt_series):
    """El ajuste numérico debe ser al menos tan bueno como la heurística."""
    identifier = SOPDTIdentifier()

    result = identifier.identify(
        sopdt_series["time"],
        sopdt_series["actuator"],
        sopdt_series["sensor"],
    )

    # Semilla original (0.6x / 0.4x) sin refinar
    seed_sim = identifier.simulate_response(
        time_data=sopdt_series["time"],
        gain=sopdt_series["k"],
        tau1=0.6 * (sopdt_series["tau1"] + sopdt_series["tau2"]),
        tau2=0.4 * (sopdt_series["tau1"] + sopdt_series["tau2"]),
        dead_time=sopdt_series["dead_time"],
        initial_u=sopdt_series["actuator"][0],
        initial_y=sopdt_series["sensor"][0],
        actuator_data=sopdt_series["actuator"],
    )
    seed_r2 = identifier.calculate_r2(sopdt_series["sensor"], seed_sim)

    assert result.fit_quality >= seed_r2


def test_denominador_de_segundo_orden(sopdt_series):
    result = SOPDTIdentifier().identify(
        sopdt_series["time"],
        sopdt_series["actuator"],
        sopdt_series["sensor"],
    )

    assert len(result.model.denominator) == 3
    assert result.model.denominator[-1] == 1.0


def test_polos_repetidos_no_dividen_por_cero():
    factor = SOPDTIdentifier._response_factor(5.0, 3.0, 3.0)
    assert 0.0 < factor < 1.0
