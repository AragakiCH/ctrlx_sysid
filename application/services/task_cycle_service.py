from __future__ import annotations

"""
Ciclo de tarea del PLC visto desde la app.

El `Intervalo` del MainTask vive en la **configuración de tareas** del proyecto
de ctrlX PLC Engineering. No es una variable del programa, así que no aparece
bajo `sym` y no hay ningún nodo OPC UA que escribir: por OPC UA el ciclo de
tarea sencillamente no existe.

Lo que sí se puede es poner una variable puente en PLC_PRG —expuesta en la
Configuración de símbolos— y que código IEC dentro del PLC la lea y ajuste el
intervalo. La app escribe esa variable; el cambio de tarea lo hace el PLC.
Ver `docs/CICLO_DE_TAREA.md` para el ST.

## Por qué el ciclo NO sigue al muestreo en los dos sentidos

Son dos cosas distintas:

* El ciclo de tarea es cada cuánto el PLC **calcula** el proceso.
* El periodo de muestreo es cada cuánto la app **lo mira**.

Mirar más despacio de lo que el PLC calcula es normal y no cuesta nada. Lo que
no funciona es mirar más rápido: por debajo del ciclo de tarea la variable no
ha cambiado todavía y llegan muestras repetidas, que a la identificación le
parecen una señal escalonada.

Por eso la sincronización es **solo hacia abajo**: si pides muestrear a 10 ms y
la tarea está en 20 ms, se baja la tarea a 10 ms. Si pides 500 ms, la tarea se
queda donde está — frenar el PLC no aporta nada y sí degrada el control.

## Por qué hay un suelo

Bajar el ciclo de tarea le da menos tiempo al PLC para terminar su trabajo. Si
no llega, el watchdog lo manda a STOP. El suelo es una barrera para que un cero
de más en el campo del navegador no pare una planta.
"""

from typing import Optional


# Por debajo de esto no se escribe. El watchdog del MainTask suele estar en
# 100 ms: pedir un ciclo de 1 ms deja al PLC sin margen para terminar el ciclo
# y lo manda a STOP. No es un límite del protocolo, es una barrera de seguridad.
MIN_CYCLE_MS = 5.0

# Techo. Más lento que esto ya no es "muestrear despacio", es frenar el proceso.
MAX_CYCLE_MS = 200.0


