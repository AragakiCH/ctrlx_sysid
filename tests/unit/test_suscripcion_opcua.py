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
    # Cabe un lote entero sin descartar: publishing (100 ms) / sampling (20 ms).
    assert params.QueueSize >= 5


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


def test_cambiar_el_mapeo_reabre_la_suscripcion():
    """
    El mapeo rol -> variable también se fija al abrir la suscripción: los nodos
    monitorizados y la etiqueta `mapping` de cada muestra salen de un closure.
    Sin reabrir, cambiar el set point desde la vista no hacía nada y el
    desplegable volvía solo a la variable anterior con la siguiente muestra.
    """
    import threading
    import time as _t

    reader = ReaderFalso.crear(0.1)
    aperturas = []

    def abrir(_node):
        aperturas.append(dict(reader.mapping))
        reader._requested_period_s = reader.period_s
        reader._revised_period_s = reader.period_s
        reader._last_subscription_sample = _t.monotonic()
        reader._subscribed_mapping_version = reader._mapping_version
        reader._sampler = type("S", (), {"is_active": True, "stop": lambda s: None})()
        return True

    reader._try_subscription = abrir
    reader.mapping = {"setpoint": "HMI_SP_Local_Automatico"}
    reader._node_cache = {}
    import threading as _th
    reader._io_lock = _th.RLock()

    hilo = threading.Thread(target=lambda: reader.muestrear_por_suscripcion(None), daemon=True)
    hilo.start()
    _t.sleep(0.4)

    reader.set_mapping({"setpoint": "Velocidad_Pct"})
    _t.sleep(0.5)

    reader._stop = True
    hilo.join(2)

    assert [a.get("setpoint") for a in aperturas] == ["HMI_SP_Local_Automatico", "Velocidad_Pct"]


def test_una_suscripcion_desfasada_no_entrega_muestras_con_el_mapeo_viejo():
    """
    Entre `set_mapping` y la reapertura pasan hasta 200 ms. Las muestras que la
    suscripción vieja entregue en ese hueco llevan el mapeo anterior y harían
    que la vista deshiciera el cambio del usuario.
    """
    import threading as _th
    import time as _t

    from infrastructure.ctrlx.plc_reader import PLCReader

    reader = PLCReader.__new__(PLCReader)
    reader._stop = False
    reader.period_s = 0.1
    reader.mapping = {"setpoint": "HMI_SP_Local_Automatico"}
    reader._node_cache = {}
    reader._io_lock = _th.RLock()
    reader._sampler = None
    reader._variable_names = ["HMI_SP_Local_Automatico", "Velocidad_Pct"]
    reader._catalog_ts = _t.monotonic()
    reader._refresh_catalog_locked = lambda node: None
    reader._resolve_node = lambda nombre: object()
    reader._opc = type("O", (), {"value_node": lambda s, n: n, "client": None})()

    entregadas = []
    reader._on_subscription_sample = lambda parcial, catalog, mapping: entregadas.append(mapping)

    capturado = {}

    class SamplerFalso:
        monitored_count = 1
        def __init__(self, client, nodos): pass
        def start(self, period, entregar):
            capturado["entregar"] = entregar
            return period
        def stop(self): pass

    import infrastructure.ctrlx.plc_reader as mod
    original = mod.OpcUaSampler
    mod.OpcUaSampler = SamplerFalso
    try:
        assert reader._try_subscription(None) is True
    finally:
        mod.OpcUaSampler = original

    entregar = capturado["entregar"]

    entregar({"raw": {}, "timestamp": None})
    assert len(entregadas) == 1
    assert entregadas[0]["setpoint"] == "HMI_SP_Local_Automatico"

    reader.set_mapping({"setpoint": "Velocidad_Pct"})

    entregar({"raw": {}, "timestamp": None})   # llega de la suscripción vieja
    assert len(entregadas) == 1, "una muestra con el mapeo viejo no debe entregarse"


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
        self.ultimos_valores = {}

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


