from __future__ import annotations

from collections import deque
from typing import Callable, Deque, Optional

from domain.models.signals import SignalSeries
from domain.services.scale_converter import DEFAULT_SCALE_KEY
from domain.services.scale_converter import to_percent as scale_to_percent

SCALED_ROLES = ("actuator", "sensor", "setpoint")


class RealtimeService:
    """
    Buffer circular de muestras del PLC.

    Cada muestra se guarda dos veces: el valor **crudo** tal como está en el PLC
    (`actuator`, `sensor`, `setpoint`) y su equivalente en **% de span**
    (`actuator_pct`, ...). La identificación siempre corre sobre el % para que la
    ganancia del modelo sea adimensional.

    La escala de cada rol la decide `TestConfigService` (lo que el usuario eligió
    en los combos "Tipo de señal"). Si no se inyecta ninguno, se cae a 4-20 mA
    cuando la variable `uiSignalType` del PLC vale 1, que era el comportamiento
    anterior.
    """

    def __init__(
        self,
        max_buffer_size: int = 1000,
        scale_provider: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.max_buffer_size = max_buffer_size
        self._buffer: Deque[dict] = deque(maxlen=max_buffer_size)
        self._scale_provider = scale_provider

        # Fuente actual del buffer. En "imported" las muestras del PLC se
        # ignoran: un buffer mezcla de archivo y tiempo real no describe
        # ningún ensayo y la identificación saldría de una serie quimérica.
        self._source: str = "realtime"
        self._import_info: Optional[dict] = None

    # ------------------------------------------------------------------ #
    # Fuente de datos (tiempo real vs importado)
    # ------------------------------------------------------------------ #

    @property
    def source(self) -> str:
        return self._source

    @property
    def import_info(self) -> Optional[dict]:
        return self._import_info

    def load_imported(self, samples: list[dict], info: dict) -> int:
        """
        Reemplaza el buffer por muestras de archivo y activa el modo importado.

        Si el archivo trae más muestras que `max_buffer_size`, el deque
        descartaría el INICIO (donde suele estar la línea base del escalón).
        Por eso se recrea el buffer con capacidad suficiente.
        """
        if len(samples) > self._buffer.maxlen:
            self._buffer = deque(maxlen=len(samples))

        self._buffer.clear()
        for sample in samples:
            self._buffer.append(self.normalize_sample(sample))

        self._source = "imported"
        self._import_info = dict(info)

        return len(samples)

    def restore_realtime(self) -> None:
        """Vuelve al flujo del PLC. Limpia el buffer: eran datos de archivo."""
        self._buffer = deque(maxlen=self.max_buffer_size)
        self._source = "realtime"
        self._import_info = None

    @property
    def accepts_realtime(self) -> bool:
        return self._source == "realtime"

    def set_scale_provider(self, provider: Optional[Callable[[str], str]]) -> None:
        """Inyecta de dónde sacar la escala de cada rol (normalmente TestConfigService)."""
        self._scale_provider = provider

    @staticmethod
    def ma_to_percent(value: float) -> float:
        return ((value - 4.0) / 16.0) * 100.0

    @staticmethod
    def percent_to_ma(value: float) -> float:
        return 4.0 + (value / 100.0) * 16.0

    def _scale_for(self, role: str, signal_type) -> str:
        if self._scale_provider is not None:
            try:
                return self._scale_provider(role)
            except Exception:
                pass

        # Compatibilidad: sin proveedor, manda la variable del PLC.
        return "ma" if signal_type == 1 else "pct"

    def normalize_sample(self, sample: dict) -> dict:
        if not isinstance(sample, dict):
            return sample

        normalized = dict(sample)
        signal_type = normalized.get("signal_type")

        scales: dict[str, str] = {}

        for role in SCALED_ROLES:
            scale_key = self._scale_for(role, signal_type) or DEFAULT_SCALE_KEY
            scales[role] = scale_key
            normalized[f"{role}_pct"] = scale_to_percent(normalized.get(role), scale_key)

        # La vista usa esto para rotular los ejes sin adivinar la unidad.
        normalized["scales"] = scales

        return normalized

    def recompute_percent(self) -> int:
        """
        Recalcula los campos `_pct` de las muestras que ya están en el buffer.

        `normalize_sample` corre al insertar, así que sin esto un cambio de
        escala solo afectaría a las muestras nuevas: el ensayo que ya se capturó
        seguiría interpretado con la escala vieja y la identificación daría
        números que no corresponden a lo que muestra la vista.

        Devuelve cuántas muestras se recalcularon.
        """
        samples = list(self._buffer)
        self._buffer.clear()

        for sample in samples:
            self._buffer.append(self.normalize_sample(sample))

        return len(samples)

    def add_sample(self, sample: dict) -> None:
        if not isinstance(sample, dict):
            return

        # En modo importado el buffer pertenece al archivo: una muestra del
        # PLC colada al final desalinearía el tiempo y contaminaría la serie.
        if self._source != "realtime":
            return

        normalized_sample = self.normalize_sample(sample)
        self._buffer.append(normalized_sample)

    def get_latest_sample(self) -> Optional[dict]:
        if not self._buffer:
            return None
        return self._buffer[-1]

    def get_buffer_size(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()

    def get_all_samples(self) -> list[dict]:
        return list(self._buffer)

    def get_series_payload(self, use_percent: bool = False) -> dict:
        samples = list(self._buffer)

        actuator_key = "actuator_pct" if use_percent else "actuator"
        sensor_key = "sensor_pct" if use_percent else "sensor"
        setpoint_key = "setpoint_pct" if use_percent else "setpoint"
        unit = "%" if use_percent else "raw"

        return {
            "time": [s.get("time") for s in samples],
            "actuator": [s.get(actuator_key) for s in samples],
            "sensor": [s.get(sensor_key) for s in samples],
            "setpoint": [s.get(setpoint_key) for s in samples],
            "signal_type": samples[-1].get("signal_type") if samples else None,
            "count": len(samples),
            "unit": unit,
        }

    def get_signal_series(self, use_percent: bool = False) -> SignalSeries:
        samples = list(self._buffer)

        if use_percent:
            actuator_key = "actuator_pct"
            sensor_key = "sensor_pct"
            setpoint_key = "setpoint_pct"
        else:
            actuator_key = "actuator"
            sensor_key = "sensor"
            setpoint_key = "setpoint"

        clean_time = []
        clean_actuator = []
        clean_sensor = []
        clean_setpoint = []

        for s in samples:
            t = s.get("time")
            a = s.get(actuator_key)
            y = s.get(sensor_key)
            sp = s.get(setpoint_key)

            if (
                isinstance(t, (int, float))
                and isinstance(a, (int, float))
                and isinstance(y, (int, float))
                and isinstance(sp, (int, float))
            ):
                clean_time.append(float(t))
                clean_actuator.append(float(a))
                clean_sensor.append(float(y))
                clean_setpoint.append(float(sp))

        signal_type = samples[-1].get("signal_type") if samples else None

        return SignalSeries(
            time=clean_time,
            actuator=clean_actuator,
            sensor=clean_sensor,
            setpoint=clean_setpoint,
            signal_type=signal_type,
        )

    def has_enough_samples(self, min_samples: int = 20) -> bool:
        return len(self._buffer) >= min_samples

    def has_dynamic_signal(self, min_delta: float = 0.5, use_percent: bool = False) -> bool:
        samples = list(self._buffer)
        if len(samples) < 2:
            return False

        actuator_key = "actuator_pct" if use_percent else "actuator"
        sensor_key = "sensor_pct" if use_percent else "sensor"

        actuator_values = [
            s.get(actuator_key) for s in samples if isinstance(s.get(actuator_key), (int, float))
        ]
        sensor_values = [
            s.get(sensor_key) for s in samples if isinstance(s.get(sensor_key), (int, float))
        ]

        if len(actuator_values) < 2 or len(sensor_values) < 2:
            return False

        actuator_delta = max(actuator_values) - min(actuator_values)
        sensor_delta = max(sensor_values) - min(sensor_values)

        return actuator_delta >= min_delta or sensor_delta >= min_delta