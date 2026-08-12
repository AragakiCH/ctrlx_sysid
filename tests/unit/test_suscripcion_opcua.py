"""
Muestreo por suscripción: agrupación, ritmo concedido y respaldo a polling.

Con *polling* cada muestra cuesta un viaje de ida y vuelta, así que el periodo
nunca baja de la latencia de red: pedir 20 ms sobre un enlace de 64 ms da 64 ms.
Con una suscripción el servidor muestrea a su ritmo y envía lotes, y ahí sí se
puede llegar a 10-20 ms.

El precio es que las notificaciones llegan por variable y hay que reconstruir
las muestras sincronizadas.
"""

import pytest

from infrastructure.ctrlx.opcua_subscription import (
    OpcUaSampler,
    SubscriptionNotSupported,
    _Agrupador,
)


# --------------------------------------------------------------------------- #
# Agrupación por instante
# --------------------------------------------------------------------------- #


@pytest.fixture
def recogidas():
    return []


@pytest.fixture
def agrupador(recogidas):
    return _Agrupador(
        nombres=["rActuator", "rSensor", "rSetPoint"],
        bucket_s=0.01,
        on_sample=recogidas.append,
    )


def test_reconstruye_una_muestra_por_ciclo(agrupador, recogidas):
    for i, t in enumerate([100.000, 100.010, 100.020]):
        agrupador.agregar("rActuator", 8.0 + i, t)
        agrupador.agregar("rSensor", 20.0 + i, t)
        agrupador.agregar("rSetPoint", 12.0, t)

    agrupador.vaciar()

    assert len(recogidas) == 3
    assert recogidas[0]["raw"] == {"rActuator": 8.0, "rSensor": 20.0, "rSetPoint": 12.0}
    assert recogidas[2]["raw"]["rSensor"] == 22.0


def test_una_variable_que_no_cambia_conserva_su_valor(recogidas):
    """
    El servidor solo reporta cambios: una variable estable deja de notificar.
    Sin memoria del último valor, las muestras saldrían con huecos.
    """
    g = _Agrupador(["a", "b"], bucket_s=0.01, on_sample=recogidas.append)

    g.agregar("a", 1.0, 200.000)
    g.agregar("b", 9.0, 200.000)
    g.agregar("a", 2.0, 200.010)   # 'b' no cambió
    g.agregar("a", 3.0, 200.020)
    g.vaciar()

    assert [m["raw"]["b"] for m in recogidas] == [9.0, 9.0, 9.0]


def test_las_muestras_salen_en_orden(agrupador, recogidas):
    for t in [100.000, 100.010, 100.020, 100.030]:
        agrupador.agregar("rActuator", 1.0, t)
    agrupador.vaciar()

    instantes = [m["timestamp"] for m in recogidas]
    assert instantes == sorted(instantes)


def test_una_notificacion_tardia_no_reabre_un_grupo_ya_emitido(agrupador, recogidas):
    """Reabrirlo emitiría dos muestras con el mismo instante."""
    agrupador.agregar("rActuator", 1.0, 100.000)
    agrupador.agregar("rActuator", 2.0, 100.010)   # emite el grupo de 100.000
    agrupador.agregar("rSensor", 99.0, 100.000)    # llega tarde
    agrupador.vaciar()

    instantes = [m["timestamp"] for m in recogidas]
    assert len(instantes) == len(set(instantes))


def test_sin_timestamp_se_usa_el_reloj_local(recogidas):
    """Algunos servidores no rellenan SourceTimestamp."""
    g = _Agrupador(["a"], bucket_s=0.01, on_sample=recogidas.append)

    g.agregar("a", 1.0, None)
    g.vaciar()

    assert len(recogidas) == 1
    assert recogidas[0]["timestamp"] > 0


