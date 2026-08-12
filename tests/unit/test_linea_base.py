"""
El régimen permanente inicial sale de la línea base, no de la primera muestra.

Caso real: la captura arrancó con la planta todavía subiendo desde cero hasta su
punto de operación (20 %). Tomando `sensor[0]` como estado estable de la entrada
inicial, el modelo simulaba desde 0 mientras la señal medida estaba en 20: toda
la línea base quedaba desplazada, la ganancia absorbía la deriva (1.88 en vez de
1.08) y el R² salía NEGATIVO pese a que la constante de tiempo estaba bien.
"""

import math

import pytest

from application.services.identification_service import IdentificationService
from domain.services.signal_processor import SignalProcessor


def ensayo_con_arranque(n=200, dt=1.0, t_step=100.0, base=20.0, salto=27.0, tau=3.0):
    """La planta sube de 0 a `base`, se asienta, y recibe el escalón."""
    t = [i * dt for i in range(n)]
    act = [25.0 if ti < t_step else 50.0 for ti in t]
    sen = []

    for ti in t:
        arranque = base * (1 - math.exp(-ti / 1.5))
        respuesta = 0.0 if ti < t_step else salto * (1 - math.exp(-(ti - t_step) / tau))
        sen.append(arranque + respuesta)

    return t, act, sen


# --------------------------------------------------------------------------- #
# _baseline
# --------------------------------------------------------------------------- #


def test_ignora_el_arranque_al_principio_de_la_ventana():
    valores = [0.0, 5.0, 12.0, 18.0] + [20.0] * 96

    assert SignalProcessor._baseline(valores, 100) == pytest.approx(20.0)


def test_es_robusta_a_un_pico_de_ruido():
    """Mediana, no media: un pico suelto no debe arrastrar la línea base."""
    valores = [20.0] * 50
    valores[30] = 900.0

    assert SignalProcessor._baseline(valores, 50) == pytest.approx(20.0)


def test_sin_linea_base_usa_el_primer_valor():
    """El escalón cae en la primera muestra: no hay más de dónde sacarlo."""
    assert SignalProcessor._baseline([7.0, 8.0, 9.0], 0) == pytest.approx(7.0)


def test_con_una_sola_muestra_previa():
    assert SignalProcessor._baseline([7.0, 50.0, 50.0], 1) == pytest.approx(7.0)


def test_lista_vacia_no_revienta():
    assert SignalProcessor._baseline([], 5) == 0.0


# --------------------------------------------------------------------------- #
# Efecto sobre la identificación
# --------------------------------------------------------------------------- #


def test_step_info_toma_la_base_no_la_primera_muestra():
    t, act, sen = ensayo_con_arranque()

    info = SignalProcessor.detect_step_info(t, act, sen)

    assert sen[0] == pytest.approx(0.0, abs=0.01)   # la primera muestra es 0
    assert info.initial_y == pytest.approx(20.0, abs=0.1)  # la base real


def test_recupera_la_ganancia_verdadera():
    """Antes la ganancia absorbía la deriva del arranque: 1.88 en vez de 1.08."""
    t, act, sen = ensayo_con_arranque(base=20.0, salto=27.0)

    r = IdentificationService().identify_fopdt(t, act, sen)

    assert r.model.gain == pytest.approx(27.0 / 25.0, rel=0.05)


def test_recupera_la_constante_de_tiempo():
    t, act, sen = ensayo_con_arranque(tau=3.0)

    r = IdentificationService().identify_fopdt(t, act, sen)

    assert r.model.tau == pytest.approx(3.0, rel=0.15)


def test_el_ajuste_deja_de_ser_negativo():
    t, act, sen = ensayo_con_arranque()

    r = IdentificationService().identify_fopdt(t, act, sen)

    assert r.fit_quality > 0.95


def test_la_curva_simulada_arranca_en_la_linea_base():
    """Es lo que se veía descuadrado en el gráfico Medido vs Modelo."""
    t, act, sen = ensayo_con_arranque()

    r = IdentificationService().identify_fopdt(t, act, sen)
    i = 90  # dentro de la línea base, antes del escalón

    assert r.simulated[i] == pytest.approx(sen[i], abs=0.5)


def test_un_ensayo_limpio_sigue_saliendo_igual():
    """Sin arranque sucio, el resultado no debe cambiar."""
    n, dt, t_step = 200, 1.0, 100.0
    t = [i * dt for i in range(n)]
    act = [25.0 if ti < t_step else 50.0 for ti in t]
    sen = [
        20.0 + (0.0 if ti < t_step else 27.0 * (1 - math.exp(-(ti - t_step) / 3.0)))
        for ti in t
    ]

    r = IdentificationService().identify_fopdt(t, act, sen)

    assert r.fit_quality > 0.98
    assert r.model.gain == pytest.approx(27.0 / 25.0, rel=0.05)
