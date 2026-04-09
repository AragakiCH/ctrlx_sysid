from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from domain.models.pid import PIDTuning
from domain.models.transfer_function import TransferFunctionModel


@dataclass
class IdentificationResult:
    model: TransferFunctionModel
    fit_quality: float
    simulated: list[float] = field(default_factory=list)
    pid_tunings: list[PIDTuning] = field(default_factory=list)
    message: Optional[str] = None