def test_no_acumula_grupos_sin_limite(recogidas):
    """
    Con timestamps desordenados o una variable muda, los grupos pendientes
    crecerían sin fin y el proceso se quedaría sin memoria.
    """
    g = _Agrupador(["a", "b"], bucket_s=0.01, on_sample=recogidas.append, max_pendientes=10)

    for i in range(500):
        g.agregar("a", float(i), 300.000 + i * 0.01)

    assert len(g._grupos) <= 10
    assert len(recogidas) >= 490


def test_un_fallo_al_entregar_no_tumba_el_agrupador():
    """El hilo de la suscripción no puede morir por un error aguas abajo."""

    def revienta(muestra):
        raise RuntimeError("el consumidor falló")

    g = _Agrupador(["a"], bucket_s=0.01, on_sample=revienta)

    g.agregar("a", 1.0, 100.000)
    g.agregar("a", 2.0, 100.010)   # dispara la emisión del anterior
    g.vaciar()                     # no debe propagar


# --------------------------------------------------------------------------- #
# Arranque de la suscripción
# --------------------------------------------------------------------------- #


class NodoFalso:
    def __init__(self, nombre):
        self.nodeid = type("N", (), {"to_string": lambda s: f"ns=2;s={nombre}"})()


class ClienteFalso:
    """Servidor que acepta la suscripción y concede el intervalo pedido."""

    def __init__(self, revised_ms=None, falla=False):
        self.revised_ms = revised_ms
        self.falla = falla
        self.creada = None

    def create_subscription(self, period_ms, handler):
        if self.falla:
            raise RuntimeError("BadTooManySubscriptions")

        cliente = self

        class Sub:
            _client_handle = 0
            parameters = type(
                "P",
                (),
                {
                    "RevisedPublishingInterval": cliente.revised_ms or period_ms,
                    "RequestedPublishingInterval": period_ms,
                },
            )()

            def create_monitored_items(self, items):
                cliente.creada = items
                return [object() for _ in items]

            def delete(self):
                pass

        return Sub()


def test_devuelve_el_periodo_concedido():
    nodos = {"a": NodoFalso("a"), "b": NodoFalso("b")}
    sampler = OpcUaSampler(ClienteFalso(), nodos)

    concedido = sampler.start(0.02, lambda m: None)

    assert concedido == pytest.approx(0.02, abs=1e-6)
    assert sampler.is_active


def test_reporta_el_periodo_mayor_que_el_servidor_impone():
    """El ctrlX no está obligado a aceptar 10 ms."""
    # Pedimos 10 ms -> publishing 100 ms. El servidor concede 250 ms.
    sampler = OpcUaSampler(ClienteFalso(revised_ms=250.0), {"a": NodoFalso("a")})

    concedido = sampler.start(0.01, lambda m: None)

    assert concedido == pytest.approx(0.025, abs=1e-6)
    assert sampler.requested_period_s == pytest.approx(0.01)
    assert sampler.revised_period_s == pytest.approx(0.025)


def test_el_bucket_se_ajusta_al_periodo_concedido():
    """
    Si el bucket se quedara en el pedido y el servidor concede uno mayor, cada
    ciclo del PLC caería en un bucket distinto y las variables no se juntarían
    nunca en la misma muestra.
    """
    sampler = OpcUaSampler(ClienteFalso(revised_ms=250.0), {"a": NodoFalso("a")})
    sampler.start(0.01, lambda m: None)

    assert sampler._agrupador._bucket_s == pytest.approx(0.025)


def test_muestrea_mas_rapido_de_lo_que_publica():
    """
    Es el punto entero del cambio: `subscribe_data_change` iguala sampling y
    publishing, y así el ritmo seguiría atado a la red.
    """
    cliente = ClienteFalso()
    OpcUaSampler(cliente, {"a": NodoFalso("a")}).start(0.02, lambda m: None)

    params = cliente.creada[0].RequestedParameters

    assert params.SamplingInterval == pytest.approx(20.0)
    assert params.QueueSize >= 10   # cabe un lote entero sin descartar


