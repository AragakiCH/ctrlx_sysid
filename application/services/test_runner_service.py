from __future__ import annotations

"""
Ejecución del ensayo de lazo abierto.

Este servicio es el que **manda el reloj del ensayo**. Genera el perfil ideal
del actuador a partir de las condiciones guardadas en `TestConfigService` y lo
recorre en su propio hilo, emitiendo un evento por muestra.

Por qué el reloj vive aquí y no en el navegador
-----------------------------------------------
Antes el perfil se calculaba en `tickEnsayo()` con un `setInterval` de 200 ms.
Eso sirve para dibujar, pero no para gobernar un proceso:

* Los navegadores **estrangulan los timers** de las pestañas en segundo plano
  (Chrome los baja a 1 Hz o menos). El escalón se aplicaría tarde, o nunca.
* Si el operador cierra la pestaña, el ensayo queda a medias sin que nadie se
  entere y el actuador se queda en el último valor comandado.
* Con varias pestañas abiertas habría varios relojes compitiendo.

Teniendo el reloj en el backend, el ensayo es uno solo, sobrevive a que se
cierre el navegador y —lo importante para la siguiente fase— es el mismo hilo
que va a escribir en el PLC.

Fase actual
-----------
**No se escribe nada en el PLC todavía.** El runner solo calcula y publica el
valor comandado. El punto de extensión es `writer`: cuando se le inyecte una
función, se llamará en cada tick con el valor a aplicar. Ver `set_writer`.
"""

import threading
import time
from typing import Callable, Optional

from domain.services.scale_converter import get_scale

# Estados posibles del ensayo.
STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"
STATUS_STOPPED = "stopped"
STATUS_ABORTED = "aborted"  # se perdió el control del actuador

# Fase dentro del ensayo.
PHASE_BASELINE = "baseline"  # antes del salto: línea base
PHASE_STEP = "step"  # después del salto


