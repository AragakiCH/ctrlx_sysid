from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PIDTuning:
    method: str
    kp: float
    ki: float
    kd: float