"""
El "Tiempo de muestreo" de la vista tiene que llegar al lector del PLC.

Antes solo alimentaba a `TestConfigService`, que lo usa para dimensionar las
ventanas de identificación. El `PLCReader` conservaba el periodo con el que se
construyó (0.2 s fijos en `main.py`), así que pedir 20 ms no cambiaba nada: el
muestreo real seguía en 200 ms y la vista avisaba de que la lectura iba 10×
más lenta que lo configurado.
"""

import pytest

from application.services.opcua_session_service import OpcUaSessionService


class ReaderFalso:
    def __init__(self, period_s):
        self.period_s = period_s

    def stop(self):
        pass

    @property
    def is_running(self):
        return True


@pytest.fixture
def servicio():
    return OpcUaSessionService(on_sample=lambda s: None, period_s=0.2)


def test_arranca_con_el_periodo_inicial(servicio):
    assert servicio.period_s == 0.2


def test_cambiar_el_periodo_sin_sesion_no_falla(servicio):
    assert servicio.set_period(0.02) == 0.02
    assert servicio.period_s == 0.02


def test_el_periodo_llega_al_lector_en_caliente(servicio):
    """Lo que faltaba: el lector seguía en su periodo original."""
    servicio._reader = ReaderFalso(0.2)

    servicio.set_period(0.02)

    assert servicio._reader.period_s == 0.02


def test_una_sesion_nueva_hereda_el_periodo_vigente(servicio):
    servicio.set_period(0.05)

    # `login` construye el PLCReader con `self._period_s`.
    assert servicio.period_s == 0.05


def test_rechaza_un_periodo_no_positivo(servicio):
    for malo in (0, -0.1):
        with pytest.raises(ValueError, match="mayor que cero"):
            servicio.set_period(malo)


def test_rechaza_un_periodo_no_numerico(servicio):
    with pytest.raises(ValueError, match="número"):
        servicio.set_period("rápido")


def test_un_periodo_invalido_no_deja_el_estado_a_medias(servicio):
    servicio._reader = ReaderFalso(0.2)

    with pytest.raises(ValueError):
        servicio.set_period(-1)

    assert servicio.period_s == 0.2
    assert servicio._reader.period_s == 0.2
