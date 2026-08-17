from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from infrastructure.ctrlx.node_repository import (
    SIGNAL_ALIASES,
    SIGNAL_ROLES,
    NodeRepository,
    normalize_name,
    resolve_mapping,
)
from infrastructure.ctrlx.opcua_client import CtrlxOpcUaClient
from infrastructure.ctrlx.opcua_subscription import (
    OpcUaSampler,
    SubscriptionNotSupported,
)


class PLCReader:
    """
    Lee cíclicamente las variables de un programa PLC.

    El mapeo rol -> variable lo decide la vista y llega en `mapping`.
    Si no hay mapping (o falta algún rol) se cae a SIGNAL_ALIASES para
    mantener compatibilidad con el comportamiento anterior.
    """

    # Se mantiene por compatibilidad; la fuente de verdad es node_repository.
    SIGNAL_ALIASES = SIGNAL_ALIASES

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        program_name: str,
        on_sample: Optional[Callable[[dict], None]] = None,
        period_s: float = 0.1,
        mapping: Optional[dict[str, Optional[str]]] = None,
        include_raw: bool = True,
    ) -> None:
        self.url = url
        self.user = user
        self.password = password
        self.period_s = period_s
        self.program_name = program_name
        self.on_sample = on_sample
        self.include_raw = include_raw

        self.mapping: dict[str, Optional[str]] = {
            role: (mapping or {}).get(role) for role in SIGNAL_ROLES
        }

        self._stop = False
        self._thread: Optional[threading.Thread] = None
        self._opc = CtrlxOpcUaClient(url=url, user=user, password=password)
        self._repo = NodeRepository(self._opc)

        # Nodo del programa, vigente mientras haya conexión. Lo publica el hilo
        # de lectura para que las escrituras no tengan que volver a navegar el
        # árbol desde la raíz en cada tick.
        self._program_node = None

        # Serializa TODO el tráfico OPC UA. La lectura corre en este hilo y las
        # escrituras llegan desde el hilo del ensayo; comparten un solo socket,
        # y sin el lock las peticiones se intercalarían y el servidor
        # respondería a destiempo.
        self._io_lock = threading.RLock()

        # Cache nombre -> nodo. Buscar el hijo recorriendo todo el programa en
        # cada escritura costaría un browse completo cada 200 ms.
        self._node_cache: dict[str, Any] = {}

        # Muestreo por suscripción. Es la única forma de bajar de la latencia
        # de red: con polling cada muestra cuesta un viaje de ida y vuelta, así
        # que el periodo nunca puede ser menor que el RTT. Si el servidor no la
        # acepta se sigue por polling sin interrumpir el trabajo.
        self._sampler: Optional[OpcUaSampler] = None
        self._sampling_mode: str = "polling"
        self._requested_period_s: Optional[float] = None
        self._revised_period_s: Optional[float] = None
        self._last_subscription_sample: Optional[float] = None
        # Ancla para pasar los timestamps del servidor al reloj local. Ver
        # `_anclar_reloj_del_servidor`.
        self._sub_time_offset: Optional[float] = None
        # ¿Llegó alguna vez una muestra por esta suscripción? Distingue "se
        # quedó muda a mitad" de "nunca arrancó", que se arreglan distinto.
        self._subscription_delivered = False
        self._last_real_notification: Optional[float] = None
        # Se apaga tras comprobar que en este servidor las suscripciones no
        # entregan nada. Sin esto la reconexión las reintenta para siempre y
        # la aplicación se queda sin datos en vez de caer a polling.
        self._subscription_disabled = False
        self._subscription_error: Optional[str] = None

        # Catálogo de nombres del programa, para los desplegables de la vista.
        # Se refresca cada `CATALOG_REFRESH_S`, no en cada muestra: es un browse
        # y no cambia salvo que se cargue otro programa en el PLC.
        self._variable_names: list[str] = []
        self._catalog_ts: float = 0.0

        # Origen del reloj del ensayo. Ver `_build_sample`.
        self._clock_start: Optional[float] = None

        # Diagnóstico del muestreo real, que casi nunca coincide con `period_s`.
        self._last_sample_monotonic: Optional[float] = None
        self._last_read_duration_s: Optional[float] = None
        self._last_interval_s: Optional[float] = None

    # Cada cuánto se vuelve a listar las variables del programa.
    CATALOG_REFRESH_S = 10.0

    # Cada cuánto se comprueba, con una lectura directa, que la suscripción no
    # esté colgada entregando valores viejos. Ver `_suscripcion_al_dia`.
    VERIFY_EVERY_S = 5.0

    # Margen de la comparación. Un float que viaja por OPC UA puede volver con
    # el último bit distinto sin que nada esté mal.
    VERIFY_TOLERANCE = 1e-6

    # ------------------------------------------------------------------ #
    # Resolución de señales
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_name(name: str) -> str:
        return normalize_name(name)

    @classmethod
    def _pick_value(cls, raw_values: dict, aliases: list[str]) -> Any:
        normalized_map = {cls._normalize_name(k): v for k, v in raw_values.items()}

        for alias in aliases:
            key = cls._normalize_name(alias)
            if key in normalized_map:
                return normalized_map[key]

        return None

    def _resolve_effective_mapping(self, raw_values: dict) -> dict[str, Optional[str]]:
        """Completa el mapping del usuario con los alias para los roles vacíos."""
        return resolve_mapping(self.mapping, list(raw_values.keys()))

    @classmethod
    def _value_for_role(
        cls,
        raw_values: dict,
        role: str,
        effective_mapping: dict[str, Optional[str]],
    ) -> Any:
        variable_name = effective_mapping.get(role)

        if variable_name:
            normalized_map = {cls._normalize_name(k): v for k, v in raw_values.items()}
            value = normalized_map.get(cls._normalize_name(variable_name))
            if value is not None:
                return value

        # último recurso: alias conocidos
        return cls._pick_value(raw_values, SIGNAL_ALIASES.get(role, []))

    # ------------------------------------------------------------------ #
    # Muestreo
    # ------------------------------------------------------------------ #

    def _refresh_catalog_locked(self, program_node) -> None:
        """Relista los nombres del programa si el catálogo caducó. Con el lock tomado."""
        now = time.monotonic()

        if self._variable_names and (now - self._catalog_ts) < self.CATALOG_REFRESH_S:
            return

        try:
            self._variable_names = self._repo.list_variable_names(program_node)
            self._catalog_ts = now
        except Exception:
            # Si el browse falla se conserva el catálogo anterior: es solo para
            # poblar desplegables y no vale la pena cortar el muestreo por ello.
            pass

    def _build_sample(self, plc_prg_node) -> dict:
        """
        Una muestra del PLC.

        Solo se leen las variables **mapeadas a un rol**, no el programa entero.
        Cada lectura es un viaje de red, así que muestrear todas las variables
        hacía que el periodo real fuera varias veces el configurado: un ensayo
        de 20 s terminaba con 20 muestras en vez de 100, y con tan pocos puntos
        durante el transitorio la constante de tiempo se colapsa a cero.

        El eje de tiempo NO sale del PLC. Se usa un reloj monótono propio y la
        variable de tiempo del programa se conserva aparte, en `plc_time`, solo
        como referencia. Un contador de PLC que se reinicia cíclicamente
        (`IF rTimeSec >= 20 THEN rTimeSec := 0`) produce un eje en diente de
        sierra: al integrar el modelo aparecen `dt` negativos y el R² se va a
        valores absurdos aunque la ganancia salga bien.
        """
        read_started = time.monotonic()

        with self._io_lock:
            self._refresh_catalog_locked(plc_prg_node)
            catalog = list(self._variable_names)

            effective_mapping = resolve_mapping(self.mapping, catalog)

            # Nombres únicos a leer: los de los roles con variable asignada.
            wanted: list[str] = []
            for name in effective_mapping.values():
                if name and name not in wanted:
                    wanted.append(name)

            raw_values: dict[str, Any] = {}
            for name in wanted:
                node = self._resolve_node(name)
                if node is None:
                    continue
                try:
                    raw_values[name] = self._opc.read_value(node)
                except Exception as exc:
                    raw_values[name] = f"READ_ERROR: {exc}"

        read_finished = time.monotonic()
        self._last_read_duration_s = read_finished - read_started

        return self._compose_sample(
            raw_values=raw_values,
            catalog=catalog,
            effective_mapping=effective_mapping,
            captured_at=read_finished,
        )

    def _compose_sample(
        self,
        raw_values: dict,
        catalog: list[str],
        effective_mapping: dict,
        captured_at: float,
    ) -> dict:
        """
        Arma la muestra a partir de los valores crudos.

        Compartida por las dos rutas de muestreo —polling y suscripción— para
        que el resto de la aplicación reciba exactamente la misma forma sin
        importar cómo se obtuvieron los datos.
        """
        if self._clock_start is None:
            self._clock_start = captured_at

        interval = None
        if self._last_sample_monotonic is not None:
            interval = captured_at - self._last_sample_monotonic

        self._last_sample_monotonic = captured_at
        self._last_interval_s = interval

        sample: dict[str, Any] = {
            "timestamp": time.time(),
            "mapping": effective_mapping,
            # Nombres del programa para los desplegables. `raw` ya no los trae
            # todos, porque solo se muestrean los roles.
            "variables": catalog,
            "sampling_mode": self._sampling_mode,
            "read_duration_s": (
                round(self._last_read_duration_s, 4)
                if self._last_read_duration_s is not None
                else None
            ),
            "sample_interval_s": round(interval, 4) if interval is not None else None,
        }

        for role in SIGNAL_ROLES:
            sample[role] = self._value_for_role(raw_values, role, effective_mapping)

        # La variable de tiempo del PLC pasa a ser informativa; el eje real lo
        # marca el instante de captura.
        sample["plc_time"] = sample.get("time")
        sample["time"] = round(captured_at - self._clock_start, 4)

        if self.include_raw:
            sample["raw"] = raw_values

        return sample

    @property
    def last_interval_s(self) -> Optional[float]:
        """Tiempo real entre las dos últimas muestras. `None` si aún no hay dos."""
        return self._last_interval_s

    @property
    def last_read_duration_s(self) -> Optional[float]:
        """Lo que tardó la última lectura OPC UA."""
        return self._last_read_duration_s

    # ------------------------------------------------------------------ #
    # Muestreo por suscripción
    # ------------------------------------------------------------------ #

    @property
    def sampling_mode(self) -> str:
        """`subscription` o `polling`."""
        return self._sampling_mode

    @property
    def requested_period_s(self) -> Optional[float]:
        return self._requested_period_s

    @property
    def revised_period_s(self) -> Optional[float]:
        """Periodo que el servidor concedió. Puede ser mayor que el pedido."""
        return self._revised_period_s

    @property
    def subscription_error(self) -> Optional[str]:
        """Por qué no se pudo suscribir, si se cayó a polling."""
        return self._subscription_error

    def _try_subscription(self, program_node) -> bool:
        """
        Intenta muestrear por suscripción. Devuelve False si hay que usar polling.

        No lanza: perder la suscripción es una degradación, no un error fatal.
        El trabajo sigue con polling y la vista muestra qué modo está activo.
        """
        # Se cierra la anterior antes de abrir otra: este método se vuelve a
        # llamar al cambiar el periodo, y sin esto quedaría una suscripción
        # huérfana publicando contra un agrupador que ya nadie lee.
        if self._sampler is not None:
            self._sampler.stop()

        self._sampler = None
        self._sampling_mode = "polling"
        self._revised_period_s = None
        self._subscription_error = None
        self._requested_period_s = self.period_s
        self._subscription_delivered = False
        self._last_real_notification = None

        try:
            with self._io_lock:
                self._refresh_catalog_locked(program_node)
                catalog = list(self._variable_names)
                effective_mapping = resolve_mapping(self.mapping, catalog)

                # Solo los roles con variable asignada: suscribir el programa
                # entero multiplicaría el tráfico sin aportar nada.
                nodos: dict[str, Any] = {}
                for nombre in effective_mapping.values():
                    if not nombre or nombre in nodos:
                        continue
                    node = self._resolve_node(nombre)
                    if node is not None:
                        nodos[nombre] = self._opc.value_node(node)

            if not nodos:
                self._subscription_error = "Ninguna variable mapeada existe en el PLC."
                return False

            sampler = OpcUaSampler(self._opc.client, nodos)

            def entregar(parcial: dict) -> None:
                self._on_subscription_sample(parcial, catalog, effective_mapping)

            revisado = sampler.start(self.period_s, entregar)

            self._sampler = sampler
            self._sampling_mode = "subscription"
            self._revised_period_s = revisado
            self._last_subscription_sample = time.monotonic()

            print(
                f"[SUB] Activa — pedido {self.period_s * 1000:.0f} ms, "
                f"concedido {revisado * 1000:.0f} ms, "
                f"{sampler.monitored_count}/{len(nodos)} variables aceptadas: "
                f"{', '.join(nodos)}"
            )
            return True

        except SubscriptionNotSupported as exc:
            self._subscription_error = str(exc)
            print(f"[SUB] No disponible, se usa polling: {exc}")
            return False

        except Exception as exc:
            # El `try` cubre TODO el montaje, incluida la línea que lo reporta.
            #
            # Antes terminaba justo antes de esas líneas, así que un fallo ahí
            # —con la suscripción ya creada— salía al bucle exterior, que lo
            # trataba como una caída de red: desconectar, esperar, reconectar,
            # repetir. La aplicación no recibía ni una muestra y el log decía
            # "OPC UA FAIL", apuntando al PLC en vez de al código.
            #
            # La regla es que nada de la ruta de suscripción pueda impedir que
            # se lea por polling, que siempre funciona.
            self._subscription_error = f"{type(exc).__name__}: {exc}"
            print(f"[SUB] Falló, se usa polling: {type(exc).__name__}: {exc}")

            if self._sampler is not None:
                try:
                    self._sampler.stop()
                except Exception:
                    pass

            self._sampler = None
            self._sampling_mode = "polling"
            return False

    def _anclar_reloj_del_servidor(self, server_ts: Optional[float]) -> float:
        """
        Pasa un `SourceTimestamp` del PLC al reloj local conservando su espaciado.

        Es la pieza que hace útil la suscripción. El servidor publica en LOTES:
        con 60 ms de muestreo manda diez muestras juntas cada 600 ms. Si el eje
        de tiempo se construye con el instante de LLEGADA, esas diez muestras
        caen todas en el mismo momento y el eje sale como una escalera —diez
        puntos en t=0, salto a t=0.6, otros diez— en vez de un punto cada 60 ms.

        Sobre esa escalera, la identificación lee mal el tiempo muerto y la
        constante de tiempo, que es justo lo que se estaba tratando de mejorar.

        Los timestamps del servidor sí vienen espaciados 60 ms exactos. Se les
        aplica un desfase fijo, calculado una sola vez, para llevarlos al mismo
        origen que `time.monotonic()`: así conviven con el polling y con el
        reloj del ensayo sin mezclar dominios.
        """
        if server_ts is None:
            return time.monotonic()

        if self._sub_time_offset is None:
            self._sub_time_offset = time.monotonic() - server_ts

        return server_ts + self._sub_time_offset

    def _on_subscription_sample(
        self, parcial: dict, catalog: list[str], effective_mapping: dict
    ) -> None:
        """Convierte lo que entrega el agrupador en una muestra completa."""
        self._last_subscription_sample = time.monotonic()

        # Un latido repite el último valor porque nada cambió; no prueba que la
        # suscripción siga viva. Solo una notificación real lo hace.
        if not parcial.get("heartbeat"):
            self._subscription_delivered = True
            self._last_real_notification = time.monotonic()

        sample = self._compose_sample(
            raw_values=parcial.get("raw", {}),
            catalog=catalog,
            effective_mapping=effective_mapping,
            captured_at=self._anclar_reloj_del_servidor(parcial.get("timestamp")),
        )

        if self.on_sample:
            try:
                self.on_sample(sample)
            except Exception:
                pass

    def muestrear_por_suscripcion(self, program_node) -> bool:
        """
        Lleva el muestreo por suscripción mientras se pueda.

        Devuelve True si la suscripción se encargó del trabajo, y False si hay
        que caer a polling.
        """
        if self._subscription_disabled or not self._try_subscription(program_node):
            return False

        while not self._stop:
            motivo = self._vigilar_suscripcion()

            if motivo == "muda":
                # Se creó pero nunca entregó nada. Reintentarla es el bucle que
                # deja la aplicación sin datos: mejor apagarla y usar polling,
                # que es más lento pero funciona.
                self._subscription_disabled = True
                self._sampling_mode = "polling"

                if self._sampler is not None:
                    self._sampler.stop()
                    self._sampler = None

                print(f"[SUB] Desactivada: {self._subscription_error}")
                return False

            if motivo != "periodo":
                return True

            # El periodo se cambió en la vista. Una suscripción se negocia con
            # un intervalo fijo al abrirla: a diferencia del polling, que lee
            # `period_s` en cada vuelta, aquí no basta con cambiar el atributo.
            # Sin reabrir, mover el campo de 100 ms a 20 ms no haría nada y el
            # aviso de la vista seguiría mostrando el ritmo viejo.
            if not self._try_subscription(program_node):
                return False

        return True

    def _vigilar_suscripcion(self) -> Optional[str]:
        """
        Espera mientras la suscripción entrega muestras.

        Devuelve `"periodo"` si hay que reabrirla con otro intervalo, `"muda"`
        si nunca entregó nada, o None si se está parando.

        Los dos silencios no son el mismo problema:

        * **Se quedó muda a mitad**: el servidor dejó de publicar o la sesión
          murió por debajo. Reconectar arregla eso, así que se lanza.
        * **Nunca entregó nada**: el servidor aceptó la suscripción pero no
          publica. Reconectar solo repite el mismo resultado, y mientras tanto
          la aplicación no recibe ni una muestra. Ahí hay que caer a polling.

        Sin esta distinción el síntoma es un bucle de "dejó de entregar
        muestras / retry" que parece un problema de red y deja la app vacía.
        """
        periodo = self._revised_period_s or self.period_s

        # El primer lote tarda un PublishingInterval entero, varias veces el
        # periodo de muestreo. Margen de sobra para no declarar muda una
        # suscripción que solo estaba arrancando.
        tolerancia_arranque = max(6.0, periodo * 100)

        proxima_verificacion = time.monotonic() + self.VERIFY_EVERY_S

        while not self._stop:
            time.sleep(0.2)

            if self._sampler is None or not self._sampler.is_active:
                return None

            if self.period_s != self._requested_period_s:
                return "periodo"

            ahora = time.monotonic()

            if not self._subscription_delivered:
                ultimo = self._last_subscription_sample or ahora
                if ahora - ultimo > tolerancia_arranque:
                    self._subscription_error = (
                        f"El servidor aceptó la suscripción pero no publicó "
                        f"ninguna muestra en {tolerancia_arranque:.0f} s."
                        + self._pista_de_suscripcion_muda()
                    )
                    return "muda"
                continue

            # A partir de aquí NO se vigila por silencio.
            #
            # Una suscripción OPC UA solo notifica cambios, así que un proceso
            # en régimen permanente calla legítimamente: es exactamente lo que
            # pasa en la línea base, justo antes del escalón. Matarla ahí era
            # el motivo de que se "cortara" sola en cada ensayo.
            #
            # Lo que sí hay que descartar es que esté colgada entregando
            # valores viejos. Eso no se distingue del silencio legítimo mirando
            # el reloj: hay que preguntarle al PLC.
            if ahora >= proxima_verificacion:
                proxima_verificacion = ahora + self.VERIFY_EVERY_S

                if not self._suscripcion_al_dia():
                    raise RuntimeError(
                        "La suscripción quedó desfasada: el PLC tiene valores "
                        "que no se están recibiendo."
                    )

        return None

    def _suscripcion_al_dia(self) -> bool:
        """
        ¿La foto que mantiene la suscripción coincide con el PLC?

        Una lectura directa cada pocos segundos. Es barata comparada con el
        polling —una cada `VERIFY_EVERY_S`, no una por muestra— y es la única
        forma de separar "el proceso está quieto" de "la suscripción está
        colgada", porque desde fuera las dos se ven igual: valores que no
        cambian.

        Ante la duda devuelve True: una lectura fallida no es prueba de que la
        suscripción esté rota, y tirarla por eso sería peor.
        """
        cache = self._sampler.ultimos_valores if self._sampler else {}
        if not cache:
            return True

        nombre = next(iter(cache))
        actual = self.read_variable_value(nombre)

        if actual is None:
            return True

        esperado = cache[nombre]

        if isinstance(actual, (int, float)) and isinstance(esperado, (int, float)):
            return abs(float(actual) - float(esperado)) <= self.VERIFY_TOLERANCE

        return actual == esperado

    def _pista_de_suscripcion_muda(self) -> str:
        """
        Añade la causa concreta cuando se puede saber.

        python-opcua marca `has_unknown_handlers` si llegaron notificaciones
        con un ClientHandle que no reconoce. Eso separa dos culpables muy
        distintos: el servidor no publica, o publica y nosotros no sabemos
        encaminar lo que manda.
        """
        sub = getattr(self._sampler, "_subscription", None)

        if getattr(sub, "has_unknown_handlers", False):
            return (
                " El servidor SÍ publicó, pero con identificadores que el "
                "cliente no reconoce."
            )

        return " Se usará polling."

    def _resolve_program_node(self):
        return self._repo.resolve_program_node(self.program_name)

    def list_variables(self) -> list[dict]:
        """Lista las variables del programa en una conexión puntual."""
        self._opc.connect()
        try:
            program_node = self._resolve_program_node()
            if program_node is None:
                raise RuntimeError(
                    f"No se encontró el programa '{self.program_name}' dentro de 'sym'."
                )
            return [v.to_dict() for v in self._repo.list_variables(program_node)]
        finally:
            self._opc.disconnect()

    def set_mapping(self, mapping: Optional[dict[str, Optional[str]]]) -> None:
        self.mapping = {role: (mapping or {}).get(role) for role in SIGNAL_ROLES}
        # Los nodos cacheados corresponden al mapeo anterior.
        with self._io_lock:
            self._node_cache.clear()

    # ------------------------------------------------------------------ #
    # Escritura
    # ------------------------------------------------------------------ #

    def _resolve_node(self, variable_name: str):
        """Nodo de una variable, con cache. Debe llamarse con el lock tomado."""
        if not variable_name:
            return None

        key = normalize_name(variable_name)
        if key in self._node_cache:
            return self._node_cache[key]

        if self._program_node is None:
            return None

        node = self._repo.find_variable_node(self._program_node, variable_name)
        if node is not None:
            self._node_cache[key] = node

        return node

    def resolve_role_variable(self, role: str) -> Optional[str]:
        """Nombre de la variable asignada a un rol, resolviendo alias si hace falta."""
        name = self.mapping.get(role)
        if name:
            return name

        # Sin mapeo explícito hay que mirar qué variables existen de verdad.
        # Basta el catálogo de nombres: leer sus valores sería un viaje de red
        # por variable solo para saber cómo se llaman.
        with self._io_lock:
            if self._program_node is None:
                return None

            self._refresh_catalog_locked(self._program_node)
            names = list(self._variable_names)

        return resolve_mapping(self.mapping, names).get(role)

    def can_write_role(self, role: str) -> tuple[bool, str]:
        """
        Comprueba si el rol se puede escribir. Devuelve (ok, motivo).

        Se consulta antes de armar la escritura para poder avisar en la vista,
        en lugar de descubrir a mitad del ensayo que el actuador no obedece.
        """
        variable_name = self.resolve_role_variable(role)
        if not variable_name and self.is_running:
            return False, f"No hay ninguna variable asignada al rol '{role}'."

        return self.can_write_variable(variable_name)

    def can_write_variable(self, variable_name: Optional[str]) -> tuple[bool, str]:
        """
        Igual que `can_write_role` pero apuntando a una variable por nombre.

        Existe para lo que no es una señal del ensayo —el ciclo de tarea, por
        ejemplo—: son variables que no tienen rol pero se escriben igual.
        """
        if not self.is_running:
            return False, "El lector del PLC no está conectado."

        if not variable_name:
            return False, "No se indicó ninguna variable."

        with self._io_lock:
            if self._program_node is None:
                return False, "Todavía no se resolvió el programa en el PLC."

            node = self._resolve_node(variable_name)
            if node is None:
                return False, (
                    f"La variable '{variable_name}' no existe en el programa "
                    f"'{self.program_name}'."
                )

            try:
                writable = self._opc.is_writable(node)
            except Exception as exc:
                return False, f"No se pudo consultar los permisos de '{variable_name}': {exc}"

        if not writable:
            return False, (
                f"El servidor OPC UA declara '{variable_name}' como solo lectura. "
                "En el ctrlX suele pasar cuando la variable la escribe el propio "
                "programa PLC: hay que exponer una variable de comando aparte."
            )

        return True, f"'{variable_name}' es escribible."

    def write_role_value(self, role: str, value: float) -> None:
        """
        Escribe un valor en la variable asignada a un rol.

        Lanza excepción si algo falla: quien llama decide si eso aborta el
        ensayo. No se reintenta aquí para no tapar un problema persistente.
        """
        variable_name = self.resolve_role_variable(role)
        if not variable_name:
            raise RuntimeError(f"No hay ninguna variable asignada al rol '{role}'.")

        self.write_variable_value(variable_name, value)

    def write_variable_value(self, variable_name: str, value: float) -> None:
        """Escribe en una variable por nombre, sin pasar por los roles."""
        if not variable_name:
            raise RuntimeError("No se indicó ninguna variable.")

        with self._io_lock:
            if self._program_node is None:
                raise RuntimeError("Sin conexión con el PLC.")

            node = self._resolve_node(variable_name)
            if node is None:
                raise RuntimeError(
                    f"La variable '{variable_name}' no existe en el programa."
                )

            self._opc.write_value(node, value)

    def read_variable_value(self, variable_name: str):
        """Lee una variable por nombre. Devuelve None si no se puede."""
        if not variable_name:
            return None

        with self._io_lock:
            if self._program_node is None:
                return None

            node = self._resolve_node(variable_name)
            if node is None:
                return None

            try:
                return self._opc.value_node(node).get_value()
            except Exception:
                return None

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        backoff = 1.0
        max_backoff = 30.0

        while not self._stop:
            try:
                self._opc.connect()
                backoff = 1.0

                program_node = self._resolve_program_node()
                if program_node is None:
                    raise RuntimeError(
                        f"No se encontró el programa '{self.program_name}' dentro de 'sym'."
                    )

                # Se publica para que las escrituras del ensayo lo usen sin
                # volver a navegar el árbol. El cache se descarta: tras una
                # reconexión los nodos viejos apuntan a una sesión muerta.
                with self._io_lock:
                    self._program_node = program_node
                    self._node_cache.clear()
                    self._repo.invalidate_layout()

                # El reloj del ensayo arranca con la conexión, no con el objeto:
                # entre construir el reader y tener sesión pueden pasar segundos.
                self._clock_start = None
                self._last_sample_monotonic = None
                # El ancla se recalcula por conexión: tras reconectar, el
                # desfase viejo mandaría las muestras al pasado.
                self._sub_time_offset = None

                # Primero se intenta la suscripción: es la única forma de
                # muestrear por debajo de la latencia de red. Si el servidor no
                # la acepta se sigue por polling, que siempre funciona.
                if self.muestrear_por_suscripcion(program_node):
                    continue

                deadline = time.monotonic()

                while not self._stop:
                    sample = self._build_sample(program_node)

                    if self.on_sample:
                        try:
                            self.on_sample(sample)
                        except Exception:
                            pass

                    # Se duerme hasta el siguiente deadline, no `period_s` fijo.
                    # Durmiendo el periodo completo tras la lectura, el periodo
                    # real sería `lectura + period_s`: con lecturas de ~0.8 s el
                    # muestreo cae a 1 Hz aunque se hayan pedido 5 Hz.
                    deadline += self.period_s
                    remaining = deadline - time.monotonic()

                    if remaining > 0:
                        time.sleep(remaining)
                    else:
                        # La lectura ya tardó más que el periodo pedido: no se
                        # puede ir más rápido, y arrastrar el retraso solo haría
                        # que el hilo nunca durmiera.
                        deadline = time.monotonic()

            except Exception as exc:
                print(f"OPC UA FAIL {self.url} -> {exc} | retry en {backoff:.1f}s")
                time.sleep(min(backoff, max_backoff))
                backoff = min(backoff * 2.0, max_backoff)

            finally:
                # La suscripción se cierra ANTES que la sesión: borrarla con el
                # socket ya cerrado deja el hilo interno de python-opcua
                # esperando una respuesta que no va a llegar.
                if self._sampler is not None:
                    self._sampler.stop()
                    self._sampler = None

                self._sampling_mode = "polling"
                self._revised_period_s = None

                # Se invalida ANTES de desconectar: si no, una escritura podría
                # colarse con un nodo que ya apunta a una sesión cerrada y
                # fallar con un error incomprensible.
                with self._io_lock:
                    self._program_node = None
                    self._node_cache.clear()
                    self._repo.invalidate_layout()

                self._opc.disconnect()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

    @property
    def is_running(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())
