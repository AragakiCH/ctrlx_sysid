"""
La muestra tiene que llevar el catálogo COMPLETO de variables del programa.

`raw` solo trae las variables con rol asignado, porque muestrear el programa
entero cuesta un viaje de red por variable y hundía el periodo de muestreo. Pero
los desplegables del paso 1 tienen que ofrecer todas: si se pueblan desde `raw`,
el usuario solo puede elegir entre las que ya están elegidas.

El síntoma engaña. En un programa con tantas variables como roles la lista se ve
completa y no se nota nada; solo aparece cuando el programa tiene variables de
sobra, que es el caso real.
"""

import threading

import pytest

from infrastructure.ctrlx.plc_reader import PLCReader

PROGRAMA = [
    "t_psl_timeout",
    "SP_max_FWD",
    "Velocidad_Scaled_pct",
    "HMI_SP_Local_Automatico",
    "bEnable",
    "rTemperatura",
    "nEstado",
]


class _RepoFalso:
    def __init__(self, nombres):
        self._nombres = nombres
        self.lecturas_de_valores = 0

    def list_variable_names(self, program_node):
        return list(self._nombres)

    def find_variable_node(self, program_node, name):
        return name if name in self._nombres else None


class _OpcFalso:
    def __init__(self):
        self.leidas = []

    def read_value(self, node):
        self.leidas.append(node)
        return 10.0 if node == "t_psl_timeout" else 20.0


def _reader(mapping, nombres=PROGRAMA):
    """PLCReader sin red, con repo y cliente OPC UA falsos."""
    r = PLCReader.__new__(PLCReader)
    r.mapping = mapping
    r.include_raw = True
    r._io_lock = threading.RLock()
    r._node_cache = {}
    r._program_node = object()
    r._repo = _RepoFalso(nombres)
    r._opc = _OpcFalso()
    r._variable_names = []
    r._catalog_ts = 0.0
    r._clock_start = None
    r._last_sample_monotonic = None
    r._last_read_duration_s = None
    r._last_interval_s = None
    return r


@pytest.fixture
def mapeo_parcial():
    """Solo dos variables mapeadas, como en el PLC del compañero."""
    return {
        "time": None,
        "actuator": "SP_max_FWD",
        "sensor": "t_psl_timeout",
        "setpoint": "SP_max_FWD",
        "signal_type": None,
    }


class TestCatalogo:
    def test_la_muestra_trae_todas_las_variables(self, mapeo_parcial):
        sample = _reader(mapeo_parcial)._build_sample(object())

        assert sample["variables"] == PROGRAMA

    def test_raw_solo_trae_las_mapeadas(self, mapeo_parcial):
        sample = _reader(mapeo_parcial)._build_sample(object())

        assert set(sample["raw"]) == {"SP_max_FWD", "t_psl_timeout"}

    def test_el_catalogo_no_depende_del_mapeo(self, mapeo_parcial):
        """Es lo que rompía el desplegable: 2 opciones en vez de 7."""
        sample = _reader(mapeo_parcial)._build_sample(object())

        assert len(sample["variables"]) > len(sample["raw"])
        assert len(sample["variables"]) == len(PROGRAMA)

    def test_una_variable_en_dos_roles_se_lee_una_sola_vez(self, mapeo_parcial):
        """SP_max_FWD es actuador y setpoint: no tiene sentido pagarla dos veces."""
        reader = _reader(mapeo_parcial)
        reader._build_sample(object())

        assert reader._opc.leidas.count("SP_max_FWD") == 1

    def test_sin_roles_mapeados_el_catalogo_sigue_llegando(self):
        """Al conectar, antes de que el usuario elija nada, ya hay que ofrecerle la lista."""
        vacio = dict.fromkeys(
            ("time", "actuator", "sensor", "setpoint", "signal_type"), None
        )
        sample = _reader(vacio, nombres=["foo", "bar", "baz"])._build_sample(object())

        assert sample["variables"] == ["foo", "bar", "baz"]


class TestEjeDeTiempo:
    def test_el_tiempo_no_sale_del_plc(self, mapeo_parcial):
        """
        Un contador del PLC que se reinicia cíclicamente da un eje en diente de
        sierra y arruina la integración del modelo. El eje real es el instante
        de captura; el del PLC queda como referencia en `plc_time`.
        """
        mapeo = {**mapeo_parcial, "time": "t_psl_timeout"}
        sample = _reader(mapeo)._build_sample(object())

        assert sample["plc_time"] == 10.0      # lo que dice el PLC
        assert sample["time"] != 10.0          # el eje real
        assert sample["time"] >= 0.0

    def test_el_tiempo_avanza_entre_muestras(self, mapeo_parcial):
        reader = _reader(mapeo_parcial)

        primera = reader._build_sample(object())["time"]
        segunda = reader._build_sample(object())["time"]

        assert segunda >= primera


class TestDiagnosticoDeMuestreo:
    def test_la_primera_muestra_no_tiene_intervalo(self, mapeo_parcial):
        sample = _reader(mapeo_parcial)._build_sample(object())

        assert sample["sample_interval_s"] is None
        assert sample["read_duration_s"] is not None

    def test_a_partir_de_la_segunda_se_mide_el_intervalo(self, mapeo_parcial):
        reader = _reader(mapeo_parcial)
        reader._build_sample(object())
        sample = reader._build_sample(object())

        assert sample["sample_interval_s"] is not None
        assert sample["sample_interval_s"] >= 0.0
        # En la muestra va redondeado a 4 decimales; la propiedad lo da crudo.
        assert reader.last_interval_s == pytest.approx(
            sample["sample_interval_s"], abs=1e-4
        )
