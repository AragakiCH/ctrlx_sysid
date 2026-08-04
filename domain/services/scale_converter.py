from __future__ import annotations

"""
Conversión entre las escalas físicas con las que puede venir una señal.

Todo el backend trabaja internamente en **porcentaje de span (0-100 %)**: es la
única escala en la que un modelo FOPDT/SOPDT tiene ganancia adimensional y las
sintonías PID son comparables entre lazos. Cada rol (actuador, sensor, setpoint)
declara en qué escala física está su variable del PLC y aquí se traduce.

No se toca el PLC: la variable se lee tal cual y la conversión ocurre en memoria.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Scale:
    """Una escala lineal definida por su valor mínimo y máximo (span)."""

    key: str
    label: str
    unit: str
    minimum: float
    maximum: float

    @property
    def span(self) -> float:
        return self.maximum - self.minimum

    def to_percent(self, value: float) -> float:
        """Valor en unidades de ingeniería -> 0-100 %."""
        if self.span == 0:
            raise ValueError(f"La escala '{self.key}' tiene span cero.")
        return ((float(value) - self.minimum) / self.span) * 100.0

    def from_percent(self, percent: float) -> float:
        """0-100 % -> valor en unidades de ingeniería."""
        return self.minimum + (float(percent) / 100.0) * self.span

    def clamp(self, value: float) -> float:
        return max(self.minimum, min(self.maximum, float(value)))

    def contains(self, value: float) -> bool:
        return self.minimum <= float(value) <= self.maximum

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "min": self.minimum,
            "max": self.maximum,
            "span": self.span,
        }


# Las claves coinciden con los <option value> de los combos "Tipo de señal"
# de la vista (typeAct / typeSen / typeSP), para que no haya traducción por medio.
SCALES: dict[str, Scale] = {
    "ma": Scale(key="ma", label="4-20 mA", unit="mA", minimum=4.0, maximum=20.0),
    "pct": Scale(key="pct", label="0-100 %", unit="%", minimum=0.0, maximum=100.0),
    "v": Scale(key="v", label="0-10 V", unit="V", minimum=0.0, maximum=10.0),
}

DEFAULT_SCALE_KEY = "ma"

# Alias tolerados para no romper si la vista o un cliente manda otra forma.
SCALE_ALIASES: dict[str, str] = {
    "ma": "ma",
    "4-20ma": "ma",
    "4_20ma": "ma",
    "current": "ma",
    "corriente": "ma",
    "pct": "pct",
    "percent": "pct",
    "porcentaje": "pct",
    "%": "pct",
    "0-100": "pct",
    "v": "v",
    "volt": "v",
    "volts": "v",
    "voltios": "v",
    "0-10v": "v",
}


def normalize_scale_key(key: Optional[str]) -> str:
    """Devuelve una clave de escala válida, cayendo al default si no se reconoce."""
    if not isinstance(key, str):
        return DEFAULT_SCALE_KEY

    cleaned = key.strip().lower().replace(" ", "")
    return SCALE_ALIASES.get(cleaned, DEFAULT_SCALE_KEY)


def get_scale(key: Optional[str]) -> Scale:
    return SCALES[normalize_scale_key(key)]


def to_percent(value, scale_key: Optional[str]) -> Optional[float]:
    """Convierte a % de span. Devuelve None si el valor no es numérico."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return get_scale(scale_key).to_percent(float(value))


def from_percent(percent, scale_key: Optional[str]) -> Optional[float]:
    """Convierte de % de span a unidades de ingeniería."""
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        return None
    return get_scale(scale_key).from_percent(float(percent))


def convert(value, from_key: Optional[str], to_key: Optional[str]) -> Optional[float]:
    """Convierte un valor entre dos escalas cualesquiera, pasando por %."""
    percent = to_percent(value, from_key)
    if percent is None:
        return None
    return from_percent(percent, to_key)


def list_scales() -> list[dict]:
    """Catálogo para poblar los combos de la vista."""
    return [scale.to_dict() for scale in SCALES.values()]