def test_si_el_servidor_rechaza_lanza_para_caer_a_polling():
    sampler = OpcUaSampler(ClienteFalso(falla=True), {"a": NodoFalso("a")})

    with pytest.raises(SubscriptionNotSupported):
        sampler.start(0.02, lambda m: None)


def test_sin_nodos_no_tiene_sentido_suscribir():
    with pytest.raises(SubscriptionNotSupported, match="nodos"):
        OpcUaSampler(ClienteFalso(), {}).start(0.02, lambda m: None)


def test_stop_es_idempotente():
    sampler = OpcUaSampler(ClienteFalso(), {"a": NodoFalso("a")})
    sampler.start(0.02, lambda m: None)

    sampler.stop()
    sampler.stop()

    assert not sampler.is_active


def test_stop_entrega_lo_que_quedaba_pendiente(recogidas):
    sampler = OpcUaSampler(ClienteFalso(), {"a": NodoFalso("a")})
    sampler.start(0.02, recogidas.append)

    sampler._agrupador.agregar("a", 5.0, 500.0)
    sampler.stop()

    assert len(recogidas) == 1
    assert recogidas[0]["raw"]["a"] == 5.0


# --------------------------------------------------------------------------- #
# Cambiar el periodo con la suscripción abierta
# --------------------------------------------------------------------------- #


class ReaderFalso:
    """
    Solo la parte de PLCReader que gobierna el ciclo de la suscripción.

    Se arma con `__new__` a propósito: instanciar un PLCReader de verdad exige
    URL, credenciales y un cliente OPC UA que aquí no aportan nada.
    """

    @staticmethod
    def crear(period_s=0.1):
        import time as _t

        from infrastructure.ctrlx.plc_reader import PLCReader

        r = PLCReader.__new__(PLCReader)
        r._stop = False
        r.period_s = period_s
        r._requested_period_s = period_s
        r._revised_period_s = period_s
        r._last_subscription_sample = _t.monotonic()
        r._subscription_delivered = True
        r._subscription_disabled = False
        r._subscription_error = None
        r._sampler = None
        return r


def test_cambiar_el_periodo_reabre_la_suscripcion():
    """
    Una suscripción se negocia con un intervalo fijo al abrirla. El polling lee
    `period_s` en cada vuelta, pero aquí no: sin reabrir, mover el campo de la
    vista de 100 ms a 20 ms no cambiaría nada y el usuario vería el ritmo viejo.
    """
    import threading
    import time as _t

    reader = ReaderFalso.crear(0.1)
    aperturas = []

    def abrir(_node):
        aperturas.append(reader.period_s)
        reader._requested_period_s = reader.period_s
        reader._revised_period_s = reader.period_s
        reader._last_subscription_sample = _t.monotonic()
        reader._sampler = type("S", (), {"is_active": True, "stop": lambda s: None})()
        return True

    reader._try_subscription = abrir

    hilo = threading.Thread(target=lambda: reader.muestrear_por_suscripcion(None), daemon=True)
    hilo.start()
    _t.sleep(0.4)

    reader.period_s = 0.02
    _t.sleep(0.5)

    reader._stop = True
    hilo.join(2)

    assert aperturas == [0.1, 0.02]


def test_si_al_reabrir_ya_no_se_puede_suscribir_se_cae_a_polling():
    import threading
    import time as _t

    reader = ReaderFalso.crear(0.1)
    resultado = {}
    intentos = []

    def abrir(_node):
        intentos.append(reader.period_s)
        if len(intentos) > 1:
            return False   # el servidor ya no la acepta
        reader._requested_period_s = reader.period_s
        reader._last_subscription_sample = _t.monotonic()
        reader._sampler = type("S", (), {"is_active": True, "stop": lambda s: None})()
        return True

    reader._try_subscription = abrir

    def correr():
        resultado["ok"] = reader.muestrear_por_suscripcion(None)

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    _t.sleep(0.4)

    reader.period_s = 0.02
    hilo.join(2)

    assert resultado["ok"] is False   # el bucle exterior seguirá por polling


