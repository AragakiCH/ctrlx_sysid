from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RealtimeSample:
    timestamp: float
    time: float
    actuator: float
    sensor: float
    setpoint: Optional[float]
    signal_type: int


@dataclass
class SignalSeries:
    time: list[float]
    actuator: list[float]
    sensor: list[float]
    setpoint: list[float]
    signal_type: int | None = None