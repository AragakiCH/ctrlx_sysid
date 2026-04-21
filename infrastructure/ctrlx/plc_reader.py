from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from opcua import ua

from infrastructure.ctrlx.opcua_client import CtrlxOpcUaClient


class PLCReader:
    SIGNAL_ALIASES = {
        "time": [
            "rTimeSec",
            "rTiempoSeg",
            "arrTimeSec",
            "time",
            "tiempo",
        ],
        "actuator": [
            "rActuator",
            "AO_Actuador_mA",
            "AO_Actuador",
            "actuator",
            "actuador",
        ],
        "sensor": [
            "rSensor",
            "AI_Sensor_mA",
            "AI_Sensor",
            "sensor",
            "sensor_ai",
        ],
        "setpoint": [
            "rSetPoint",
            "SP_mA",
            "SP",
            "setpoint",
            "set_point",
        ],
        "signal_type": [
            "uiSignalType",
            "signal_type",
            "uiTipoSenal",
            "tipo_senal",
        ],
    }
        
    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        program_name: str,
        on_sample: Optional[Callable[[dict], None]] = None,
        period_s: float = 0.1,
    ) -> None:
        self.url = url
        self.user = user
        self.password = password
        self.period_s = period_s
        self.program_name = program_name
        self.on_sample = on_sample
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        self._opc = CtrlxOpcUaClient(url=url, user=user, password=password)

    @staticmethod
    def _variant_type_name(node) -> str:
        try:
            return ua.VariantType(node.get_data_type_as_variant_type()).name
        except Exception:
            return "UNKNOWN"
    
    @staticmethod
    def _normalize_name(name: str) -> str:
        return (
            (name or "")
            .strip()
            .lower()
            .replace(" ", "")
            .replace("-", "")
        )
    
    @classmethod
    def _pick_value(cls, raw_values: dict, aliases: list[str]):
        normalized_map = {
            cls._normalize_name(k): v
            for k, v in raw_values.items()
        }

        for alias in aliases:
            key = cls._normalize_name(alias)
            if key in normalized_map:
                return normalized_map[key]

        return None

    def _build_sample(self, plc_prg_node) -> dict:
        children = plc_prg_node.get_children()

        raw_values = {}
        for child in children:
            name = child.get_browse_name().Name
            try:
                raw_values[name] = self._opc.read_value(child)
            except Exception as exc:
                raw_values[name] = f"READ_ERROR: {exc}"

        sample = {
            "timestamp": time.time(),
            "time": self._pick_value(raw_values, self.SIGNAL_ALIASES["time"]),
            "actuator": self._pick_value(raw_values, self.SIGNAL_ALIASES["actuator"]),
            "sensor": self._pick_value(raw_values, self.SIGNAL_ALIASES["sensor"]),
            "setpoint": self._pick_value(raw_values, self.SIGNAL_ALIASES["setpoint"]),
            "signal_type": self._pick_value(raw_values, self.SIGNAL_ALIASES["signal_type"]),
            "raw": raw_values,
        }

        return sample

    def _resolve_program_node(self):
        root = self._opc.get_root_node()

        program_node = self._opc.browse_by_names(
            root,
            "Objects",
            "Datalayer",
            "plc",
            "app",
            "Application",
            "sym",
            self.program_name,
        )
        return program_node

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