# --------------------------------------------------------------------------- #
# El eje de tiempo sale del reloj del servidor, no del de llegada
# --------------------------------------------------------------------------- #


def _reader_de_suscripcion():
    """PLCReader reducido a lo que hace falta para componer muestras."""
    from infrastructure.ctrlx.plc_reader import PLCReader

    r = PLCReader.__new__(PLCReader)
    r._clock_start = None
    r._last_sample_monotonic = None
    r._last_interval_s = None
    r._last_read_duration_s = None
    r._last_subscription_sample = None
    r._sub_time_offset = None
    r._sampling_mode = "subscription"
    r.include_raw = False
    r._value_for_role = lambda raw, role, mapa: raw.get(mapa.get(role))
    return r


def test_el_eje_de_tiempo_respeta_el_espaciado_del_servidor():
    """
    El servidor publica en LOTES: con 60 ms de muestreo manda diez muestras
    juntas cada 600 ms. Construyendo el eje con el instante de LLEGADA, esas
    diez caen en el mismo momento y el eje sale como una escalera: diez puntos
    en t=0, salto a t=0.6, otros diez. Sobre eso la identificación lee mal el
    tiempo muerto y la constante de tiempo.
    """
    import time as _t

    reader = _reader_de_suscripcion()
    muestras = []
    reader.on_sample = muestras.append

    mapa = {"sensor": "y"}
    g = _Agrupador(
        ["y"],
        bucket_s=0.06,
        on_sample=lambda p: reader._on_subscription_sample(p, ["y"], mapa),
    )

    base = 1754212800.0
    for lote in range(3):
        for i in range(10):
            n = lote * 10 + i
            g.agregar("y", 8.0 + n * 0.01, base + n * 0.06)
        _t.sleep(0.15)   # el hueco hasta el siguiente PublishingInterval
    g.vaciar()

    eje = [m["time"] for m in muestras]
    deltas = {round(eje[i + 1] - eje[i], 4) for i in range(len(eje) - 1)}

    assert len(muestras) == 30
    assert deltas == {0.06}


def test_sin_timestamp_del_servidor_se_usa_el_reloj_local():
    """Si el servidor no rellena SourceTimestamp, algo hay que poner."""
    reader = _reader_de_suscripcion()
    muestras = []
    reader.on_sample = muestras.append

    reader._on_subscription_sample({"raw": {"y": 1.0}, "timestamp": None}, ["y"], {"sensor": "y"})

    assert len(muestras) == 1
    assert muestras[0]["time"] == 0.0


# --------------------------------------------------------------------------- #
# Una suscripción que nunca entrega no puede dejar la app sin datos
# --------------------------------------------------------------------------- #


class _SubFalso:
    def __init__(self, has_unknown_handlers=False):
        self.is_active = True
        self.has_unknown_handlers = has_unknown_handlers
        self._subscription = self

    def stop(self):
        self.is_active = False


def _reader_vigilando(period_s=0.02, entregada=False, unknown=False):
    import time as _t

    from infrastructure.ctrlx.plc_reader import PLCReader

    r = PLCReader.__new__(PLCReader)
    r._stop = False
    r.period_s = period_s
    r._requested_period_s = period_s
    r._revised_period_s = period_s
    r._last_subscription_sample = _t.monotonic()
    r._subscription_delivered = entregada
    r._subscription_disabled = False
    r._subscription_error = None
    r._sampling_mode = "subscription"
    r._sampler = _SubFalso(has_unknown_handlers=unknown)
    return r