def test_el_silencio_ya_no_mata_una_suscripcion_que_funciona():
    """
    Cambio deliberado. Antes, tras N segundos sin notificaciones se lanzaba
    para reconectar. Pero una suscripción OPC UA solo notifica CAMBIOS, así que
    un proceso en régimen permanente calla legítimamente — justo lo que pasa en
    la línea base antes del escalón. Matarla ahí era el motivo de que la
    suscripción se "cortara" sola en cada ensayo.

    Ahora el silencio se tolera y lo que se comprueba es que los valores no
    estén desfasados, preguntándole al PLC.
    """
    import threading
    import time as _t

    reader = _reader_vigilando(period_s=0.02, entregada=True)
    reader._last_subscription_sample = _t.monotonic() - 100
    reader._suscripcion_al_dia = lambda: True

    fallo = {}
    hilo = threading.Thread(
        target=lambda: fallo.setdefault("exc", _capturar(reader)), daemon=True
    )
    hilo.start()
    hilo.join(1.5)

    assert fallo.get("exc") is None


def _capturar(reader):
    try:
        reader._vigilar_suscripcion()
    except Exception as exc:      # pragma: no cover - solo si el test falla
        return exc
    return None


def test_una_suscripcion_desfasada_si_lanza_para_reconectar():
    """
    El caso que el silencio ya no cubre: la suscripción está viva pero
    entregando valores viejos. Desde fuera se ve igual que un proceso quieto
    —valores que no cambian—, así que solo una lectura directa los distingue.
    """
    reader = _reader_vigilando(period_s=0.02, entregada=True)
    reader.VERIFY_EVERY_S = 0.0            # verificar en la primera vuelta
    reader._suscripcion_al_dia = lambda: False

    with pytest.raises(RuntimeError, match="desfasada"):
        reader._vigilar_suscripcion()


def test_al_dia_compara_la_cache_contra_el_plc():
    reader = _reader_vigilando()
    reader._sampler.ultimos_valores = {"y": 8.085}

    reader.read_variable_value = lambda nombre: 8.085
    assert reader._suscripcion_al_dia() is True

    reader.read_variable_value = lambda nombre: 12.4
    assert reader._suscripcion_al_dia() is False


def test_una_lectura_fallida_no_condena_a_la_suscripcion():
    """Un fallo puntual de lectura no prueba que la suscripción esté rota."""
    reader = _reader_vigilando()
    reader._sampler.ultimos_valores = {"y": 8.085}
    reader.read_variable_value = lambda nombre: None

    assert reader._suscripcion_al_dia() is True


def test_sin_cache_todavia_no_hay_nada_que_comparar():
    reader = _reader_vigilando()
    reader._sampler.ultimos_valores = {}

    assert reader._suscripcion_al_dia() is True


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


# --------------------------------------------------------------------------- #
# Latido: la señal no puede cortarse porque el proceso esté quieto
# --------------------------------------------------------------------------- #
#
# Una suscripción OPC UA solo notifica CAMBIOS. Un proceso en régimen
# permanente no genera ni una notificación, y eso es exactamente lo que pasa en
# la línea base ANTES del escalón: la parte que fija el valor inicial y el
# umbral de detección. Sin latido la señal se corta justo ahí y desde fuera
# parece que la suscripción se cayó.


def test_sin_ningun_valor_no_se_inventa_nada(recogidas):
    """Repetir un valor exige tener uno. Sin datos no hay nada que repetir."""
    g = _Agrupador(["a"], bucket_s=0.01, on_sample=recogidas.append)

    g.latido()

    assert recogidas == []


def test_el_latido_repite_el_ultimo_valor_conocido(recogidas):
    import time as _t

    g = _Agrupador(["a", "b"], bucket_s=0.01, on_sample=recogidas.append)
    g.agregar("a", 7.5, 500.000)
    g.agregar("b", 2.5, 500.000)

    _t.sleep(0.05)      # el proceso queda quieto: el servidor no manda nada
    g.latido()

    assert len(recogidas) >= 1
    ultima = recogidas[-1]
    assert ultima["raw"] == {"a": 7.5, "b": 2.5}
    assert ultima["heartbeat"] is True


def test_las_muestras_reales_no_se_marcan_como_latido(agrupador, recogidas):
    agrupador.agregar("rActuator", 1.0, 100.000)
    agrupador.agregar("rActuator", 2.0, 100.010)
    agrupador.vaciar()

    assert recogidas[0]["heartbeat"] is False
    assert recogidas[1]["heartbeat"] is False


