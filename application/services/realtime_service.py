from __future__ import annotations

from collections import deque
from typing import Deque, Optional

from domain.models.signals import SignalSeries


class RealtimeService:
    def __init__(self, max_buffer_size: int = 1000) -> None:
        self.max_buffer_size = max_buffer_size
        self._buffer: Deque[dict] = deque(maxlen=max_buffer_size)

    @staticmethod
    def ma_to_percent(value: float) -> float:
        return ((value - 4.0) / 16.0) * 100.0

    @staticmethod
    def percent_to_ma(value: float) -> float:
        return 4.0 + (value / 100.0) * 16.0

    def normalize_sample(self, sample: dict) -> dict:
        if not isinstance(sample, dict):
            return sample

        normalized = dict(sample)

        signal_type = normalized.get("signal_type")
        actuator = normalized.get("actuator")
        sensor = normalized.get("sensor")
        setpoint = normalized.get("setpoint")

        if signal_type == 1:
            if isinstance(actuator, (int, float)):
                normalized["actuator_pct"] = self.ma_to_percent(float(actuator))
            else:
                normalized["actuator_pct"] = None

            if isinstance(sensor, (int, float)):
                normalized["sensor_pct"] = self.ma_to_percent(float(sensor))
            else:
                normalized["sensor_pct"] = None

            if isinstance(setpoint, (int, float)):
                normalized["setpoint_pct"] = self.ma_to_percent(float(setpoint))
            else:
                normalized["setpoint_pct"] = None

        else:
            normalized["actuator_pct"] = actuator if isinstance(actuator, (int, float)) else None
            normalized["sensor_pct"] = sensor if isinstance(sensor, (int, float)) else None
            normalized["setpoint_pct"] = setpoint if isinstance(setpoint, (int, float)) else None

        return normalized

    def add_sample(self, sample: dict) -> None:
        if not isinstance(sample, dict):
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