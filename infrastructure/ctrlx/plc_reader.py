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