def test_items_rechazados_por_el_servidor_lanzan():
    """
    `create_monitored_items` devuelve un StatusCode cuando el servidor rechaza
    el item. Sin mirarlo, la suscripción queda "activa" y vacía, y el síntoma
    es un bucle de reconexión que parece un problema de red.
    """
    peticion = type("P", (), {"ItemToMonitor": type("I", (), {"NodeId": type("N", (), {"to_string": lambda s: "ns=2;s=x"})()})()})()
    malo = type("SC", (), {"is_good": lambda s: False, "__str__": lambda s: "BadNodeIdUnknown"})()

    with pytest.raises(SubscriptionNotSupported, match="rechazó todos"):
        OpcUaSampler._verificar_items([peticion], [malo])


def test_items_aceptados_devuelven_sus_ids():
    peticion = type("P", (), {"ItemToMonitor": type("I", (), {"NodeId": type("N", (), {"to_string": lambda s: "ns=2;s=x"})()})()})()

    assert OpcUaSampler._verificar_items([peticion, peticion], [11, 22]) == [11, 22]


def test_una_suscripcion_que_nunca_publica_se_declara_muda():
    """
    Distinto de quedarse muda a mitad: reconectar repetiría el mismo resultado
    y la aplicación no recibiría ni una muestra. Hay que caer a polling.
    """
    import threading
    import time as _t

    reader = _reader_vigilando(period_s=0.02, entregada=False)
    reader._last_subscription_sample = _t.monotonic() - 100  # ya lleva callada

    resultado = {}
    hilo = threading.Thread(
        target=lambda: resultado.setdefault("motivo", reader._vigilar_suscripcion()),
        daemon=True,
    )
    hilo.start()
    hilo.join(2)

    assert resultado["motivo"] == "muda"
    assert "no publicó" in reader._subscription_error


def test_quedarse_muda_a_mitad_si_lanza_para_reconectar():
    """Ahí el problema sí es la sesión, y reconectar lo arregla."""
    import time as _t

    reader = _reader_vigilando(period_s=0.02, entregada=True)
    reader._last_subscription_sample = _t.monotonic() - 100

    with pytest.raises(RuntimeError, match="dejó de entregar"):
        reader._vigilar_suscripcion()


def test_una_suscripcion_muda_apaga_las_suscripciones_y_pide_polling():
    reader = _reader_vigilando()
    reader._try_subscription = lambda node: True
    reader._vigilar_suscripcion = lambda: "muda"
    reader._subscription_error = "no publicó nada"

    assert reader.muestrear_por_suscripcion(None) is False
    assert reader._subscription_disabled is True
    assert reader._sampling_mode == "polling"


def test_apagadas_no_se_vuelven_a_intentar():
    """Reintentarlas en cada reconexión es el bucle que deja la app vacía."""
    reader = _reader_vigilando()
    reader._subscription_disabled = True
    intentos = []
    reader._try_subscription = lambda node: intentos.append(1) or True

    assert reader.muestrear_por_suscripcion(None) is False
    assert intentos == []


def test_distingue_al_servidor_que_publica_con_handles_desconocidos():
    """Separa 'el servidor no publica' de 'publica y no sabemos encaminarlo'."""
    reader = _reader_vigilando(unknown=True)

    assert "no reconoce" in reader._pista_de_suscripcion_muda()


# --------------------------------------------------------------------------- #
# `_try_subscription` de punta a punta
# --------------------------------------------------------------------------- #
#
# Las pruebas de arriba ejercitan las piezas por separado: el agrupador, el
# sampler, el vigilante. Ninguna recorría `_try_subscription` entero, y por ese
# hueco se coló un `self._handles` (atributo del sampler, no del reader) que
# reventaba en la línea de diagnóstico DESPUÉS de que la suscripción ya estaba
# montada. La excepción salía al bucle exterior, que reconectaba, y la
# aplicación se quedaba sin una sola muestra repitiendo "OPC UA FAIL".


class _OpcParaSuscripcion:
    """Cliente OPC UA mínimo: entrega nodos y el cliente para el sampler."""

    def __init__(self, cliente):
        self.client = cliente

    def value_node(self, node):
        return node


