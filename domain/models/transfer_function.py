from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TransferFunctionModel:
    model_type: str
    gain: float
    tau: float | None = None
    tau1: float | None = None
    tau2: float | None = None
    dead_time: float = 0.0
    numerator: list[float] = field(default_factory=list)
    denominator: list[float] = field(default_factory=list)
    tf_string: str = ""