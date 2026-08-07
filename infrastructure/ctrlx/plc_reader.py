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

        if self._clock_start is None:
            self._clock_start = read_finished

        interval = None
        if self._last_sample_monotonic is not None:
            interval = read_finished - self._last_sample_monotonic

        self._last_sample_monotonic = read_finished
        self._last_read_duration_s = read_finished - read_started
        self._last_interval_s = interval

        sample: dict[str, Any] = {
            "timestamp": time.time(),
            "mapping": effective_mapping,
            # Nombres del programa para los desplegables. `raw` ya no los trae
            # todos, porque solo se muestrean los roles.
            "variables": catalog,
            "read_duration_s": round(self._last_read_duration_s, 4),
            "sample_interval_s": round(interval, 4) if interval is not None else None,
        }

        for role in SIGNAL_ROLES:
            sample[role] = self._value_for_role(raw_values, role, effective_mapping)

        # La variable de tiempo del PLC pasa a ser informativa; el eje real lo
        # marca el instante de captura.
        sample["plc_time"] = sample.get("time")
        sample["time"] = round(read_finished - self._clock_start, 4)

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
        if not self.is_running:
            return False, "El lector del PLC no está conectado."

        variable_name = self.resolve_role_variable(role)
        if not variable_name:
            return False, f"No hay ninguna variable asignada al rol '{role}'."

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

        with self._io_lock:
            if self._program_node is None:
                raise RuntimeError("Sin conexión con el PLC.")

            node = self._resolve_node(variable_name)
            if node is None:
                raise RuntimeError(
                    f"La variable '{variable_name}' no existe en el programa."
                )

            self._opc.write_value(node, value)

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

                # El reloj del ensayo arranca con la conexión, no con el objeto:
                # entre construir el reader y tener sesión pueden pasar segundos.
                self._clock_start = None
                self._last_sample_monotonic = None

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
                # Se invalida ANTES de desconectar: si no, una escritura podría
                # colarse con un nodo que ya apunta a una sesión cerrada y
                # fallar con un error incomprensible.
                with self._io_lock:
                    self._program_node = None
                    self._node_cache.clear()

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