def test_el_latido_no_pisa_un_ciclo_ya_emitido_con_datos_reales(recogidas):
    """Si el servidor sí está publicando, el latido tiene que apartarse."""
    g = _Agrupador(["a"], bucket_s=0.5, on_sample=recogidas.append)
    g.agregar("a", 1.0, 900.0)
    g.agregar("a", 2.0, 900.5)      # emite el ciclo 0

    antes = len(recogidas)
    g.latido()                       # el ciclo actual es el 0, ya emitido

    assert len(recogidas) == antes


def test_el_latido_avanza_el_eje_de_tiempo(recogidas):
    """Repetir el valor sin avanzar el tiempo daría una muestra encima de otra."""
    import time as _t

    g = _Agrupador(["a"], bucket_s=0.01, on_sample=recogidas.append)
    g.agregar("a", 1.0, 700.000)

    _t.sleep(0.05)
    g.latido()
    _t.sleep(0.05)
    g.latido()

    instantes = [m["timestamp"] for m in recogidas]
    assert instantes == sorted(instantes)
    assert len(set(instantes)) == len(instantes)


def test_el_sampler_late_solo_mientras_esta_activo():
    """Un hilo suelto seguiría empujando muestras tras cerrar la sesión."""
    import time as _t

    recogidas = []
    sampler = OpcUaSampler(ClienteFalso(), {"a": NodoFalso("a")})
    sampler.start(0.02, recogidas.append)
    sampler._agrupador.agregar("a", 3.0, 800.0)

    _t.sleep(0.25)
    durante = len(recogidas)

    sampler.stop()
    _t.sleep(0.2)

    assert durante > 1                    # latió mientras estaba activo
    assert len(recogidas) == durante      # y dejó de latir al cerrarse


def test_un_proceso_quieto_sigue_dando_muestras_al_ritmo_pedido():
    """La prueba de fondo: régimen permanente y la señal no se corta."""
    import time as _t

    recogidas = []
    sampler = OpcUaSampler(ClienteFalso(), {"y": NodoFalso("y")})
    sampler.start(0.02, recogidas.append)

    sampler._agrupador.agregar("y", 8.085, 1000.0)   # único valor del servidor
    _t.sleep(0.8)
    sampler.stop()

    # Las muestras salen con la ventana de seguridad (~170 ms aquí), así que
    # en 0.8 s se emiten ~30; se exige holgadamente menos.
    assert len(recogidas) >= 15
    assert all(m["raw"]["y"] == 8.085 for m in recogidas)


# --------------------------------------------------------------------------- #
# Las muestras se colocan en el instante en que el PLC las tomó, no al llegar
# --------------------------------------------------------------------------- #
#
# El caso que se veía en la vista: muestreo de 500 ms, escalón escrito a los
# 40 s y la curva "Leído del PLC" subiendo a los 44. El servidor publicaba en
# lotes de 10 ciclos (5 s), el latido iba un solo ciclo por detrás y cuando el
# lote llegaba sus ciclos ya habían salido con el valor viejo: se descartaban
# como tardíos y el cambio aparecía en el siguiente latido, a la hora de
# LLEGADA. Dentro del lote, además, las notificaciones vienen agrupadas por
# variable, y emitir "al llegar una posterior" perdía las de la segunda.


def _simular_lotes(bucket_s, ciclos, publishing_ciclos, valores_por_ciclo, nombres, retraso_s):
    """
    Reproduce un servidor que muestrea cada `bucket_s`, retiene las
    notificaciones y las manda todas juntas cada `publishing_ciclos`, en el
    orden real de un DataChangeNotification: por variable, y dentro de cada
    variable por instante. Devuelve (muestras, instante_de_llegada_por_clave).
    """
    import time as _t

    recogidas = []
    llegadas = {}
    g = _Agrupador(nombres, bucket_s=bucket_s, on_sample=recogidas.append, retraso_s=retraso_s)

    base = 1_754_212_800.0
    t_ini = _t.monotonic()

    lote = []
    for ciclo in range(ciclos):
        lote.append(ciclo)
        es_ultimo = ciclo == ciclos - 1
        if len(lote) == publishing_ciclos or es_ultimo:
            # el lote sale cuando el reloj real llega al final del lote
            while _t.monotonic() - t_ini < (lote[-1] + 1) * bucket_s:
                g.latido()
                _t.sleep(bucket_s / 4)
            for nombre in nombres:
                for c in lote:
                    v = valores_por_ciclo(nombre, c)
                    if v is not None:
                        g.agregar(nombre, v, base + c * bucket_s)
                        llegadas.setdefault(c, _t.monotonic())
            lote = []

    # se deja pasar la ventana de seguridad para que salga todo
    fin = _t.monotonic() + retraso_s + 3 * bucket_s
    while _t.monotonic() < fin:
        g.latido()
        _t.sleep(bucket_s / 4)

    return g, recogidas, llegadas, t_ini


