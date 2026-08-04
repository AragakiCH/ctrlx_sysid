from __future__ import annotations

"""
Configuración del ensayo de lazo abierto (paso 2 de la vista) y de la escala
física de cada señal (paso 1).

Este servicio es la única fuente de verdad sobre:

* En qué escala está cada rol (`actuator`, `sensor`, `setpoint`): 4-20 mA,
  0-100 % o 0-10 V. Con eso el `RealtimeService` puede llenar los campos `_pct`
  sin depender de la variable `uiSignalType` del PLC.
* Cómo es el escalón que el operador va a aplicar: valor inicial, valor final,
  retardo antes del salto, duración total y orden de modelo deseado.

**No escribe nada en el PLC.** El escalón lo aplica el operador desde el ctrlX;
aquí solo se guardan los números para poder dimensionar la ventana de
identificación, fijar el umbral de detección y mostrar la vista previa.
"""

import threading
from typing import Optional

from domain.services.scale_converter import (
    DEFAULT_SCALE_KEY,
    get_scale,
    list_scales,
    normalize_scale_key,
    to_percent,
)

SCALED_ROLES = ("actuator", "sensor", "setpoint")

ORDER_CHOICES = ("auto", "fopdt", "sopdt", "integrating")

# Las opciones del <select id="orderSel"> de la vista usan números.
ORDER_ALIASES = {
    "auto": "auto",
    "1": "fopdt",
    "fopdt": "fopdt",
    "2": "sopdt",
    "sopdt": "sopdt",
    "0": "integrating",
    "integrating": "integrating",
    "integrador": "integrating",
    "integrante": "integrating",
}


def normalize_order(order: Optional[str]) -> str:
    if not isinstance(order, str):
        return "auto"
    return ORDER_ALIASES.get(order.strip().lower(), "auto")