class TestRunnerService:
    """Ejecuta el perfil del actuador y publica su avance."""

    # Evita que pytest intente recolectarla como clase de tests.
    __test__ = False

    # Fallos de escritura SEGUIDOS que se toleran antes de abortar.
    #
    # Un hipo de red no debe cortar un ensayo de dos minutos, pero seguir
    # capturando datos cuando ya no se gobierna el actuador produce un modelo
    # falso: la entrada que el algoritmo cree haber aplicado no es la que vio
    # la planta. El contador se reinicia con cada escritura exitosa.
    MAX_CONSECUTIVE_WRITE_ERRORS = 5

    def __init__(
        self,
        test_config_service,
        on_event: Optional[Callable[[dict], None]] = None,
        writer: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._config = test_config_service
        self._on_event = on_event
        self._writer = writer

        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

        self._status = STATUS_IDLE
        self._plan: Optional[dict] = None
        self._started_at: Optional[float] = None  # time.time(), para la vista
        self._monotonic_start: Optional[float] = None  # base del reloj interno

        self._index = 0
        self._elapsed_s = 0.0
        self._command: Optional[float] = None
        self._command_pct: Optional[float] = None
        self._phase: Optional[str] = None
        self._write_errors = 0
        self._consecutive_write_errors = 0
        self._last_write_error: Optional[str] = None
        self._abort_reason: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Punto de extensión para la fase 2
    # ------------------------------------------------------------------ #

    def set_writer(self, writer: Optional[Callable[[float], None]]) -> None:
        """
        Inyecta la función que escribe el valor comandado en el PLC.

        Se llama en cada tick con el valor **en la escala del actuador** (la
        misma en la que el usuario configuró el escalón).

        Un fallo suelto se tolera y se cuenta; a los
        `MAX_CONSECUTIVE_WRITE_ERRORS` seguidos el ensayo se aborta, porque a
        partir de ahí ya no se está gobernando la planta.

        Con `writer=None` el ensayo vuelve a ser solo informativo: calcula y
        publica el valor, pero no toca el PLC.
        """
        with self._lock:
            self._writer = writer

    @property
    def writes_enabled(self) -> bool:
        with self._lock:
            return self._writer is not None

    # ------------------------------------------------------------------ #
    # Perfil
    # ------------------------------------------------------------------ #

    def build_plan(self) -> dict:
        """
        Perfil completo del actuador, muestra a muestra.

        A diferencia de `TestConfigService.build_preview()` —que devuelve N
        puntos repartidos para dibujar— aquí hay **una entrada por muestra
        real** del ensayo, al mismo periodo con el que se va a ejecutar. Es lo
        que la vista usa para pintar la línea objetivo de una sola vez, y lo que
        recorrerá el escritor del PLC en la fase 2.
        """
        config = self._config.describe_step_config()

        period = float(config["sample_period_s"])
        duration = float(config["duration_s"])
        delay = float(config["delay_s"])
        from_value = float(config["step_from"])
        to_value = float(config["step_to"])

        scale = get_scale(config["actuator_scale"]["key"])

        total = max(1, int(round(duration / period)))

        time_axis: list[float] = []
        values: list[float] = []
        values_pct: list[float] = []

        for i in range(total):
            t = i * period
            value = from_value if t < delay else to_value

            time_axis.append(round(t, 6))
            values.append(value)
            values_pct.append(scale.to_percent(value))

        return {
            "time": time_axis,
            "actuator": values,
            "actuator_pct": values_pct,
            "unit": scale.unit,
            "scale": scale.to_dict(),
            "sample_period_s": period,
            "duration_s": duration,
            "step_at_s": delay,
            "from_value": from_value,
            "to_value": to_value,
            "from_value_pct": scale.to_percent(from_value),
            "to_value_pct": scale.to_percent(to_value),
            "samples": total,
        }

    def _value_at(self, elapsed_s: float, plan: dict) -> tuple[float, float, str]:
        """Valor comandado en un instante dado. Devuelve (valor, valor_pct, fase)."""
        if elapsed_s < plan["step_at_s"]:
            return plan["from_value"], plan["from_value_pct"], PHASE_BASELINE
        return plan["to_value"], plan["to_value_pct"], PHASE_STEP

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #

    def start(self) -> dict:
        """Arranca un ensayo. Devuelve el estado inicial junto con el plan."""
        with self._lock:
            if self._status == STATUS_RUNNING:
                raise ValueError(
                    "Ya hay un ensayo en curso. Deténlo antes de arrancar otro."
                )

            # build_plan lee la config; si es inválida, revienta aquí y no
            # llegamos a arrancar el hilo.
            plan = self.build_plan()

            self._plan = plan
            self._status = STATUS_RUNNING
            self._stop_flag.clear()

            self._started_at = time.time()
            self._monotonic_start = time.monotonic()
            self._index = 0
            self._elapsed_s = 0.0
            self._write_errors = 0
            self._consecutive_write_errors = 0
            self._last_write_error = None
            self._abort_reason = None

            value, value_pct, phase = self._value_at(0.0, plan)
            self._command = value
            self._command_pct = value_pct
            self._phase = phase

            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

            state = self._state_locked()

        self._emit("test_started", {**state, "plan": plan})
        return {**state, "plan": plan}

    def stop(self) -> dict:
        """Detiene el ensayo en curso. Idempotente."""
        with self._lock:
            was_running = self._status == STATUS_RUNNING
            self._stop_flag.set()

            if was_running:
                self._status = STATUS_STOPPED

        if was_running:
            # El actuador vuelve a su valor inicial: una parada de emergencia
            # que dejara la planta en el valor del escalón sería peor que no
            # haber arrancado. Y recién después se suelta.
            self._return_to_start()
            self._release_writer()

        with self._lock:
            state = self._state_locked()

        if was_running:
            self._emit("test_stopped", state)

        return state

    def _run(self) -> None:
        """
        Bucle del ensayo.

        El periodo se respeta contra un **deadline acumulado**, no durmiendo el
        periodo completo en cada vuelta: si se durmiera `period` fijo, el coste
        de cada iteración se iría sumando y un ensayo de 120 s terminaría varios
        segundos tarde, desalineando el escalón respecto a las muestras del PLC.
        """
        plan = self._plan
        if plan is None:
            return

        period = plan["sample_period_s"]
        total = plan["samples"]
        deadline = self._monotonic_start or time.monotonic()

        for index in range(total):
            if self._stop_flag.is_set():
                return

            elapsed = time.monotonic() - (self._monotonic_start or time.monotonic())
            value, value_pct, phase = self._value_at(elapsed, plan)

            written, error = self._apply(value)

            with self._lock:
                self._index = index
                self._elapsed_s = elapsed
                self._command = value
                self._command_pct = value_pct
                self._phase = phase

                if error is not None:
                    self._write_errors += 1
                    self._consecutive_write_errors += 1
                    self._last_write_error = error
                elif written:
                    # Una escritura buena limpia la racha: lo que importa es
                    # perder el control de forma sostenida, no un fallo suelto.
                    self._consecutive_write_errors = 0

                abortar = (
                    self._consecutive_write_errors >= self.MAX_CONSECUTIVE_WRITE_ERRORS
                )
                state = self._state_locked()

            self._emit("test_tick", {**state, "written": written})

            if abortar:
                self._abort(
                    f"{self._consecutive_write_errors} escrituras seguidas fallaron. "
                    f"Último error: {self._last_write_error}"
                )
                return

            # Espera hasta el siguiente deadline.
            deadline += period
            remaining = deadline - time.monotonic()

            if remaining > 0:
                # `wait` en vez de `sleep`: así un stop() corta al instante en
                # lugar de esperar a que termine el periodo.
                if self._stop_flag.wait(remaining):
                    return
            else:
                # Se perdió el paso (el tick tardó más que el periodo).
                # Resincronizamos para no arrastrar el retraso al resto.
                deadline = time.monotonic()

        self._return_to_start()
        self._release_writer()

        with self._lock:
            if self._status == STATUS_RUNNING:
                self._status = STATUS_FINISHED
            state = self._state_locked()

        self._emit("test_finished", state)

    def _return_to_start(self) -> str | None:
        """
        Devuelve el actuador a `step_from`.

        Se hace al terminar, al detener y al abortar. Sin esto la planta queda
        en el valor final del escalón: el proceso no vuelve a donde estaba y el
        siguiente ensayo arrancaría desde otro punto de operación, con lo que
        el escalón medido no sería el configurado.

        No lanza: si esta escritura falla ya no queda nada más que intentar.
        Devuelve el mensaje de error, o None si fue bien.
        """
        with self._lock:
            writer = self._writer
            plan = self._plan

        if writer is None or plan is None:
            return None

        value = plan["from_value"]

        try:
            writer(value)
        except Exception as exc:
            error = f"No se pudo devolver el actuador a {value}: {exc}"
            with self._lock:
                self._write_errors += 1
                self._last_write_error = error
            return error

        with self._lock:
            self._command = value
            self._command_pct = plan["from_value_pct"]

        return None

    def _release_writer(self) -> None:
        """
        Suelta el actuador al terminar el ensayo.

        Se llama SIEMPRE después de `_return_to_start()`, nunca antes: primero
        se deja la planta en el valor inicial y recién entonces se deja de
        gobernarla.

        Sin esto el escritor queda armado indefinidamente. La variable del
        actuador suele ser una que también escribe la HMI (`HMI_SP_Local_...`),
        así que mantenerla tomada después del ensayo deja dos dueños para el
        mismo dato y el operador no recupera el control hasta reiniciar.

        Armar de nuevo es un acto explícito desde `POST /api/test/writer`.
        """
        with self._lock:
            tenia_writer = self._writer is not None
            self._writer = None

        if tenia_writer:
            self._emit(
                "writer_released",
                {
                    "enabled": False,
                    "reason": (
                        "El ensayo terminó. El actuador quedó en su valor inicial "
                        "y la escritura se desarmó."
                    ),
                },
            )

    def _abort(self, reason: str) -> None:
        """Corta el ensayo por pérdida de control del actuador."""
        with self._lock:
            self._abort_reason = reason
            self._status = STATUS_ABORTED

        self._stop_flag.set()

        # Se intenta igual: puede que la conexión se haya recuperado, y dejar
        # el actuador en el valor del escalón es peor que intentarlo.
        self._return_to_start()
        self._release_writer()

        with self._lock:
            state = self._state_locked()

        self._emit("test_aborted", state)

    def _apply(self, value: float) -> tuple[bool, Optional[str]]:
        """
        Aplica el valor comandado.

        Hoy solo escribe si hay `writer` inyectado; sin él, el ensayo es
        puramente informativo. Un fallo de escritura no aborta el ensayo:
        se reporta y se sigue.
        """
        with self._lock:
            writer = self._writer

        if writer is None:
            return False, None

        try:
            writer(value)
            return True, None
        except Exception as exc:
            return False, str(exc)

    # ------------------------------------------------------------------ #
    # Estado
    # ------------------------------------------------------------------ #

    def _state_locked(self) -> dict:
        """Snapshot del estado. Debe llamarse con el lock tomado."""
        plan = self._plan
        total = plan["samples"] if plan else 0
        duration = plan["duration_s"] if plan else 0.0

        progress = 0.0
        if total > 0:
            progress = min(1.0, (self._index + 1) / total)

        return {
            "status": self._status,
            "running": self._status == STATUS_RUNNING,
            "elapsed_s": round(self._elapsed_s, 3),
            "duration_s": duration,
            "index": self._index,
            "total": total,
            "progress": round(progress, 4),
            "actuator_cmd": self._command,
            "actuator_cmd_pct": self._command_pct,
            "phase": self._phase,
            "started_at": self._started_at,
            "writes_enabled": self._writer is not None,
            "write_errors": self._write_errors,
            "consecutive_write_errors": self._consecutive_write_errors,
            "last_write_error": self._last_write_error,
            "abort_reason": self._abort_reason,
        }

    def get_state(self) -> dict:
        with self._lock:
            return self._state_locked()

    def get_plan(self) -> dict:
        """Plan del ensayo en curso; si no hay ninguno, el que se generaría ahora."""
        with self._lock:
            if self._plan is not None:
                return self._plan
        return self.build_plan()

    def current_command(self) -> Optional[dict]:
        """
        Valor comandado vigente, o `None` si no hay ensayo corriendo.

        Lo consume `on_sample` para etiquetar cada muestra del PLC con lo que se
        estaba pidiendo en ese instante.
        """
        with self._lock:
            if self._status != STATUS_RUNNING:
                return None
            return {
                "actuator_cmd": self._command,
                "actuator_cmd_pct": self._command_pct,
                "test_phase": self._phase,
                "test_elapsed_s": round(self._elapsed_s, 3),
            }

    def is_running(self) -> bool:
        with self._lock:
            return self._status == STATUS_RUNNING

    # ------------------------------------------------------------------ #

    def _emit(self, event_type: str, data: dict) -> None:
        if self._on_event is None:
            return

        try:
            self._on_event({"type": event_type, "data": data})
        except Exception:
            # Un fallo publicando no puede tumbar el ensayo.
            pass

    def set_event_handler(self, on_event: Optional[Callable[[dict], None]]) -> None:
        with self._lock:
            self._on_event = on_event