def test_un_escalon_que_viaja_en_lote_se_coloca_en_su_instante_y_no_al_llegar():
    """
    El actuador salta en el ciclo 12 y la notificación no llega hasta el
    final del lote (ciclo 19). La muestra del ciclo 12 tiene que salir con el
    valor nuevo y con `monotonic` en el ciclo 12: NI en el 19, NI descartada.
    """
    bucket = 0.02
    publishing = 5   # ciclos por lote -> 100 ms

    def valores(nombre, c):
        if nombre == "u":
            return 25.0 if c < 12 else 50.0
        return 8.0 + 0.1 * c          # el sensor cambia cada ciclo

    g, muestras, llegadas, t_ini = _simular_lotes(
        bucket, ciclos=25, publishing_ciclos=publishing,
        valores_por_ciclo=valores, nombres=["u", "y"],
        retraso_s=publishing * bucket + bucket + 0.05,
    )

    assert g.tardias == 0, "con la ventana bien dimensionada nada llega tarde"

    por_u = [m["raw"]["u"] for m in muestras]
    assert 25.0 in por_u and 50.0 in por_u

    primera_alta = next(i for i, m in enumerate(muestras) if m["raw"]["u"] == 50.0)
    # todas las anteriores a la subida son 25, todas las posteriores 50:
    assert all(v == 25.0 for v in por_u[:primera_alta])
    assert all(v == 50.0 for v in por_u[primera_alta:])

    # y la subida queda en el ciclo 12 según el reloj local estimado, con
    # margen de un ciclo (redondeo del ancla), no en el ciclo 19 de llegada.
    salto = muestras[primera_alta]["monotonic"] - t_ini
    assert abs(salto - 12 * bucket) <= 1.5 * bucket, salto
    assert salto < llegadas[12] - t_ini - 2 * bucket, "se ubicó a la hora de llegada"


def test_el_transitorio_dentro_de_un_lote_no_se_colapsa(recogidas):
    """
    Antes, todas las notificaciones de un lote se descartaban menos la última
    y el sensor daba un salto único. Cada ciclo del lote tiene que salir con
    SU valor, y en orden.
    """
    bucket = 0.02

    def valores(nombre, c):
        return float(c)

    g, muestras, _, _ = _simular_lotes(
        bucket, ciclos=20, publishing_ciclos=5,
        valores_por_ciclo=valores, nombres=["y"],
        retraso_s=5 * bucket + bucket + 0.05,
    )

    reales = [m for m in muestras if not m["heartbeat"]]
    assert [m["raw"]["y"] for m in reales] == [float(c) for c in range(20)]
    assert g.tardias == 0


def test_las_notificaciones_agrupadas_por_variable_se_reconstruyen_por_instante(recogidas):
    """
    Un DataChangeNotification trae primero TODAS las de una variable y luego
    todas las de la otra: u(5), u(6), u(7), y(5), y(6), y(7). Emitir el ciclo
    5 al ver llegar u(6) dejaba a y(5) y y(6) fuera. Cada ciclo tiene que
    salir con las dos variables de ese ciclo.
    """
    g = _Agrupador(["u", "y"], bucket_s=0.01, on_sample=recogidas.append, retraso_s=1.0)

    base = 500.0
    for c in (5, 6, 7):
        g.agregar("u", 10.0 + c, base + c * 0.01)
    for c in (5, 6, 7):
        g.agregar("y", 20.0 + c, base + c * 0.01)
    g.vaciar()

    assert [m["raw"] for m in recogidas] == [
        {"u": 15.0, "y": 25.0},
        {"u": 16.0, "y": 26.0},
        {"u": 17.0, "y": 27.0},
    ]
    assert g.tardias == 0


