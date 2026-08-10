"""Un modelo que no converge tiene que decir por qué, no desaparecer."""

import pytest

from application.services.identification_service import IdentificationService
from domain.models.signals import SignalSeries


def serie(actuator, sensor, dt=0.2):
    n = len(actuator)
    return SignalSeries(
        time=[i * dt for i in range(n)],
        actuator=actuator,
        sensor=sensor,
        setpoint=[0.0] * n,
        signal_type=1,
    )


@pytest.fixture
def svc():
    return IdentificationService()


def escalon_limpio(n=60, dt=0.2):
    """FOPDT de manual: los tres modelos deberían converger."""
    import math

    act = [25.0] * 10 + [75.0] * (n - 10)
    sen = []
    for i in range(n):
        te = (i - 10) * dt
        sen.append(0.0 if te <= 0 else 2.5 * (1 - math.exp(-te / 3.0)))
    return act, sen


# --------------------------------------------------------------------------- #


def test_con_datos_buenos_no_se_descarta_nada(svc):
    act, sen = escalon_limpio()
    resultados, descartados = svc.compare_models_detailed(serie(act, sen))

    assert len(resultados) == 3
    assert descartados == []


def test_compare_models_sigue_devolviendo_solo_la_lista(svc):
    """La firma vieja no cambia: hay código que la usa."""
    act, sen = escalon_limpio()
    resultados = svc.compare_models(serie(act, sen))

    assert [r.model.model_type for r in resultados]


def test_un_modelo_que_falla_reporta_el_motivo(svc, monkeypatch):
    """
    Antes esto era `except Exception: pass`: la tarjeta desaparecía de la
    vista y no había forma de distinguir «no aplica» de «se rompió».
    """

    def revienta(*args, **kwargs):
        raise ValueError("no hay pendiente para estimar el integrador")

    monkeypatch.setattr(svc, "identify_integrating", revienta)

    act, sen = escalon_limpio()
    resultados, descartados = svc.compare_models_detailed(serie(act, sen))

    assert len(resultados) == 2
    assert len(descartados) == 1
    assert descartados[0]["model_type"] == "integrating"
    assert "pendiente" in descartados[0]["reason"]


def test_una_excepcion_sin_mensaje_igual_reporta_algo(svc, monkeypatch):
    def revienta(*args, **kwargs):
        raise RuntimeError()

    monkeypatch.setattr(svc, "identify_sopdt", revienta)

    act, sen = escalon_limpio()
    _, descartados = svc.compare_models_detailed(serie(act, sen))

    assert descartados[0]["reason"] == "RuntimeError"


def test_si_fallan_todos_la_lista_queda_vacia(svc, monkeypatch):
    for nombre in ("identify_fopdt", "identify_sopdt", "identify_integrating"):
        monkeypatch.setattr(svc, nombre, lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))

    act, sen = escalon_limpio()
    resultados, descartados = svc.compare_models_detailed(serie(act, sen))

    assert resultados == []
    assert len(descartados) == 3


def test_la_ventana_invalida_sigue_lanzando(svc):
    """Sin escalón no hay nada que descartar: es un error de la ventana entera."""
    plano = [25.0] * 60

    with pytest.raises(ValueError):
        svc.compare_models_detailed(serie(plano, [0.0] * 60))