class TestConfigService:
    """Estado compartido del ensayo. Seguro para el hilo del PLCReader."""

    # El nombre empieza por "Test": sin esto pytest intenta recolectarla como
    # clase de tests cuando se importa en un módulo de pruebas.
    __test__ = False

    # Mínimos para que la identificación tenga sentido numérico.
    MIN_POST_SAMPLES = 30
    MIN_PRE_SAMPLES = 5

    def __init__(self, sample_period_s: float = 0.2) -> None:
        self._lock = threading.RLock()

        self._sample_period_s = float(sample_period_s)

        self._scales: dict[str, str] = {role: DEFAULT_SCALE_KEY for role in SCALED_ROLES}

        # Escalón por defecto: 25 % -> 50 % en una escala de 4-20 mA.
        self._step_from = 8.0
        self._step_to = 12.0
        self._duration_s = 120.0
        self._delay_s = 10.0
        self._order = "auto"

    # ------------------------------------------------------------------ #
    # Escalas por rol
    # ------------------------------------------------------------------ #

    def get_scales(self) -> dict[str, str]:
        with self._lock:
            return dict(self._scales)

    def scale_for(self, role: str) -> str:
        with self._lock:
            return self._scales.get(role, DEFAULT_SCALE_KEY)

    def set_scales(self, scales: Optional[dict]) -> dict:
        """Fija la escala de cada rol. Los roles ausentes conservan su valor."""
        with self._lock:
            for role in SCALED_ROLES:
                value = (scales or {}).get(role)
                if value is not None:
                    self._scales[role] = normalize_scale_key(value)

            return self.describe_scales()

    def describe_scales(self) -> dict:
        with self._lock:
            return {
                "scales": dict(self._scales),
                "detail": {
                    role: get_scale(key).to_dict() for role, key in self._scales.items()
                },
                "available": list_scales(),
            }

    # ------------------------------------------------------------------ #
    # Configuración del ensayo
    # ------------------------------------------------------------------ #

    def set_step_config(
        self,
        step_from: Optional[float] = None,
        step_to: Optional[float] = None,
        duration_s: Optional[float] = None,
        delay_s: Optional[float] = None,
        order: Optional[str] = None,
        sample_period_s: Optional[float] = None,
    ) -> dict:
        with self._lock:
            new_from = self._step_from if step_from is None else float(step_from)
            new_to = self._step_to if step_to is None else float(step_to)
            new_duration = self._duration_s if duration_s is None else float(duration_s)
            new_delay = self._delay_s if delay_s is None else float(delay_s)
            new_order = self._order if order is None else normalize_order(order)
            new_period = (
                self._sample_period_s
                if sample_period_s is None
                else float(sample_period_s)
            )

            self._validate(
                step_from=new_from,
                step_to=new_to,
                duration_s=new_duration,
                delay_s=new_delay,
                sample_period_s=new_period,
            )

            self._step_from = new_from
            self._step_to = new_to
            self._duration_s = new_duration
            self._delay_s = new_delay
            self._order = new_order
            self._sample_period_s = new_period

            return self.describe_step_config()

    def _validate(
        self,
        step_from: float,
        step_to: float,
        duration_s: float,
        delay_s: float,
        sample_period_s: float,
    ) -> None:
        scale = get_scale(self._scales["actuator"])

        if sample_period_s <= 0:
            raise ValueError("El tiempo de muestreo debe ser mayor que cero.")

        if duration_s <= 0:
            raise ValueError("La duración total debe ser mayor que cero.")

        if delay_s < 0:
            raise ValueError("El retardo antes del escalón no puede ser negativo.")

        if delay_s >= duration_s:
            raise ValueError(
                "El retardo debe ser menor que la duración total: si no, no queda "
                "respuesta después del escalón para identificar."
            )

        if not scale.contains(step_from):
            raise ValueError(
                f"El valor inicial {step_from} está fuera de la escala "
                f"{scale.label} ({scale.minimum}–{scale.maximum} {scale.unit})."
            )

        if not scale.contains(step_to):
            raise ValueError(
                f"El valor final {step_to} está fuera de la escala "
                f"{scale.label} ({scale.minimum}–{scale.maximum} {scale.unit})."
            )

        if abs(step_to - step_from) < 1e-9:
            raise ValueError(
                "El valor inicial y el final son iguales: no hay escalón que identificar."
            )

        post_samples = int((duration_s - delay_s) / sample_period_s)
        if post_samples < self.MIN_POST_SAMPLES:
            raise ValueError(
                f"Con duración {duration_s} s, retardo {delay_s} s y muestreo "
                f"{sample_period_s} s solo quedan {post_samples} muestras después del "
                f"escalón. Se necesitan al menos {self.MIN_POST_SAMPLES}."
            )

    # ------------------------------------------------------------------ #
    # Derivados
    # ------------------------------------------------------------------ #

    def describe_step_config(self) -> dict:
        """Config + todo lo que la vista necesita calculado por el backend."""
        with self._lock:
            scale_key = self._scales["actuator"]
            scale = get_scale(scale_key)

            from_pct = scale.to_percent(self._step_from)
            to_pct = scale.to_percent(self._step_to)

            delta = self._step_to - self._step_from
            delta_pct = to_pct - from_pct

            return {
                "step_from": self._step_from,
                "step_to": self._step_to,
                "duration_s": self._duration_s,
                "delay_s": self._delay_s,
                "order": self._order,
                "sample_period_s": self._sample_period_s,
                "actuator_scale": scale.to_dict(),
                "derived": {
                    "step_from_pct": from_pct,
                    "step_to_pct": to_pct,
                    "delta": delta,
                    "delta_pct": delta_pct,
                    "direction": "up" if delta > 0 else "down",
                    "step_threshold_pct": self.step_threshold_pct(),
                    "pre_samples": self.pre_samples(),
                    "post_samples": self.post_samples(),
                    "expected_samples": self.expected_samples(),
                    "step_at_s": self._delay_s,
                },
            }

    def step_threshold_pct(self) -> float:
        """
        Umbral para considerar que hubo escalón, en % de span.

        Se usa la mitad del salto esperado: suficiente para no dispararse con
        ruido, y tolerante si el actuador no alcanza exactamente el valor final.
        """
        with self._lock:
            scale = get_scale(self._scales["actuator"])
            delta_pct = abs(
                scale.to_percent(self._step_to) - scale.to_percent(self._step_from)
            )
            return max(delta_pct * 0.5, 1.0)

    def pre_samples(self) -> int:
        """Muestras a conservar antes del escalón (línea base)."""
        with self._lock:
            n = int(self._delay_s / self._sample_period_s)
            return max(self.MIN_PRE_SAMPLES, min(n, 200))

    def post_samples(self) -> int:
        """Muestras de respuesta a capturar después del escalón."""
        with self._lock:
            n = int((self._duration_s - self._delay_s) / self._sample_period_s)
            return max(self.MIN_POST_SAMPLES, n)

    def expected_samples(self) -> int:
        with self._lock:
            return int(self._duration_s / self._sample_period_s)

    def get_order(self) -> str:
        with self._lock:
            return self._order

    # ------------------------------------------------------------------ #
    # Vista previa del escalón
    # ------------------------------------------------------------------ #

    def build_preview(self, points: int = 200, scale_key: Optional[str] = None) -> dict:
        """
        Serie ideal del escalón para dibujar la vista previa del paso 2.

        `scale_key` permite pedirla en otra escala (por ejemplo en % aunque el
        actuador esté en mA) sin cambiar la configuración guardada.
        """
        with self._lock:
            source = get_scale(self._scales["actuator"])
            target = get_scale(scale_key) if scale_key else source

            n = max(2, min(int(points), 2000))

            from_value = target.from_percent(source.to_percent(self._step_from))
            to_value = target.from_percent(source.to_percent(self._step_to))

            time_axis: list[float] = []
            values: list[float] = []

            for i in range(n):
                t = self._duration_s * i / (n - 1)
                time_axis.append(t)
                values.append(from_value if t < self._delay_s else to_value)

            return {
                "time": time_axis,
                "actuator": values,
                "unit": target.unit,
                "scale": target.to_dict(),
                "step_at_s": self._delay_s,
                "duration_s": self._duration_s,
                "from_value": from_value,
                "to_value": to_value,
            }

    # ------------------------------------------------------------------ #
    # Snapshot completo
    # ------------------------------------------------------------------ #

    def describe(self) -> dict:
        return {
            "ok": True,
            **self.describe_scales(),
            "step": self.describe_step_config(),
            "orders": list(ORDER_CHOICES),
        }