class TaskCycleService:
    """
    Lee y escribe el ciclo de tarea a través de una variable puente del PLC.

    Sin variable configurada todo queda inactivo y la app funciona igual: es
    una mejora opcional, no un requisito.
    """

    def __init__(self, session_service) -> None:
        self._session = session_service
        self._variable: Optional[str] = None
        self._sync_enabled = False

    # ------------------------------------------------------------------ #
    # Configuración
    # ------------------------------------------------------------------ #

    @property
    def variable(self) -> Optional[str]:
        return self._variable

    @property
    def sync_enabled(self) -> bool:
        return self._sync_enabled

    def configure(
        self,
        variable: Optional[str] = None,
        sync_enabled: Optional[bool] = None,
    ) -> dict:
        """Fija la variable puente y si el muestreo arrastra al ciclo."""
        if variable is not None:
            self._variable = variable.strip() or None

        if sync_enabled is not None:
            self._sync_enabled = bool(sync_enabled)

        # Sin variable no hay a dónde escribir: dejar el interruptor encendido
        # solo serviría para prometer algo que no va a pasar.
        if self._variable is None:
            self._sync_enabled = False

        return self.status()

    # ------------------------------------------------------------------ #
    # Lectura
    # ------------------------------------------------------------------ #

    def read_cycle_ms(self) -> Optional[float]:
        """Ciclo que reporta el PLC, en ms. None si no se puede leer."""
        reader = self._session.reader
        if reader is None or not self._variable:
            return None

        valor = reader.read_variable_value(self._variable)

        try:
            ms = float(valor)
        except (TypeError, ValueError):
            return None

        return ms if ms > 0 else None

    def status(self) -> dict:
        """Todo lo que la vista necesita para decidir qué mostrar."""
        cycle_ms = self.read_cycle_ms()
        writable, reason = self._writable()

        pedido_s = self._session.requested_period_s
        pedido_ms = pedido_s * 1000.0 if pedido_s else None

        # Muestrear por debajo del ciclo devuelve valores repetidos: la
        # variable todavía no cambió cuando se vuelve a leer.
        sobremuestreo = bool(
            cycle_ms and pedido_ms and pedido_ms < cycle_ms * 0.95
        )

        return {
            "variable": self._variable,
            "sync_enabled": self._sync_enabled,
            "cycle_ms": round(cycle_ms, 3) if cycle_ms else None,
            "writable": writable,
            "reason": reason,
            "oversampling": sobremuestreo,
            "min_cycle_ms": MIN_CYCLE_MS,
            "max_cycle_ms": MAX_CYCLE_MS,
        }

    def _writable(self) -> tuple[bool, Optional[str]]:
        if not self._variable:
            return False, "No hay ninguna variable de ciclo configurada."

        reader = self._session.reader
        if reader is None:
            return False, "No hay sesión OPC UA activa."

        ok, motivo = reader.can_write_variable(self._variable)
        return ok, None if ok else motivo

    # ------------------------------------------------------------------ #
    # Escritura
    # ------------------------------------------------------------------ #

    def set_cycle_ms(self, cycle_ms: float) -> dict:
        """
        Escribe el ciclo pedido en la variable puente.

        Lanza `ValueError` si el valor sale de los límites: es preferible
        rechazarlo aquí a mandarle al PLC un número que lo pare.
        """
        try:
            valor = float(cycle_ms)
        except (TypeError, ValueError):
            raise ValueError("El ciclo de tarea debe ser un número.")

        if valor < MIN_CYCLE_MS:
            raise ValueError(
                f"Un ciclo de {valor:g} ms deja al PLC sin margen para terminar "
                f"su trabajo y el watchdog lo mandaría a STOP. El mínimo que "
                f"acepta la app es {MIN_CYCLE_MS:g} ms."
            )

        if valor > MAX_CYCLE_MS:
            raise ValueError(
                f"Un ciclo de {valor:g} ms frenaría el proceso. El máximo que "
                f"acepta la app es {MAX_CYCLE_MS:g} ms. Para muestrear más "
                f"despacio no hace falta tocar el PLC: basta subir el tiempo "
                f"de muestreo."
            )

        if not self._variable:
            raise ValueError("No hay ninguna variable de ciclo configurada.")

        reader = self._session.reader
        if reader is None:
            raise ValueError("No hay sesión OPC UA activa.")

        ok, motivo = reader.can_write_variable(self._variable)
        if not ok:
            raise ValueError(motivo)

        reader.write_variable_value(self._variable, valor)

        return self.status()

    def sync_with_period(self, period_s: float) -> Optional[dict]:
        """
        Ajusta el ciclo al cambiar el tiempo de muestreo, si toca.

        Devuelve el resultado de la escritura, o None si no había nada que
        hacer. **Solo baja el ciclo**, nunca lo sube: muestrear más despacio
        que el PLC es normal y no justifica frenarlo.
        """
        if not self._sync_enabled or not self._variable:
            return None

        pedido_ms = float(period_s) * 1000.0
        actual_ms = self.read_cycle_ms()

        # Ya calcula al menos tan rápido como vamos a mirar: nada que hacer.
        if actual_ms is not None and actual_ms <= pedido_ms * 1.05:
            return None

        objetivo = max(pedido_ms, MIN_CYCLE_MS)
        if objetivo > MAX_CYCLE_MS:
            return None

        try:
            return self.set_cycle_ms(objetivo)
        except ValueError as exc:
            return {"error": str(exc), **self.status()}
