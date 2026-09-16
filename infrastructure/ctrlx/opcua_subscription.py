from __future__ import annotations

"""
Muestreo por suscripción OPC UA.

Es la única forma de bajar de ~60 ms sobre una red. Con *polling* cada muestra
cuesta un viaje completo de ida y vuelta, así que el periodo nunca puede ser
menor que la latencia: pedir 20 ms sobre un enlace de 64 ms da 64 ms.

Con una suscripción se invierte el reparto de trabajo:

* El **servidor** muestrea la variable cada `sampling_interval` (puede bajar al
  ciclo de tarea del PLC).
* Las acumula en una cola por variable (`queue_size`).
* Cada `publishing_interval` envía lo acumulado en un solo mensaje.

Así el ritmo de muestreo lo fija el PLC, no la red: las muestras llegan con sus
marcas de tiempo originales aunque viajen juntas.

Dos precios, y los dos se pagan aquí:

* Las notificaciones llegan **por variable**, no como muestras sincronizadas.
  Reconstruirlas es el trabajo de `_Agrupador`.
* Todo lo que está en el lote llega **tarde**, hasta un `publishing_interval`
  entero. Por eso el intervalo de publicación se acota (`MAX_PUBLISHING_MS`)
  y el agrupador emite con una ventana de seguridad: las muestras se colocan
  en el instante en que el PLC las tomó, nunca en el instante en que llegaron.
"""

import math
import threading
import time
from typing import Any, Callable, Optional

try:  # opcua solo hace falta para hablar con el PLC de verdad
    from opcua import ua
except ImportError:  # pragma: no cover
    ua = None


class SubscriptionNotSupported(Exception):
    """El servidor no acepta suscripciones, o no en las condiciones pedidas."""


