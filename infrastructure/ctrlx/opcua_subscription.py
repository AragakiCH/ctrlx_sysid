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
* Cada `publishing_interval` envía el lote entero en un solo mensaje.

Así el ritmo de muestreo lo fija el PLC, no la red: en un lote de 100 ms llegan
10 muestras de 10 ms con sus marcas de tiempo originales.

El precio es que las notificaciones llegan **por variable**, no como muestras
sincronizadas. Reconstruirlas es el trabajo de `_Agrupador`.
"""

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
    se agrupan por instante redondeado al intervalo de muestreo.

    Un grupo se emite cuando llega una notificación de un instante POSTERIOR:
    eso significa que el PLC ya avanzó de ciclo y el grupo anterior no va a
    recibir nada más. Esperar a tenerlo "completo" bloquearía para siempre si
    una variable no cambia y el servidor no la reporta.
    """

    def __init__(
        self,
        nombres: list[str],
        bucket_s: float,
        on_sample: Callable[[dict], None],
        max_pendientes: int = 200,
    ) -> None:
        self._nombres = list(nombres)
        self._bucket_s = max(bucket_s, 1e-4)
        self._on_sample = on_sample
        self._max_pendientes = max_pendientes

        self._lock = threading.RLock()
        self._grupos: dict[int, dict[str, Any]] = {}
        # Último valor conocido de cada variable: rellena los huecos de las que
        # no cambiaron en ese ciclo, que el servidor no reporta.
        self._ultimos: dict[str, Any] = {}
        self._ultima_clave_emitida: Optional[int] = None
        # Origen de tiempos. Los timestamps OPC UA son epoch (~1.7e9); dividir
        # eso por 0.01 da números enormes donde el error de coma flotante ya
        # pesa. Trabajando sobre la diferencia contra el primero, los valores
        # se mantienen pequeños y el redondeo es exacto.
        self._t0: Optional[float] = None

    def _clave(self, timestamp: float) -> int:
        """
        Índice del ciclo al que pertenece este instante.

        Se **redondea**, no se trunca. Con `int()` un instante como 300.010 se
        convierte en 300.0099999… al dividir, cae en el bucket anterior y dos
        ciclos consecutivos se funden en uno: se pierde una muestra de cada dos
        sin ningún aviso.
        """
        if self._t0 is None:
            self._t0 = timestamp

        return int(round((timestamp - self._t0) / self._bucket_s))

    def agregar(self, nombre: str, valor: Any, timestamp: Optional[float]) -> None:
        if timestamp is None:
            timestamp = time.time()

        clave = self._clave(timestamp)

        with self._lock:
            self._ultimos[nombre] = valor

            if self._ultima_clave_emitida is not None and clave <= self._ultima_clave_emitida:
                # Llegó tarde: el grupo de ese instante ya salió. Se conserva
                # como último valor conocido, pero no se reabre el grupo.
                return

            grupo = self._grupos.setdefault(clave, {})
            grupo[nombre] = valor

            listas = [k for k in self._grupos if k < clave]
            self._emitir(listas)

            # Salvaguarda: si el servidor manda timestamps desordenados o una
            # variable deja de reportar, los grupos se acumularían sin fin.
            if len(self._grupos) > self._max_pendientes:
                sobrantes = sorted(self._grupos)[: len(self._grupos) - self._max_pendientes]
                self._emitir(sobrantes)

    def _emitir(self, claves: list[int]) -> None:
        """Debe llamarse con el lock tomado."""
        for clave in sorted(claves):
            grupo = self._grupos.pop(clave, None)
            if grupo is None:
                continue

            muestra = {
                "timestamp": (self._t0 or 0.0) + clave * self._bucket_s,
                "raw": {n: grupo.get(n, self._ultimos.get(n)) for n in self._nombres},
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
            self._emitir(list(self._grupos))


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
            fuente = data.monitored_item.Value.SourceTimestamp
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

    # Cuántos periodos de muestreo caben en un lote. Más alto = menos mensajes
    # de red pero más latencia hasta ver el dato; más bajo = al revés.
    PUBLISH_FACTOR = 10

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

    @property
    def requested_period_s(self) -> Optional[float]:
        return None if self._requested_ms is None else self._requested_ms / 1000.0

    @property
    def revised_period_s(self) -> Optional[float]:
        return None if self._revised_ms is None else self._revised_ms / 1000.0

    @property
    def monitored_count(self) -> int:
        """Cuántos items aceptó el servidor. Puede ser menos de los pedidos."""
        return len(self._handles)

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
        publishing_ms = sampling_ms * self.PUBLISH_FACTOR
        queue_size = int(self.PUBLISH_FACTOR) + self.QUEUE_MARGIN

        self._requested_ms = sampling_ms

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

        self._revised_ms = self._leer_intervalo_revisado(sampling_ms)

        # El agrupador tiene que usar el intervalo REAL: con el pedido, si el
        # servidor concede uno mayor, cada ciclo del PLC caería en un bucket
        # distinto y las variables no se juntarían nunca en la misma muestra.
        self._agrupador._bucket_s = max(self._revised_ms / 1000.0, 1e-4)

        return self._revised_ms / 1000.0

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

    def _leer_intervalo_revisado(self, pedido_ms: float) -> float:
        """
        El servidor puede conceder un intervalo mayor que el pedido. Se busca
        en los parámetros de la suscripción; si no está accesible, se asume el
        pedido, que es lo mejor que se puede decir.
        """
        for atributo in ("RevisedPublishingInterval", "RequestedPublishingInterval"):
            valor = getattr(self._subscription.parameters, atributo, None)
            if valor:
                # Los items se muestrean a `sampling_ms`, no al publishing:
                # se devuelve el publishing dividido por el factor.
                return max(float(valor) / self.PUBLISH_FACTOR, 1.0)

        return pedido_ms

    def stop(self) -> None:
        """Cierra la suscripción. Idempotente y silencioso: se llama al caer."""
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
