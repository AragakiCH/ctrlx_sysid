"""
Ciclo de tarea del PLC.

El `Intervalo` del MainTask no es una variable del programa: vive en la
configuración de tareas y no se exporta bajo `sym`, así que no hay nodo OPC UA
que escribir. Todo esto trabaja sobre una **variable puente** de PLC_PRG que el
código IEC del PLC usa para ajustar el intervalo.

Lo que se protege aquí es sobre todo que un número mal puesto en el navegador
no pare una planta.
"""

import pytest

from application.services.task_cycle_service import (
    MAX_CYCLE_MS,
    MIN_CYCLE_MS,
    TaskCycleService,
)


class ReaderFalso:
    def __init__(self, valores=None, escribible=True, motivo="solo lectura"):
        self.valores = valores if valores is not None else {"uiTaskCycleMs": 20.0}
        self.escribible = escribible
        self.motivo = motivo
        self.escrituras = []

    def read_variable_value(self, nombre):
        return self.valores.get(nombre)

    def can_write_variable(self, nombre):
        if nombre not in self.valores:
            return False, f"La variable '{nombre}' no existe en el programa."
        if not self.escribible:
            return False, self.motivo
        return True, "escribible"

    def write_variable_value(self, nombre, valor):
        self.escrituras.append((nombre, valor))
        self.valores[nombre] = valor


class SesionFalsa:
    def __init__(self, reader=None, period_s=0.1):
        self.reader = reader
        self.requested_period_s = period_s


@pytest.fixture
def reader():
    return ReaderFalso()


@pytest.fixture
def servicio(reader):
    s = TaskCycleService(SesionFalsa(reader))
    s.configure(variable="uiTaskCycleMs")
    return s


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #


def test_sin_variable_todo_queda_inactivo():
    s = TaskCycleService(SesionFalsa(ReaderFalso()))

    estado = s.status()

    assert estado["variable"] is None
    assert estado["sync_enabled"] is False
    assert estado["writable"] is False


def test_activar_la_sincronizacion_sin_variable_no_prende_nada():
    """Prometería un ajuste que no tiene a dónde escribir."""
    s = TaskCycleService(SesionFalsa(ReaderFalso()))

    estado = s.configure(sync_enabled=True)

    assert estado["sync_enabled"] is False


def test_cadena_vacia_desactiva_la_variable(servicio):
    estado = servicio.configure(variable="   ")

    assert estado["variable"] is None
    assert estado["sync_enabled"] is False


def test_lee_el_ciclo_que_reporta_el_plc(servicio):
    assert servicio.status()["cycle_ms"] == 20.0


def test_una_variable_que_no_existe_no_es_escribible(reader):
    s = TaskCycleService(SesionFalsa(reader))
    s.configure(variable="NoExiste")

    estado = s.status()

    assert estado["writable"] is False
    assert "no existe" in estado["reason"]


# --------------------------------------------------------------------------- #
# Sobremuestreo
# --------------------------------------------------------------------------- #


def test_avisa_cuando_se_mira_mas_rapido_de_lo_que_el_plc_calcula(reader):
    """
    Por debajo del ciclo de tarea la variable todavía no cambió: llegan
    muestras repetidas, que a la identificación le parecen una señal
    escalonada. Es el síntoma que hay que poder explicar.
    """
    s = TaskCycleService(SesionFalsa(reader, period_s=0.010))   # 10 ms
    s.configure(variable="uiTaskCycleMs")                        # tarea a 20 ms

    assert s.status()["oversampling"] is True


def test_muestrear_mas_despacio_que_el_plc_no_es_problema(reader):
    s = TaskCycleService(SesionFalsa(reader, period_s=0.100))

    s.configure(variable="uiTaskCycleMs")

    assert s.status()["oversampling"] is False


# --------------------------------------------------------------------------- #
# Escritura con límites
# --------------------------------------------------------------------------- #


def test_escribe_el_ciclo_pedido(servicio, reader):
    servicio.set_cycle_ms(10.0)

    assert reader.escrituras == [("uiTaskCycleMs", 10.0)]


def test_rechaza_un_ciclo_por_debajo_del_suelo(servicio, reader):
    """Sin margen para terminar el ciclo, el watchdog manda el PLC a STOP."""
    with pytest.raises(ValueError, match="watchdog"):
        servicio.set_cycle_ms(MIN_CYCLE_MS - 1)

    assert reader.escrituras == []


def test_rechaza_un_ciclo_por_encima_del_techo(servicio, reader):
    with pytest.raises(ValueError, match="frenaría"):
        servicio.set_cycle_ms(MAX_CYCLE_MS + 1)

    assert reader.escrituras == []


def test_rechaza_lo_que_no_es_un_numero(servicio):
    with pytest.raises(ValueError, match="número"):
        servicio.set_cycle_ms("rapido")


def test_no_escribe_si_la_variable_es_de_solo_lectura(reader):
    reader.escribible = False
    reader.motivo = "El servidor OPC UA la declara como solo lectura."
    s = TaskCycleService(SesionFalsa(reader))
    s.configure(variable="uiTaskCycleMs")

    with pytest.raises(ValueError, match="solo lectura"):
        s.set_cycle_ms(10.0)

    assert reader.escrituras == []


def test_sin_sesion_no_se_escribe():
    s = TaskCycleService(SesionFalsa(reader=None))
    s.configure(variable="uiTaskCycleMs")

    with pytest.raises(ValueError, match="sesión"):
        s.set_cycle_ms(10.0)


# --------------------------------------------------------------------------- #
# Sincronización con el tiempo de muestreo
# --------------------------------------------------------------------------- #


def test_apagada_no_toca_el_plc(servicio, reader):
    servicio.sync_with_period(0.005)

    assert reader.escrituras == []


def test_baja_el_ciclo_cuando_se_pide_muestrear_mas_rapido(servicio, reader):
    servicio.configure(sync_enabled=True)

    servicio.sync_with_period(0.010)   # 10 ms, con la tarea en 20 ms

    assert reader.escrituras == [("uiTaskCycleMs", 10.0)]


def test_no_frena_el_plc_al_muestrear_mas_despacio(servicio, reader):
    """
    Mirar cada 500 ms no exige que el PLC calcule cada 500 ms. Subir el ciclo
    degradaría el control sin ganar nada en la identificación.
    """
    servicio.configure(sync_enabled=True)

    servicio.sync_with_period(0.500)

    assert reader.escrituras == []


def test_no_reescribe_si_el_ciclo_ya_alcanza(servicio, reader):
    """Escribir el mismo valor sería tráfico y riesgo por nada."""
    servicio.configure(sync_enabled=True)

    servicio.sync_with_period(0.020)   # justo el ciclo actual

    assert reader.escrituras == []


def test_la_sincronizacion_respeta_el_suelo(servicio, reader):
    """Pedir 1 ms de muestreo no puede llevar la tarea a 1 ms."""
    servicio.configure(sync_enabled=True)

    servicio.sync_with_period(0.001)

    assert reader.escrituras == [("uiTaskCycleMs", MIN_CYCLE_MS)]


def test_un_fallo_al_sincronizar_no_lanza(reader):
    """
    Se llama desde el guardado de la config del ensayo: que el PLC rechace el
    cambio no puede tumbar el guardado.
    """
    reader.escribible = False
    s = TaskCycleService(SesionFalsa(reader))
    s.configure(variable="uiTaskCycleMs", sync_enabled=True)

    resultado = s.sync_with_period(0.010)

    assert "error" in resultado