class _Agrupador:
    """
    Reconstruye muestras sincronizadas a partir de notificaciones sueltas.

    Cada notificación trae UNA variable con su `SourceTimestamp`. Las variables
    de un mismo ciclo de tarea comparten timestamp (o caen muy cerca), así que
    se agrupan por instante redondeado al intervalo de muestreo ("ciclo").

    Cómo se emite
    -------------
    Los ciclos salen **en orden y con retraso fijo** (`retraso_s`), desde
    `latido()`, que corre en un hilo al ritmo de muestreo. Un ciclo se emite
    cuando el reloj local dice que ya pasó hace más de `retraso_s`; para
    entonces cualquier notificación suya ya tuvo tiempo de llegar, incluso la
    que el servidor retuvo un `publishing_interval` completo.

    Antes se emitía un ciclo en cuanto llegaba una notificación POSTERIOR, y el
    latido iba solo un ciclo por detrás del reloj. Con lotes de 10 ciclos eso
    era un desastre silencioso: el latido emitía los ciclos con el valor viejo
    antes de que el lote llegara, el lote entero se descartaba por "tardío" y
    el cambio aparecía en el siguiente latido, a la hora de LLEGADA. Un escalón
    escrito a los 40 s se veía a los 44. Y como dentro de un lote las
    notificaciones vienen agrupadas por variable, emitir "al llegar una
    posterior" perdía además los valores de la segunda variable del lote.

    Ciclos sin notificación (el servidor solo reporta cambios) se rellenan con
    el último valor emitido: es exactamente lo que devolvería el polling. Van
    marcados `heartbeat=True` para que quien vigile la suscripción distinga un
    dato reportado de uno repetido.

    Reloj
    -----
    Los timestamps del servidor viven en su propio reloj, que puede estar
    desfasado del local por minutos. Solo se usa su ESPACIADO: se anota el
    instante local en el que ocurrió el ciclo 0 (`_origen_local`) y se
    refina con cada notificación quedándose con la estimación más temprana,
    que es la que llegó con menos latencia. Cada muestra sale con
    `monotonic`: el instante local estimado en que el PLC la tomó, en el mismo
    dominio que `time.monotonic()` y que el reloj del ensayo.
    """

    # Margen fijo sobre el intervalo de publicación para absorber el jitter de
    # red y del hilo de recepción.
    MARGEN_S = 0.05

    # Cuántos ciclos se rellenan como máximo de golpe. Si el reloj salta (una
    # pausa larga, un timestamp inicial viejo), inundar el buffer con miles de
    # muestras repetidas sería peor que dejar el hueco.
    MAX_RELLENO = 200

    def __init__(
        self,
        nombres: list[str],
        bucket_s: float,
        on_sample: Callable[[dict], None],
        max_pendientes: int = 200,
        retraso_s: Optional[float] = None,
    ) -> None:
        self._nombres = list(nombres)
        self._bucket_s = max(bucket_s, 1e-4)
        self._on_sample = on_sample
        self._max_pendientes = max_pendientes
        # Sin dato explícito: dos ciclos, suficiente para pruebas y para un
        # servidor que publique cada ciclo. `OpcUaSampler` pasa el real.
        self._retraso_s = (
            float(retraso_s) if retraso_s is not None else 2.0 * self._bucket_s
        )

        self._lock = threading.RLock()
        self._grupos: dict[int, dict[str, Any]] = {}
        # Último valor EMITIDO de cada variable: rellena los huecos de las que
        # no cambiaron en ese ciclo, que el servidor no reporta.
        self._ultimos: dict[str, Any] = {}
        # Último valor RECIBIDO, aunque su ciclo todavía no haya salido. Es lo
        # que hay que comparar contra una lectura directa del PLC.
        self._recibidos: dict[str, Any] = {}
        self._ultima_clave_emitida: Optional[int] = None
        # Origen de tiempos del servidor. Los timestamps OPC UA son epoch
        # (~1.7e9); dividir eso por 0.01 da números enormes donde el error de
        # coma flotante ya pesa. Sobre la diferencia contra el primero los
        # valores se mantienen pequeños y el redondeo es exacto.
        self._t0: Optional[float] = None
        # Instante local (monotonic) en el que ocurrió el ciclo 0. Se refina
        # hacia atrás: la notificación que llegó con menos latencia es la que
        # mejor lo estima.
        self._origen_local: Optional[float] = None
        # Timestamp más reciente visto. Un `SourceTimestamp` mucho más viejo
        # que este es el valor inicial de una variable que no cambia desde
        # hace rato: se recoloca en el presente en vez de abrir un ciclo en el
        # pasado remoto.
        self._ts_max: Optional[float] = None
        # Diagnóstico: notificaciones que llegaron cuando su ciclo ya había
        # salido. Con el retraso bien dimensionado tiene que quedarse en cero.
        self.tardias = 0
        # Últimas notificaciones crudas, para poder ver desde fuera qué
        # timestamps manda el servidor y a qué ciclo fue cada una.
        self._registro: list[dict] = []

    REGISTRO_MAX = 40

    def _anotar(self, nombre, valor, timestamp, llegada, clave) -> None:
        """Con el lock tomado."""
        self._registro.append(
            {
                "name": nombre,
                "value": valor,
                "source_ts": round(timestamp, 4),
                "cycle": clave,
                # Cuánto después del ciclo asignado llegó (latencia + cola).
                "arrival_lag_s": round(
                    llegada - ((self._origen_local or llegada) + clave * self._bucket_s), 4
                ),
                # Dónde cae el timestamp dentro del ciclo: 0 = sobre la rejilla.
                "grid_offset": round(
                    (timestamp - (self._t0 or timestamp)) / self._bucket_s - clave, 3
                ),
            }
        )
        if len(self._registro) > self.REGISTRO_MAX:
            del self._registro[: len(self._registro) - self.REGISTRO_MAX]

    @property
    def registro(self) -> list[dict]:
        with self._lock:
            return list(self._registro)

    # ------------------------------------------------------------------ #
    # Propiedades
    # ------------------------------------------------------------------ #

    @property
    def ultimos_valores(self) -> dict:
        """Última foto RECIBIDA de cada variable (para comparar contra el PLC)."""
        with self._lock:
            return dict(self._recibidos)

    @property
    def retraso_s(self) -> float:
        return self._retraso_s

    def set_retraso(self, retraso_s: float) -> None:
        with self._lock:
            self._retraso_s = max(float(retraso_s), 0.0)

    # ------------------------------------------------------------------ #
    # Entrada
    # ------------------------------------------------------------------ #

    # Tolerancia, en fracción de ciclo, con la que un instante se considera
    # "sobre la rejilla" de muestreo. Absorbe el jitter de los timestamps del
    # servidor (y el error de coma flotante) sin adelantar nada apreciable.
    TOLERANCIA_REJILLA = 0.1

    def _clave(self, timestamp: float) -> int:
        """
        Índice del ciclo (instante de muestreo del servidor) en el que este
        valor fue observado.

        El servidor muestrea cada variable en una **rejilla** fija: cada
        `bucket_s`, con la fase que marca el primer timestamp (`_t0`). Un
        timestamp que cae sobre la rejilla es una muestra de ese ciclo. Uno que
        cae ENTRE dos instantes de la rejilla —el actuador, que se escribe por
        OPC UA en un momento cualquiera— no pudo ser observado hasta el
        **siguiente** instante de muestreo: se asigna con techo, no con
        redondeo al más cercano.

        Redondear al más cercano colocaba el actuador hasta medio ciclo ANTES
        de haberse escrito: en la vista la señal leída subía antes que la
        comandada, y el modelo veía un tiempo muerto medio ciclo más largo. Con
        techo la señal leída nunca precede a la orden y las dos variables
        quedan sobre la misma rejilla, que es exactamente lo que vería el
        polling.

        No se trunca con `int()`: un instante como 300.010 se convierte en
        300.0099999… al dividir y caería en el ciclo anterior. La tolerancia
        cubre ese error y el jitter del servidor.
        """
        if self._t0 is None:
            self._t0 = timestamp

        return int(math.ceil((timestamp - self._t0) / self._bucket_s - self.TOLERANCIA_REJILLA))

    def agregar(self, nombre: str, valor: Any, timestamp: Optional[float]) -> None:
        llegada = time.monotonic()

        with self._lock:
            if timestamp is None:
                # Sin marca del servidor solo queda el reloj local, expresado
                # en el dominio del servidor si ya se conoce el desfase.
                timestamp = self._ahora_servidor(llegada)

            if self._ts_max is not None and timestamp < self._ts_max - self._ventana_vieja():
                # Valor inicial de una variable que lleva mucho sin cambiar:
                # su SourceTimestamp es el de la ÚLTIMA escritura, que puede
                # ser de hace minutos. Se recoloca en el presente.
                timestamp = self._ts_max

            clave = self._clave(timestamp)

            if self._ts_max is None or timestamp > self._ts_max:
                self._ts_max = timestamp

            # Ancla local del ciclo 0: instante local en que el reloj del
            # servidor marcaba `_t0`. Se estima con el timestamp EXACTO (no con
            # el ciclo asignado, que puede ir hasta un ciclo por delante), así
            # que cada estimación es `verdad + latencia` y nunca anterior a la
            # verdad. La más temprana es la que llegó con menos latencia: solo
            # se mueve hacia atrás.
            origen = llegada - (timestamp - self._t0)
            if self._origen_local is None or origen < self._origen_local:
                self._origen_local = origen

            self._recibidos[nombre] = valor
            self._anotar(nombre, valor, timestamp, llegada, clave)

            if self._ultima_clave_emitida is not None and clave <= self._ultima_clave_emitida:
                # Llegó cuando su ciclo ya salió. No se reabre (dos muestras con
                # el mismo instante), pero se cuenta: si pasa, el retraso está
                # mal dimensionado.
                self.tardias += 1
                return

            self._grupos.setdefault(clave, {})[nombre] = valor

            # Salvaguarda: si el servidor manda timestamps desordenados o el
            # latido no corre, los grupos se acumularían sin fin.
            if len(self._grupos) > self._max_pendientes:
                limite = sorted(self._grupos)[len(self._grupos) - self._max_pendientes]
                self._emitir_hasta(limite - 1)

    def _ventana_vieja(self) -> float:
        """Cuánto más viejo que lo último visto tiene que ser un timestamp para tratarlo como inicial."""
        return max(2.0 * self._retraso_s, 10.0 * self._bucket_s, 1.0)

    def _ahora_servidor(self, ahora_local: float) -> float:
        """Estimación del reloj del servidor en un instante local."""
        if self._t0 is None or self._origen_local is None:
            return time.time()
        return self._t0 + (ahora_local - self._origen_local)

    # ------------------------------------------------------------------ #
    # Salida
    # ------------------------------------------------------------------ #

    def latido(self) -> None:
        """
        Emite, en orden, todos los ciclos que ya quedaron fuera de la ventana
        de seguridad. Es el ÚNICO camino de salida en operación normal.

        **Es lo que mantiene viva la señal.** Una suscripción OPC UA solo
        notifica CAMBIOS: un proceso en régimen permanente, o una variable que
        el programa PLC nunca toca, no genera ni una sola notificación. Sin
        esto la señal se corta justo donde más falta hace —la línea base plana
        ANTES del escalón— y desde fuera parece que la suscripción se cayó.
        """
        with self._lock:
            if self._t0 is None or self._origen_local is None or not self._recibidos:
                # Nunca llegó ningún valor: no hay nada que repetir. Distinto
                # de un proceso quieto, y quien llama necesita esa diferencia.
                return

            transcurrido = time.monotonic() - self._origen_local - self._retraso_s
            corte = int(math.floor(transcurrido / self._bucket_s))

            if corte < 0:
                return

            self._emitir_hasta(corte)

    def _emitir_hasta(self, corte: int) -> None:
        """Emite todos los ciclos <= corte, rellenando huecos. Con el lock tomado."""
        if self._ultima_clave_emitida is None:
            pendientes = sorted(self._grupos)
            if not pendientes:
                return
            inicio = pendientes[0]
        else:
            inicio = self._ultima_clave_emitida + 1

        if corte < inicio:
            return

        # Un salto enorme del reloj (timestamp inicial viejo, pausa larga) no
        # se rellena entero: se emiten los grupos reales que haya y solo los
        # últimos MAX_RELLENO ciclos.
        if corte - inicio + 1 > self.MAX_RELLENO:
            reales = sorted(k for k in self._grupos if k < corte - self.MAX_RELLENO + 1)
            for clave in reales:
                self._emitir(clave)
            inicio = corte - self.MAX_RELLENO + 1

        for clave in range(inicio, corte + 1):
            self._emitir(clave)

    def _emitir(self, clave: int) -> None:
        """Emite un ciclo. Con el lock tomado."""
        grupo = self._grupos.pop(clave, None)
        latido = grupo is None

        if grupo:
            self._ultimos.update(grupo)

        muestra = {
            "timestamp": (self._t0 or 0.0) + clave * self._bucket_s,
            # Instante local en el que el PLC tomó la muestra, en el dominio
            # de `time.monotonic()`. Es lo que alinea la señal con el reloj
            # del ensayo sin depender de cuándo llegó el lote.
            "monotonic": (self._origen_local or 0.0) + clave * self._bucket_s,
            "raw": {n: self._ultimos.get(n) for n in self._nombres},
            # Distingue un valor que el servidor acaba de reportar de uno
            # repetido porque nada cambió. Quien vigila la salud de la
            # suscripción necesita saberlo.
            "heartbeat": latido,
        }

        self._ultima_clave_emitida = clave

        try:
            self._on_sample(muestra)
        except Exception:
            # Un fallo aguas abajo no puede tumbar el hilo de la suscripción.
            pass

    def vaciar(self) -> None:
        """Emite lo que quede pendiente. Se llama al cerrar la suscripción."""
        with self._lock:
            if self._grupos:
                self._emitir_hasta(max(self._grupos))