def test_los_huecos_entre_lotes_se_rellenan_con_el_ultimo_valor_emitido(recogidas):
    """Sin huecos: el eje avanza un ciclo por muestra aunque el servidor calle."""
    g = _Agrupador(["y"], bucket_s=0.01, on_sample=recogidas.append, retraso_s=0.0)

    g.agregar("y", 1.0, 700.00)
    g.agregar("y", 2.0, 700.05)     # cinco ciclos después; entre medio, nada
    g.vaciar()

    assert [m["raw"]["y"] for m in recogidas] == [1.0, 1.0, 1.0, 1.0, 1.0, 2.0]
    assert [m["heartbeat"] for m in recogidas] == [False, True, True, True, True, False]


def test_un_timestamp_inicial_viejo_no_abre_un_ciclo_en_el_pasado(recogidas):
    """
    El valor inicial de una variable que no cambia desde hace minutos llega
    con el SourceTimestamp de su última escritura. Tomado literal, abre un
    ciclo miles de posiciones atrás y descoloca el ancla del reloj.
    """
    g = _Agrupador(["u", "y"], bucket_s=0.5, on_sample=recogidas.append, retraso_s=1.0)

    g.agregar("y", 8.0, 1000.0)          # fresco
    g.agregar("u", 25.0, 1000.0 - 600)   # escrito hace 10 minutos
    g.vaciar()

    assert len(recogidas) == 1
    assert recogidas[0]["raw"] == {"u": 25.0, "y": 8.0}


def test_el_sampler_acota_el_intervalo_de_publicacion():
    """
    Con 500 ms de muestreo, publicar cada 10 periodos era retener cada cambio
    hasta 5 s. El lote no puede pasar de MAX_PUBLISHING_MS sea cual sea el
    periodo, y la ventana de emisión se dimensiona con lo concedido.
    """
    cliente = ClienteFalso()
    sampler = OpcUaSampler(cliente, {"a": NodoFalso("a")})

    concedido = sampler.start(0.5, lambda m: None)

    assert concedido == pytest.approx(0.5)
    assert sampler.publishing_period_s == pytest.approx(0.1)
    assert sampler.delay_s == pytest.approx(0.1 + 0.5 + _Agrupador.MARGEN_S)
    # la cola cubre un lote entero (aquí, una muestra) más margen
    assert cliente.creada[0].RequestedParameters.QueueSize >= 1 + OpcUaSampler.QUEUE_MARGIN


def test_con_muestreo_rapido_el_lote_sigue_siendo_de_varios_periodos():
    """A 10 ms sí compensa agrupar: 10 muestras por mensaje, como antes."""
    assert OpcUaSampler.publishing_interval_ms(10.0) == pytest.approx(100.0)
    assert OpcUaSampler.publishing_interval_ms(20.0) == pytest.approx(100.0)
    assert OpcUaSampler.publishing_interval_ms(500.0) == pytest.approx(100.0)


def test_las_muestras_traen_el_instante_local_de_captura():
    """`monotonic` es lo que alinea la señal con el reloj del ensayo."""
    import time as _t

    recogidas = []
    g = _Agrupador(["y"], bucket_s=0.01, on_sample=recogidas.append, retraso_s=0.0)

    antes = _t.monotonic()
    g.agregar("y", 1.0, 900.00)
    g.agregar("y", 2.0, 900.01)
    g.vaciar()

    assert all(isinstance(m["monotonic"], float) for m in recogidas)
    assert recogidas[1]["monotonic"] - recogidas[0]["monotonic"] == pytest.approx(0.01)
    assert abs(recogidas[0]["monotonic"] - antes) < 0.05


def test_el_reader_usa_el_instante_estimado_por_el_agrupador():
    """
    Y lo publica como `captured_monotonic`, que es lo que el runner del
    ensayo usa para etiquetar la muestra con el comando que regía entonces.
    """
    import time as _t

    reader = _reader_de_suscripcion()
    muestras = []
    reader.on_sample = muestras.append

    ahora = _t.monotonic()
    parcial = {"raw": {"y": 1.0}, "timestamp": 1000.0, "monotonic": ahora - 0.3}

    reader._on_subscription_sample(parcial, ["y"], {"sensor": "y"})

    assert muestras[0]["captured_monotonic"] == pytest.approx(ahora - 0.3)


