from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PIDTuning:
    """
    Sintonía PID.

    kp/ki/kd  -> forma paralela:  u = Kp*e + Ki*∫e + Kd*de/dt
    ti/td     -> forma estándar:  u = Kp*(e + (1/Ti)*∫e + Td*de/dt)

    El bloque PID de ctrlX (AXCS) se parametriza con Kp, Ti y Td,
    por eso se exponen ambas formas.
    """

    method: str
    kp: float
    ki: float
    kd: float
    ti: Optional[float] = None
    td: Optional[float] = None
    lambda_c: Optional[float] = None
    description: str = ""

    @classmethod
    def from_standard(
        cls,
        method: str,
        kp: float,
        ti: float,
        td: float = 0.0,
        lambda_c: Optional[float] = None,
        description: str = "",
    ) -> "PIDTuning":
        """Construye la sintonía desde Kp, Ti, Td y deriva Ki y Kd."""
        ki = kp / ti if ti and ti > 1e-9 else 0.0
        kd = kp * td if td else 0.0

        return cls(
            method=method,
            kp=kp,
            ki=ki,
            kd=kd,
            ti=ti,
            td=td,
            lambda_c=lambda_c,
            description=description,
        )