class _Handler:
    """Recibe las notificaciones de python-opcua y las pasa al agrupador."""

    def __init__(self, agrupador: _Agrupador, nombre_por_nodo: dict) -> None:
        self._agrupador = agrupador
        self._nombres = nombre_por_nodo

    def datachange_notification(self, node, val, data) -> None:
        nombre = self._nombres.get(node.nodeid.to_string())
        if nombre is None:
            return

        timestamp = None
        try:
            valor = data.monitored_item.Value
            # SourceTimestamp es el instante en que el dato cambió en el PLC.
            # Si el servidor no lo rellena, el ServerTimestamp (cuándo lo
            # muestreó) conserva igual el espaciado entre muestras.
            fuente = valor.SourceTimestamp or valor.ServerTimestamp
            if fuente is not None:
                timestamp = fuente.timestamp()
        except Exception:
            pass

        self._agrupador.agregar(nombre, val, timestamp)

    def event_notification(self, event) -> None:  # pragma: no cover
        pass

    def status_change_notification(self, status) -> None:  # pragma: no cover
        pass


class OpcUaSampler:
    """
    Muestreo por suscripción sobre un conjunto de nodos.

    `start()` devuelve el intervalo **REVISADO** que concedió el servidor, que
    puede ser mayor que el pedido: el ctrlX no está obligado a aceptar 10 ms.
    Quien llama decide qué hacer con esa diferencia.
    """

    # Cuántos periodos de muestreo caben en un lote, como máximo. Más alto =
    # menos mensajes de red; más bajo = menos latencia hasta ver el dato.
    PUBLISH_FACTOR = 10

    # Tope del intervalo de publicación. Es la latencia máxima con la que el
    # servidor retiene un cambio antes de mandarlo, sea cual sea el periodo
    # de muestreo. Sin tope, con 500 ms de muestreo el lote era de 5 s y el
    # escalón se veía cinco segundos tarde.
    MAX_PUBLISHING_MS = 100.0

    # Margen sobre la cola calculada: si el servidor se retrasa un publish, sin
    # margen se perderían muestras silenciosamente (DiscardOldest).
    QUEUE_MARGIN = 3

    def __init__(self, client, nodes_por_nombre: dict[str, Any]) -> None:
        self._client = client
        self._nodes = dict(nodes_por_nombre)

        self._subscription = None
        self._handles: list = []
        self._agrupador: Optional[_Agrupador] = None

        self._requested_ms: Optional[float] = None
        self._revised_ms: Optional[float] = None
        self._publishing_ms: Optional[float] = None
        self._revised_publishing_ms: Optional[float] = None

        self._latido_thread: Optional[threading.Thread] = None
        self._parar = threading.Event()

    @classmethod
    def publishing_interval_ms(cls, sampling_ms: float) -> float:
        """Intervalo de publicación a pedir para un muestreo dado."""
        return max(1.0, min(sampling_ms * cls.PUBLISH_FACTOR, cls.MAX_PUBLISHING_MS))

    @property
    def requested_period_s(self) -> Optional[float]:
        return None if self._requested_ms is None else self._requested_ms / 1000.0

    @property
    def revised_period_s(self) -> Optional[float]:
        return None if self._revised_ms is None else self._revised_ms / 1000.0

    @property
    def publishing_period_s(self) -> Optional[float]:
        """Intervalo de publicación concedido, en segundos."""
        if self._revised_publishing_ms is None:
            return None
        return self._revised_publishing_ms / 1000.0

    @property
    def delay_s(self) -> Optional[float]:
        """Retraso fijo con el que salen las muestras (ventana de seguridad)."""
        return self._agrupador.retraso_s if self._agrupador else None

    @property
    def late_notifications(self) -> int:
        """Notificaciones que llegaron con su ciclo ya emitido. Debe ser 0."""
        return self._agrupador.tardias if self._agrupador else 0

    @property
    def recent_notifications(self) -> list[dict]:
        """Últimas notificaciones crudas del servidor, para diagnóstico."""
        return self._agrupador.registro if self._agrupador else []

    @property
    def monitored_count(self) -> int:
        """Cuántos items aceptó el servidor. Puede ser menos de los pedidos."""
        return len(self._handles)

    @property
    def ultimos_valores(self) -> dict:
        """Última foto conocida de las variables suscritas."""
        return self._agrupador.ultimos_valores if self._agrupador else {}

    def start(self, period_s: float, on_sample: Callable[[dict], None]) -> float:
        """
        Abre la suscripción. Devuelve el periodo real concedido, en segundos.

        Lanza `SubscriptionNotSupported` si el servidor no la acepta; quien
        llama puede entonces caer a polling.
        """
        if ua is None:  # pragma: no cover
            raise SubscriptionNotSupported("La librería opcua no está disponible.")

        if not self._nodes:
            raise SubscriptionNotSupported("No hay nodos que suscribir.")

        sampling_ms = max(1.0, float(period_s) * 1000.0)
        publishing_ms = self.publishing_interval_ms(sampling_ms)
        # La cola tiene que absorber un lote entero más margen. Con el tope
        # de publicación, para periodos lentos el lote es de una sola muestra.
        queue_size = int(math.ceil(publishing_ms / sampling_ms)) + self.QUEUE_MARGIN

        self._requested_ms = sampling_ms
        self._publishing_ms = publishing_ms

        nombre_por_nodo = {
            node.nodeid.to_string(): nombre for nombre, node in self._nodes.items()
        }

        # El bucket agrupa por instante; se usa el periodo PEDIDO como tamaño
        # inicial y se reajusta abajo con el revisado.
        self._agrupador = _Agrupador(
            nombres=list(self._nodes.keys()),
            bucket_s=sampling_ms / 1000.0,
            on_sample=on_sample,
        )

        handler = _Handler(self._agrupador, nombre_por_nodo)

        try:
            self._subscription = self._client.create_subscription(publishing_ms, handler)
        except Exception as exc:
            raise SubscriptionNotSupported(
                f"El servidor no aceptó la suscripción: {exc}"
            ) from exc

        try:
            self._handles = self._crear_monitored_items(sampling_ms, queue_size)
        except Exception as exc:
            self.stop()
            raise SubscriptionNotSupported(
                f"No se pudieron crear los items monitorizados: {exc}"
            ) from exc

        self._revised_publishing_ms = self._leer_publishing_revisado(publishing_ms)
        self._revised_ms = self._intervalo_revisado(sampling_ms)

        # El agrupador tiene que usar el intervalo REAL: con el pedido, si el
        # servidor concede uno mayor, cada ciclo del PLC caería en un bucket
        # distinto y las variables no se juntarían nunca en la misma muestra.
        bucket_s = max(self._revised_ms / 1000.0, 1e-4)
        self._agrupador._bucket_s = bucket_s
        # Ventana de seguridad: lo que el servidor puede retener un cambio
        # (un publishing entero), más un ciclo de redondeo, más jitter.
        self._agrupador.set_retraso(
            self._revised_publishing_ms / 1000.0 + bucket_s + _Agrupador.MARGEN_S
        )

        self._arrancar_latido()

        return self._revised_ms / 1000.0

    def _arrancar_latido(self) -> None:
        """
        Hilo que llama a `_Agrupador.latido()` al ritmo de muestreo.

        Es el único camino de salida del agrupador: emite en orden los ciclos
        que ya quedaron fuera de la ventana de seguridad, con dato real si el
        servidor lo mandó y repitiendo el último valor si no.
        """
        self._parar.clear()

        periodo = max(self.revised_period_s or 0.02, 0.005)

        def bucle() -> None:
            while not self._parar.wait(periodo):
                try:
                    self._agrupador.latido()
                except Exception:
                    # Igual que en el agrupador: este hilo no puede morir por
                    # un fallo aguas abajo, o la señal se cortaría del todo.
                    pass

        self._latido_thread = threading.Thread(
            target=bucle, name="opcua-latido", daemon=True
        )
        self._latido_thread.start()

    def _crear_monitored_items(self, sampling_ms: float, queue_size: int) -> list:
        """
        `subscribe_data_change` fija SamplingInterval = PublishingInterval, que
        es justo lo que hay que evitar: se quiere muestrear rápido y publicar
        en lotes. Por eso se arma la petición a mano.
        """
        peticiones = []

        for node in self._nodes.values():
            rv = ua.ReadValueId()
            rv.NodeId = node.nodeid
            rv.AttributeId = ua.AttributeIds.Value

            params = ua.MonitoringParameters()
            params.ClientHandle = self._subscription._client_handle + 1
            self._subscription._client_handle += 1
            params.SamplingInterval = sampling_ms
            params.QueueSize = queue_size
            params.DiscardOldest = True

            item = ua.MonitoredItemCreateRequest()
            item.ItemToMonitor = rv
            item.MonitoringMode = ua.MonitoringMode.Reporting
            item.RequestedParameters = params

            peticiones.append(item)

        resultados = self._subscription.create_monitored_items(peticiones)

        return self._verificar_items(peticiones, resultados)

    @staticmethod
    def _verificar_items(peticiones: list, resultados: list) -> list:
        """
        Comprueba que el servidor aceptara cada item.

        `create_monitored_items` devuelve el MonitoredItemId cuando el item se
        creó, pero un **StatusCode** cuando el servidor lo rechazó. Sin mirarlo,
        una suscripción con todos los items rechazados queda "activa" y no
        entrega nada: el síntoma es un bucle de reconexión con el mensaje
        "la suscripción dejó de entregar muestras", que hace pensar en un
        problema de red cuando en realidad el servidor dijo que no desde el
        primer momento.
        """
        rechazados = []
        aceptados = []

        for peticion, resultado in zip(peticiones, resultados):
            # Un StatusCode donde debería ir un id es un rechazo.
            if isinstance(resultado, ua.StatusCode) or hasattr(resultado, "is_good"):
                nodo = peticion.ItemToMonitor.NodeId.to_string()
                rechazados.append(f"{nodo} -> {resultado}")
            else:
                aceptados.append(resultado)

        if not aceptados:
            raise SubscriptionNotSupported(
                "El servidor rechazó todos los items monitorizados: "
                + "; ".join(rechazados)
            )

        if rechazados:
            print(f"[SUB] Items rechazados por el servidor: {'; '.join(rechazados)}")

        return aceptados

    def _leer_publishing_revisado(self, pedido_ms: float) -> float:
        """
        Intervalo de publicación que concedió el servidor. Si no está
        accesible, se asume el pedido, que es lo mejor que se puede decir.
        """
        for atributo in ("RevisedPublishingInterval", "RequestedPublishingInterval"):
            valor = getattr(self._subscription.parameters, atributo, None)
            if valor:
                return max(float(valor), 1.0)

        return pedido_ms

    def _intervalo_revisado(self, sampling_ms: float) -> float:
        """
        Periodo de muestreo concedido.

        python-opcua no expone el `RevisedSamplingInterval` de cada item, así
        que se infiere: si el servidor estiró el intervalo de publicación, se
        asume que estiró el muestreo en la misma proporción.
        """
        pedido = self._publishing_ms or sampling_ms
        concedido = self._revised_publishing_ms or pedido

        if concedido > pedido:
            return max(sampling_ms * concedido / pedido, 1.0)

        return max(sampling_ms, 1.0)

    def stop(self) -> None:
        """Cierra la suscripción. Idempotente y silencioso: se llama al caer."""
        self._parar.set()

        if self._latido_thread is not None:
            self._latido_thread.join(timeout=1.0)
            self._latido_thread = None

        if self._agrupador is not None:
            try:
                self._agrupador.vaciar()
            except Exception:
                pass

        if self._subscription is not None:
            try:
                self._subscription.delete()
            except Exception:
                pass

        self._subscription = None
        self._handles = []
        self._agrupador = None

    @property
    def is_active(self) -> bool:
        return self._subscription is not None