def test_el_eje_del_reader_nunca_retrocede_aunque_el_ancla_se_afine():
    reader = _reader_de_suscripcion()
    muestras = []
    reader.on_sample = muestras.append

    reader._on_subscription_sample({"raw": {"y": 1.0}, "timestamp": 1.0, "monotonic": 100.00}, ["y"], {"sensor": "y"})
    reader._on_subscription_sample({"raw": {"y": 2.0}, "timestamp": 1.0, "monotonic": 99.95}, ["y"], {"sensor": "y"})

    assert muestras[1]["time"] > muestras[0]["time"]


# --------------------------------------------------------------------------- #
# La señal leída no puede preceder a la orden
# --------------------------------------------------------------------------- #
#
# El servidor muestrea sobre una rejilla fija. El sensor cae siempre sobre ella
# (lo escribe el PLC cada ciclo y el servidor lo muestrea cada periodo); el
# actuador se escribe por OPC UA en cualquier momento entre dos muestreos.
# Redondear su timestamp al ciclo MÁS CERCANO lo adelantaba hasta medio ciclo:
# en la vista "Leído del PLC" subía antes que "Comandado", y el modelo veía un
# tiempo muerto medio ciclo más largo de lo real.


def test_un_cambio_entre_dos_muestreos_se_asigna_al_siguiente_no_al_mas_cercano(recogidas):
    g = _Agrupador(["u", "y"], bucket_s=0.5, on_sample=recogidas.append, retraso_s=0.0)

    base = 1000.0
    for c in range(6):                       # sensor sobre la rejilla
        g.agregar("y", 8.0, base + c * 0.5)
    g.agregar("u", 50.0, base + 1.1)         # escrito 0.1 s después del ciclo 2 (t=1.0)
    g.vaciar()

    por_ciclo = {round((m["timestamp"] - base) / 0.5): m["raw"]["u"] for m in recogidas}

    assert por_ciclo[2] is None, "en el ciclo 2 el servidor aún no lo había visto"
    assert por_ciclo[3] == 50.0, "se observó en el siguiente muestreo"


def test_un_timestamp_sobre_la_rejilla_con_jitter_no_salta_de_ciclo(recogidas):
    g = _Agrupador(["y"], bucket_s=0.5, on_sample=recogidas.append, retraso_s=0.0)

    base = 1000.0
    g.agregar("y", 1.0, base)
    g.agregar("y", 2.0, base + 0.5 + 0.02)   # 4 % de jitter hacia adelante
    g.agregar("y", 3.0, base + 1.0 - 0.02)   # 4 % hacia atrás
    g.vaciar()

    assert [m["raw"]["y"] for m in recogidas] == [1.0, 2.0, 3.0]


def test_un_timestamp_fuera_de_rejilla_no_adelanta_el_ancla_local():
    """
    El ancla (instante local del ciclo 0) se estima con el timestamp exacto,
    no con el ciclo asignado: si se usara el ciclo, una escritura a mitad de
    ciclo adelantaría TODAS las muestras posteriores hasta medio ciclo.
    """
    import time as _t

    recogidas = []
    g = _Agrupador(["u", "y"], bucket_s=0.5, on_sample=recogidas.append, retraso_s=0.0)

    base = 1000.0
    t_ini = _t.monotonic()
    g.agregar("y", 8.0, base)
    ancla_sensor = g._origen_local

    _t.sleep(0.3)                             # el reloj real avanza hasta la escritura
    g.agregar("u", 50.0, base + 0.26)        # entre ciclos; ciclo asignado = 1

    assert g._origen_local >= ancla_sensor - 1e-3
    assert g._origen_local >= t_ini - 1e-3


def test_el_registro_de_notificaciones_dice_donde_cae_cada_timestamp():
    g = _Agrupador(["u", "y"], bucket_s=0.5, on_sample=lambda m: None, retraso_s=0.0)

    g.agregar("y", 8.0, 1000.0)
    g.agregar("u", 50.0, 1000.3)

    registro = g.registro
    assert [r["name"] for r in registro] == ["y", "u"]
    assert registro[0]["grid_offset"] == pytest.approx(0.0)
    assert registro[1]["cycle"] == 1
    assert -1.0 < registro[1]["grid_offset"] < 0.0