def _reader_para_try(nombres=("rActuator", "rSensor"), cliente=None):
    import threading

    from infrastructure.ctrlx.plc_reader import PLCReader

    r = PLCReader.__new__(PLCReader)
    r._io_lock = threading.RLock()
    r.period_s = 0.02
    r.mapping = {"actuator": nombres[0], "sensor": nombres[-1]}
    r._variable_names = list(nombres)
    r._sampler = None
    r._sampling_mode = "polling"
    r._revised_period_s = None
    r._requested_period_s = None
    r._subscription_error = None
    r._subscription_delivered = False
    r._subscription_disabled = False
    r._refresh_catalog_locked = lambda node: None
    r._resolve_node = lambda nombre: NodoFalso(nombre) if nombre in nombres else None
    r._opc = _OpcParaSuscripcion(cliente or ClienteFalso())
    return r


def test_try_subscription_deja_el_lector_en_modo_suscripcion():
    reader = _reader_para_try()

    assert reader._try_subscription(object()) is True
    assert reader._sampling_mode == "subscription"
    assert reader._sampler is not None
    assert reader._revised_period_s == pytest.approx(0.02)


def test_try_subscription_no_revienta_al_informar_del_resultado():
    """
    El fallo original no estaba en montar la suscripción sino en la línea que
    la reporta, ya con todo listo. Se veía como un error de conexión.
    """
    reader = _reader_para_try()

    reader._try_subscription(object())   # no debe lanzar AttributeError


def test_try_subscription_cae_a_polling_si_el_servidor_no_quiere():
    reader = _reader_para_try(cliente=ClienteFalso(falla=True))

    assert reader._try_subscription(object()) is False
    assert reader._sampling_mode == "polling"
    assert "no aceptó" in reader._subscription_error


def test_try_subscription_sin_variables_mapeadas_cae_a_polling():
    """
    Nombres que no coinciden con ningún alias: sin mapeo explícito no hay nada
    que suscribir. Con nombres tipo `rActuator` el alias los encontraría solo.
    """
    reader = _reader_para_try(nombres=("Temperatura_Horno", "Valvula_A"))
    reader.mapping = {"actuator": None, "sensor": None}

    assert reader._try_subscription(object()) is False
    assert "Ninguna variable" in reader._subscription_error


def test_reabrir_no_deja_la_suscripcion_anterior_colgada():
    """Sin cerrarla, quedaría publicando contra un agrupador que nadie lee."""
    reader = _reader_para_try()
    reader._try_subscription(object())
    primero = reader._sampler

    reader.period_s = 0.05
    reader._try_subscription(object())

    assert primero.is_active is False
    assert reader._sampler is not primero


def test_un_error_de_codigo_en_la_ruta_de_suscripcion_no_impide_el_polling():
    """
    Es exactamente lo que pasó: un atributo mal escrito en la línea que informa
    del resultado, con la suscripción ya montada. La excepción salía al bucle
    exterior, que la trataba como caída de red y reconectaba en bucle: la app
    se quedaba sin una sola muestra y el log culpaba al PLC.

    Nada de esta ruta puede impedir que se lea por polling.
    """
    import infrastructure.ctrlx.plc_reader as modulo

    reader = _reader_para_try()

    class SamplerQueRevienta:
        """Se monta bien y falla al informar, igual que el fallo real."""

        def __init__(self, *a, **k):
            self.is_active = True

        def start(self, period, cb):
            return period

        @property
        def monitored_count(self):
            raise AttributeError("_handles")

        def stop(self):
            self.is_active = False

    anterior = modulo.OpcUaSampler
    modulo.OpcUaSampler = SamplerQueRevienta
    try:
        assert reader._try_subscription(object()) is False
    finally:
        modulo.OpcUaSampler = anterior

    assert reader._sampling_mode == "polling"
    assert reader._sampler is None
    assert "AttributeError" in reader._subscription_error
