"""
El detector de escalón no puede depender del periodo de muestreo.

Caso real que lo destapó: un archivo importado de 917 muestras a 20 ms donde el
actuador rampea de 15 % a 60 % en 2.4 s. El salto completo (45 %) supera de
sobra el umbral (22.5 %), pero la implementación anterior comparaba dentro de
una ventana medida en MUESTRAS y topada en 60: a 20 ms eso cubre 1.2 s, la
mitad de la rampa, así que nunca acumulaba el umbral y se reportaba
"no se detectó ningún escalón" en un registro que claramente tenía uno.
"""

import pytest

from application.services.step_detector_service import StepDetectorService


def rampa(n, dt, t_ini, t_fin, bajo, alto):
    """Actuador plano en `bajo`, rampa lineal hasta `alto`, plano."""
    out = []
    for i in range(n):
        t = i * dt
        if t < t_ini:
            out.append(bajo)
        elif t > t_fin:
            out.append(alto)
        else:
            out.append(bajo + (alto - bajo) * (t - t_ini) / (t_fin - t_ini))
    return out


# --------------------------------------------------------------------------- #
# El caso que falló
# --------------------------------------------------------------------------- #


def test_rampa_larga_con_muestreo_rapido():
    """917 muestras a 20 ms, rampa de 2.4 s. Antes devolvía None."""
    dt = 0.02
    act = rampa(917, dt, 10.3, 12.7, 15.0, 60.0)

    idx = StepDetectorService(min_step_delta=22.5).find_latest_rising_step_index(act)

    assert idx is not None
    assert idx * dt == pytest.approx(10.3, abs=0.05)


def test_el_mismo_ensayo_a_cualquier_periodo_da_el_mismo_instante():
    """
    El resultado tiene que depender de la SEÑAL, no de a qué ritmo se muestreó.
    Es lo que el ancho fijo en muestras rompía: a 20 ms no detectaba nada y a
    200 ms sí, con el mismo ensayo.

    La tolerancia es UN periodo de muestreo, que es la precisión máxima
    alcanzable: el instante real cae entre dos muestras y se devuelve la
    primera ya en transición.
    """
    for dt in (0.01, 0.02, 0.05, 0.1, 0.2):
        n = int(20.0 / dt)
        act = rampa(n, dt, 10.0, 12.0, 15.0, 60.0)
        idx = StepDetectorService(min_step_delta=22.5).find_latest_rising_step_index(act)

        esperado = round(10.0 / dt)

        assert idx is not None, f"no detectó nada con dt={dt}"
        # En índices, no en segundos: comparar tiempos falla por el último bit
        # del flotante cuando el error es exactamente un periodo.
        assert abs(idx - esperado) <= 1, f"desviado {idx - esperado} muestras con dt={dt}"


def test_rampa_muy_lenta():
    """45 % repartidos en 16 s: ninguna ventana razonable lo cubriría."""
    dt = 0.02
    act = rampa(900, dt, 1.0, 17.0, 15.0, 60.0)

    idx = StepDetectorService(min_step_delta=22.5).find_latest_rising_step_index(act)

    assert idx is not None
    assert idx * dt == pytest.approx(1.0, abs=0.1)


# --------------------------------------------------------------------------- #
# Lo que ya funcionaba y no debe romperse
# --------------------------------------------------------------------------- #


def test_escalon_instantaneo():
    act = [8.0] * 50 + [12.0] * 100

    idx = StepDetectorService(min_step_delta=2.0).find_latest_rising_step_index(act)

    assert idx == 50


def test_devuelve_la_primera_muestra_en_transicion():
    """
    Misma convención que `SignalProcessor.detect_step_info`. Desfasarlas una
    muestra descuadraría `step_time` y el tiempo muerto de todos los modelos.
    """
    act = [0.0] * 30 + [50.0] * 30

    idx = StepDetectorService(min_step_delta=25.0).find_latest_rising_step_index(act)

    assert act[idx] == 50.0
    assert act[idx - 1] == 0.0


def test_señal_ciclica_toma_la_ultima_subida():
    act = ([4.0] * 25 + [12.0] * 75) * 3

    idx = StepDetectorService(min_step_delta=2.0).find_latest_rising_step_index(act)

    assert idx == 225


def test_sin_movimiento_devuelve_none():
    assert (
        StepDetectorService(min_step_delta=2.0).find_latest_rising_step_index([10.0] * 200)
        is None
    )


def test_solo_bajada_devuelve_none():
    """Solo interesan las subidas: una caída no es el escalón del ensayo."""
    act = [12.0] * 100 + [4.0] * 100

    assert (
        StepDetectorService(min_step_delta=2.0).find_latest_rising_step_index(act) is None
    )


def test_subida_por_debajo_del_umbral_no_cuenta():
    act = [10.0] * 50 + [11.0] * 50

    assert (
        StepDetectorService(min_step_delta=5.0).find_latest_rising_step_index(act) is None
    )


def test_serie_demasiado_corta():
    assert StepDetectorService().find_latest_rising_step_index([1.0]) is None
    assert StepDetectorService().find_latest_rising_step_index([]) is None


def test_ruido_en_la_linea_base_no_arrastra_el_inicio():
    """Con ruido, el pie no debe irse al principio del registro."""
    import random

    random.seed(11)
    base = [15.0 + random.gauss(0, 0.05) for _ in range(300)]
    subida = [15.0 + 45.0 * i / 100 for i in range(1, 101)]
    act = base + subida + [60.0] * 100

    idx = StepDetectorService(min_step_delta=22.5).find_latest_rising_step_index(act)

    assert idx is not None
    assert 295 < idx < 340
