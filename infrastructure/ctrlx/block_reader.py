from __future__ import annotations

"""
Lectura del búfer circular del PLC.

Por qué existe
--------------
Sondear variables por OPC UA **no puede garantizar** capturar todas las muestras
a ritmo de tarea. La tarea IEC corre con su reloj (20 ms) y el lector sondea con
otro independiente: sin sincronización entre ambos, inevitablemente se lee dos
veces el mismo ciclo y se pierde otro. Muestrear más rápido baja la probabilidad,
no la elimina. Y ninguna lectura dice *cuándo* calculó el PLC ese valor.

La solución es invertir quién muestrea. El programa PLC escribe cada ciclo en un
ARRAY circular; la app lee ese array en bloque cada pocos segundos y reconstruye
la serie. El muestreo lo hace la propia tarea, así que el periodo es exactamente
el de la tarea, sin jitter y sin huecos. Un array completo se lee en UN viaje de
red, así que traer 1000 muestras cuesta lo mismo que traer una.

Detección de pérdida
--------------------
Si la app tarda más en leer de lo que el PLC tarda en dar una vuelta completa al
array, hay muestras sobrescritas. Eso se detecta comparando el contador de
escrituras total (`nWriteCount`) contra lo ya consumido, y se reporta como
`overrun` en vez de entregar una serie con huecos invisibles: una serie a la que
le faltan tramos en silencio produce un modelo que parece válido y no lo es.

El contrato con el PLC está en `docs/plc_ring_buffer.st`.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class BlockMapping:
    """Nombres de las variables del búfer circular en el programa PLC."""

    # Arrays paralelos, uno por señal. `time` es opcional: si el PLC no lo
    # publica, se reconstruye a partir del periodo de tarea.
    actuator: str = "arrActuator"
    sensor: str = "arrSensor"
    setpoint: Optional[str] = "arrSetPoint"
    time: Optional[str] = "arrTimeSec"

    # Escalares de control.
    write_count: str = "nWriteCount"   # muestras escritas desde el arranque
    period: Optional[str] = "rTaskPeriodSec"  # periodo real de la tarea, en s

    def signal_names(self) -> list[str]:
        return [n for n in (self.time, self.actuator, self.sensor, self.setpoint) if n]


@dataclass
class BlockReadResult:
    """Lo extraído en una pasada."""

    samples: list[dict] = field(default_factory=list)
    overrun: bool = False
    lost: int = 0
    capacity: int = 0
    write_count: int = 0
    period_s: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "count": len(self.samples),
            "overrun": self.overrun,
            "lost": self.lost,
            "capacity": self.capacity,
            "write_count": self.write_count,
            "period_s": self.period_s,
        }


class RingBufferReader:
    """
    Reconstruye la serie a partir del búfer circular del PLC.

    Es un objeto con estado: recuerda cuántas muestras lleva consumidas para
    entregar solo las nuevas en cada pasada.
    """

    def __init__(
        self,
        mapping: Optional[BlockMapping] = None,
        default_period_s: float = 0.02,
    ) -> None:
        self.mapping = mapping or BlockMapping()
        self.default_period_s = default_period_s

        # Muestras totales que el PLC ya había escrito la última vez que se
        # leyó. Es un contador absoluto, no un índice: así se distingue "no
        # pasó nada" de "dio una vuelta entera y volvió al mismo índice".
        self._consumed: int = 0
        self._primed = False

    def reset(self) -> None:
        self._consumed = 0
        self._primed = False

    # ------------------------------------------------------------------ #

    @staticmethod
    def _as_list(value: Any) -> list:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    def extract(self, raw: dict) -> BlockReadResult:
        """
        Convierte una lectura cruda del PLC en las muestras nuevas.

        `raw` es el diccionario nombre -> valor tal como vuelve del servidor;
        los arrays llegan como listas.
        """
        m = self.mapping

        actuador = self._as_list(raw.get(m.actuator))
        sensor = self._as_list(raw.get(m.sensor))

        capacidad = min(len(actuador), len(sensor))
        if capacidad == 0:
            return BlockReadResult(capacity=0)

        setpoint = self._as_list(raw.get(m.setpoint)) if m.setpoint else []
        tiempo = self._as_list(raw.get(m.time)) if m.time else []

        periodo = raw.get(m.period) if m.period else None
        periodo = (
            float(periodo)
            if isinstance(periodo, (int, float)) and periodo > 0
            else self.default_period_s
        )

        escritas = raw.get(m.write_count)
        if not isinstance(escritas, (int, float)):
            return BlockReadResult(capacity=capacidad, period_s=periodo)
        escritas = int(escritas)

        # Primera pasada: no se arrastra el histórico que ya estaba en el array
        # antes de conectarse. Se empieza a contar desde aquí.
        if not self._primed:
            self._primed = True
            self._consumed = escritas
            return BlockReadResult(
                capacity=capacidad, write_count=escritas, period_s=periodo
            )

        nuevas = escritas - self._consumed

        if nuevas <= 0:
            # El PLC no avanzó (o se reinició: en ese caso se resincroniza).
            if escritas < self._consumed:
                self._consumed = escritas
            return BlockReadResult(
                capacity=capacidad, write_count=escritas, period_s=periodo
            )

        overrun = nuevas > capacidad
        perdidas = nuevas - capacidad if overrun else 0

        # Con overrun, lo más viejo ya se sobrescribió: solo se puede recuperar
        # una vuelta completa. Se reporta cuántas se perdieron.
        a_leer = min(nuevas, capacidad)
        primera = escritas - a_leer

        muestras: list[dict] = []

        for k in range(a_leer):
            indice_absoluto = primera + k
            i = indice_absoluto % capacidad

            muestra = {
                "actuator": actuador[i],
                "sensor": sensor[i],
                "setpoint": setpoint[i] if i < len(setpoint) else None,
                # El tiempo lo pone el PLC si lo publica; si no, se deriva del
                # índice absoluto y el periodo de tarea, que es exacto por
                # construcción y siempre monótono.
                "time": (
                    float(tiempo[i])
                    if i < len(tiempo) and isinstance(tiempo[i], (int, float))
                    else round(indice_absoluto * periodo, 6)
                ),
                "sample_index": indice_absoluto,
            }
            muestras.append(muestra)

        self._consumed = escritas

        return BlockReadResult(
            samples=muestras,
            overrun=overrun,
            lost=perdidas,
            capacity=capacidad,
            write_count=escritas,
            period_s=periodo,
        )

    # ------------------------------------------------------------------ #

    def max_poll_interval_s(self, capacity: int, period_s: float) -> float:
        """
        Cada cuánto hay que leer, como muy tarde, para no perder nada.

        Se deja la mitad del array de margen: leer justo cuando está a punto de
        dar la vuelta no tolera ni un hipo de red.
        """
        if capacity <= 0 or period_s <= 0:
            return 0.0
        return (capacity * period_s) / 2.0
