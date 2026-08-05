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

    def _build_sample(self, plc_prg_node) -> dict:
        # Bajo el lock: comparte socket con las escrituras del ensayo.
        with self._io_lock:
            raw_values = self._repo.read_program_values(plc_prg_node)
        effective_mapping = self._resolve_effective_mapping(raw_values)

        sample: dict[str, Any] = {
            "timestamp": time.time(),
            "mapping": effective_mapping,
        }

        for role in SIGNAL_ROLES:
            sample[role] = self._value_for_role(raw_values, role, effective_mapping)

        if self.include_raw:
            sample["raw"] = raw_values

        return sample

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
        with self._io_lock:
            if self._program_node is None:
                return None
            names = list(self._repo.read_program_values(self._program_node).keys())

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

                while not self._stop:
                    sample = self._build_sample(program_node)

                    if self.on_sample:
                        try:
                            self.on_sample(sample)
                        except Exception:
                            pass

                    time.sleep(self.period_s)

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
