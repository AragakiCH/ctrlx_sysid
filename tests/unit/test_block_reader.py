"""
Búfer circular del PLC: captura sin pérdida a ritmo de tarea.

El sondeo por OPC UA no puede garantizar que se capturen todas las muestras —la
tarea IEC y el lector corren con relojes independientes—. Aquí el muestreo lo
hace la propia tarea y la app lee el array en bloque; lo que se prueba es que la
reconstrucción de la serie sea exacta y que la pérdida, cuando ocurre, se
detecte en vez de pasar desapercibida.
"""

import pytest

from infrastructure.ctrlx.block_reader import BlockMapping, RingBufferReader

CAP = 10  # capacidad pequeña para forzar las vueltas


def plc(write_count, capacity=CAP, periodo=0.02, con_tiempo=True):
    """Estado del array tal como lo devolvería el servidor OPC UA."""
    actuador, sensor, setpoint, tiempo = [], [], [], []

    for i in range(capacity):
        # Cada posición guarda la última muestra absoluta que le tocó.
        vuelta = (write_count - 1 - i) // capacity
        absoluto = vuelta * capacity + i
        if absoluto < 0:
            absoluto = 0

        actuador.append(float(absoluto))
        sensor.append(float(absoluto) * 2)
        setpoint.append(50.0)
        tiempo.append(round(absoluto * periodo, 6))

    datos = {
        "arrActuator": actuador,
        "arrSensor": sensor,
        "arrSetPoint": setpoint,
        "nWriteCount": write_count,
        "rTaskPeriodSec": periodo,
    }
    if con_tiempo:
        datos["arrTimeSec"] = tiempo

    return datos


@pytest.fixture
def lector():
    return RingBufferReader(mapping=BlockMapping(), default_period_s=0.02)


class TestPrimeraPasada:
    def test_no_arrastra_lo_que_ya_estaba(self, lector):
        """Al conectarse, el array trae historia vieja que no es de este ensayo."""
        r = lector.extract(plc(write_count=57))

        assert r.samples == []
        assert r.write_count == 57
        assert r.capacity == CAP

    def test_a_partir_de_ahi_solo_lo_nuevo(self, lector):
        lector.extract(plc(write_count=57))
        r = lector.extract(plc(write_count=61))

        assert len(r.samples) == 4
        assert [s["sample_index"] for s in r.samples] == [57, 58, 59, 60]


class TestReconstruccion:
    def test_las_muestras_salen_en_orden(self, lector):
        lector.extract(plc(write_count=0))
        r = lector.extract(plc(write_count=7))

        assert [s["actuator"] for s in r.samples] == [0, 1, 2, 3, 4, 5, 6]

    def test_el_tiempo_es_monotono_entre_vueltas(self, lector):
        """
        El índice del array vuelve a cero, pero el tiempo no puede: un eje en
        diente de sierra mete dt negativos en la integración del modelo.
        """
        lector.extract(plc(write_count=0))
        r = lector.extract(plc(write_count=25))

        tiempos = [s["time"] for s in r.samples]
        assert all(tiempos[i] > tiempos[i - 1] for i in range(1, len(tiempos)))

    def test_sin_array_de_tiempo_se_deriva_del_periodo(self, lector):
        """El periodo de tarea es exacto por construcción: sirve de eje."""
        lector.extract(plc(write_count=0, con_tiempo=False))
        r = lector.extract(plc(write_count=5, con_tiempo=False))

        assert [s["time"] for s in r.samples] == [0.0, 0.02, 0.04, 0.06, 0.08]

    def test_sin_lectura_nueva_no_devuelve_nada(self, lector):
        lector.extract(plc(write_count=30))
        r = lector.extract(plc(write_count=30))

        assert r.samples == []
        assert r.overrun is False


class TestDeteccionDePerdida:
    def test_leer_a_tiempo_no_pierde_nada(self, lector):
        lector.extract(plc(write_count=0))
        r = lector.extract(plc(write_count=CAP))   # justo una vuelta

        assert r.overrun is False
        assert r.lost == 0
        assert len(r.samples) == CAP

    def test_llegar_tarde_se_detecta_y_se_cuantifica(self, lector):
        """
        Si la app tarda más de lo que el PLC tarda en dar la vuelta, hay
        muestras sobrescritas. Entregarlas con huecos en silencio produciría un
        modelo que parece válido y no lo es.
        """
        lector.extract(plc(write_count=0))
        r = lector.extract(plc(write_count=CAP * 3))

        assert r.overrun is True
        assert r.lost == CAP * 2
        assert len(r.samples) == CAP        # solo se recupera la última vuelta

    def test_tras_un_overrun_se_resincroniza(self, lector):
        lector.extract(plc(write_count=0))
        lector.extract(plc(write_count=CAP * 3))
        r = lector.extract(plc(write_count=CAP * 3 + 4))

        assert r.overrun is False
        assert len(r.samples) == 4

    def test_un_reinicio_del_plc_no_bloquea_la_captura(self, lector):
        """Si el PLC se reinicia, el contador vuelve a cero: hay que reengancharse."""
        lector.extract(plc(write_count=500))
        lector.extract(plc(write_count=520))

        r = lector.extract(plc(write_count=3))     # reinicio
        assert r.samples == []

        r = lector.extract(plc(write_count=6))
        assert len(r.samples) == 3


class TestPresupuestoDeLectura:
    def test_dice_cada_cuanto_hay_que_leer(self, lector):
        """2000 muestras a 20 ms son 40 s; se lee a la mitad para tener margen."""
        assert lector.max_poll_interval_s(2000, 0.02) == pytest.approx(20.0)

    def test_un_buffer_mas_chico_exige_leer_mas_seguido(self, lector):
        assert lector.max_poll_interval_s(200, 0.02) == pytest.approx(2.0)

    def test_sin_datos_no_inventa_un_presupuesto(self, lector):
        assert lector.max_poll_interval_s(0, 0.02) == 0.0


class TestRobustez:
    def test_array_vacio_no_revienta(self, lector):
        r = lector.extract({"arrActuator": [], "arrSensor": [], "nWriteCount": 5})
        assert r.capacity == 0
        assert r.samples == []

    def test_sin_contador_no_se_inventa_la_serie(self, lector):
        """Sin nWriteCount no hay forma de saber qué es nuevo: mejor nada."""
        datos = plc(write_count=10)
        del datos["nWriteCount"]

        r = lector.extract(datos)
        assert r.samples == []

    def test_el_setpoint_es_opcional(self, lector):
        datos = plc(write_count=0)
        del datos["arrSetPoint"]
        lector.extract(datos)

        datos = plc(write_count=3)
        del datos["arrSetPoint"]
        r = lector.extract(datos)

        assert len(r.samples) == 3
        assert all(s["setpoint"] is None for s in r.samples)

    def test_reset_vuelve_a_engancharse_desde_cero(self, lector):
        lector.extract(plc(write_count=100))
        lector.reset()

        r = lector.extract(plc(write_count=104))
        assert r.samples == []          # se re-engancha, no arrastra
