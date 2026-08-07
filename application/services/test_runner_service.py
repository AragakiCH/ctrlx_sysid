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
PHASE_PRESET = "preset"  # llevando la planta al valor inicial, antes de grabar
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

    # --- Pre-posicionado antes de grabar ---
    # Cuánto tiene que estar quieta la planta, y con qué tolerancia, para dar
    # por buena la línea base. La tolerancia va en % de span, que es la escala
    # común interna.
    SETTLE_WINDOW_S = 3.0        # ventana que se mira para juzgar "quieta"
    SETTLE_TOLERANCE_PCT = 0.5   # recorrido máximo dentro de esa ventana
    SETTLE_MIN_S = 2.0           # mínimo, aunque el sensor no se mueva nada
    SETTLE_TIMEOUT_S = 30.0      # a partir de aquí se arranca igual, avisando
    SETTLE_POLL_S = 0.2

    def __init__(
        self,
        test_config_service,
        on_event: Optional[Callable[[dict], None]] = None,
        writer: Optional[Callable[[float], None]] = None,
        sensor_provider: Optional[Callable[[], Optional[float]]] = None,
        on_capture_start: Optional[Callable[[], None]] = None,
    ) -> None:
        self._config = test_config_service
        self._on_event = on_event
        self._writer = writer

        # Lectura del sensor (en % de span) para saber cuándo asentó, y aviso
        # de "empieza la grabación" para que el buffer se limpie en el momento
        # justo y no arrastre el viaje al punto de partida.
        self._sensor_provider = sensor_provider
        self._on_capture_start = on_capture_start

        # Apagado por defecto: quien decide es la API (`POST /api/test/start`),
        # que sí lo pide activo. Así el runner se puede ejercitar sin esperar la
        # fase de asentado.
        self._presettle = False
        self._settle_window_s = self.SETTLE_WINDOW_S
        self._settle_tolerance_pct = self.SETTLE_TOLERANCE_PCT
        self._settle_min_s = self.SETTLE_MIN_S
        self._settle_timeout_s = self.SETTLE_TIMEOUT_S
        self._settle_poll_s = self.SETTLE_POLL_S

        # Intención del usuario ("quiero que el ensayo mueva el actuador") y la
        # fábrica con la que se reconstruye el escritor en cada arranque. Ver
        # `arm()`: sobreviven a que el escritor se suelte al terminar un ensayo.
        self._write_intent = writer is not None
        self._writer_factory: Optional[Callable[[], Callable[[float], None]]] = None

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

    def arm(self, factory: Callable[[], Callable[[float], None]]) -> None:
        """
        Deja armada la escritura y **recuerda la intención** del usuario.

        La intención y el escritor activo son cosas distintas a propósito. Al
        terminar cada ensayo el escritor se suelta (`_release_writer`) para que
        la HMI recupere la variable del actuador, pero la intención sobrevive:
        si no, tras el primer ensayo la casilla seguiría marcada y el siguiente
        arranque no movería nada. Es justo lo que se veía al reiniciar —"me deja
        iniciar un ensayo nuevo, pero no genera la señal del actuador"—.

        Se guarda una **fábrica**, no la función ya construida: así cada ensayo
        resuelve el escritor contra la sesión OPC UA vigente en ese momento y no
        contra una que pudo cerrarse y reabrirse por el medio.
        """
        with self._lock:
            self._writer_factory = factory
            self._write_intent = True
            self._writer = factory()

    def disarm(self) -> None:
        """Desarma y olvida la intención: los ensayos vuelven a ser solo dibujo."""
        with self._lock:
            self._writer_factory = None
            self._write_intent = False
            self._writer = None

    @property
    def write_intent(self) -> bool:
        """¿Quiere el usuario que los ensayos muevan el actuador?"""
        with self._lock:
            return self._write_intent

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

    def start(self, presettle: Optional[bool] = None) -> dict:
        """
        Arranca un ensayo. Devuelve el estado inicial junto con el plan.

        Con `presettle` activo (por defecto) el actuador se lleva primero al
        valor inicial y se espera a que el sensor deje de moverse; recién
        entonces empieza la grabación. Requiere escritura armada: sin ella la
        app no gobierna el actuador y el pre-posicionado no tiene sentido.
        """
        with self._lock:
            if presettle is not None:
                self._presettle = bool(presettle)
            if self._status == STATUS_RUNNING:
                raise ValueError(
                    "Ya hay un ensayo en curso. Deténlo antes de arrancar otro."
                )

            # build_plan lee la config; si es inválida, revienta aquí y no
            # llegamos a arrancar el hilo.
            plan = self.build_plan()

            # Se rearma solo si el usuario lo dejó pedido. El escritor se suelta
            # al final de cada ensayo para devolverle la variable a la HMI, así
            # que sin esto el segundo ensayo de la sesión no movería nada.
            if self._write_intent and self._writer is None and self._writer_factory:
                self._writer = self._writer_factory()

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

    def _preset_and_settle(self, plan: dict) -> Optional[bool]:
        """
        Lleva la planta al valor inicial y espera a que se quede quieta, **antes**
        de empezar a grabar.

        Devuelve `None` si el ensayo se canceló por el medio, `False` si no
        había nada que pre-posicionar (sin escritura armada) y `True` si de
        verdad se esperó a que asentara.

        Sin esta fase el operador tiene que dejar el actuador en el valor inicial
        a mano antes de pulsar Inicio; si no lo hace, el ensayo graba la planta
        todavía viniendo del punto anterior. El ajuste toma la primera muestra
        como el régimen permanente de la entrada inicial, así que esa deriva se
        la come la ganancia y el modelo describe algo que no ocurrió.

        No basta con escribir el valor y esperar `delay_s`: ese retardo es la
        línea base que SÍ se graba, y una planta lenta puede tardar mucho más en
        asentar. Aquí se espera de verdad, mirando el sensor.
        """
        provider = self._sensor_provider
        writer = self._writer

        if writer is None:
            # Sin escritura la app no gobierna el actuador: no hay nada que
            # pre-posicionar y la responsabilidad sigue siendo del operador.
            return False

        objetivo = plan["from_value"]

        try:
            writer(objetivo)
        except Exception as exc:
            self._emit("test_preset_error", {"detail": str(exc)})

        if provider is None:
            # Sin lectura del sensor no se puede saber si asentó. Se concede un
            # margen fijo, mejor que arrancar de inmediato.
            self._stop_flag.wait(self._settle_min_s)
            return True if not self._stop_flag.is_set() else None

        inicio = time.monotonic()
        historial: list[tuple[float, float]] = []

        while not self._stop_flag.is_set():
            transcurrido = time.monotonic() - inicio

            valor = None
            try:
                valor = provider()
            except Exception:
                pass

            if isinstance(valor, (int, float)):
                historial.append((transcurrido, float(valor)))
                # Solo interesa la ventana reciente: que la planta estuviera
                # quieta hace veinte segundos no dice nada de ahora.
                historial = [
                    (t, v) for (t, v) in historial if transcurrido - t <= self._settle_window_s
                ]

            with self._lock:
                self._phase = PHASE_PRESET
                self._command = objetivo
                self._command_pct = plan["from_value_pct"]
                self._elapsed_s = transcurrido
                estado = self._state_locked()

            self._emit(
                "test_presetting",
                {
                    **estado,
                    "target": objetivo,
                    "sensor": valor,
                    "elapsed_s": round(transcurrido, 2),
                    "timeout_s": self._settle_timeout_s,
                },
            )

            asentada = (
                transcurrido >= self._settle_min_s
                and len(historial) >= 3
                and (max(v for _, v in historial) - min(v for _, v in historial))
                <= self._settle_tolerance_pct
            )

            if asentada:
                self._emit(
                    "test_settled",
                    {"elapsed_s": round(transcurrido, 2), "sensor": valor},
                )
                return True

            if transcurrido >= self._settle_timeout_s:
                # No se aborta: puede que la planta tenga una deriva propia que
                # nunca baje de la tolerancia. Se sigue, pero avisando, y el
                # aviso de línea base no asentada saldrá después si hace falta.
                self._emit(
                    "test_settle_timeout",
                    {
                        "elapsed_s": round(transcurrido, 2),
                        "detail": (
                            f"La planta no se estabilizó en {self._settle_timeout_s:.0f} s. "
                            "Se arranca igual, pero la línea base puede no estar en reposo."
                        ),
                    },
                )
                return True

            self._stop_flag.wait(self._settle_poll_s)

        return None  # cancelado

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

        # Pre-posicionado: la planta llega al valor inicial y se estabiliza
        # ANTES de que empiece la grabación.
        hubo_preset = False

        if self._presettle:
            resultado = self._preset_and_settle(plan)
            if resultado is None:
                return  # se canceló durante el asentado
            hubo_preset = resultado

        if hubo_preset:
            # El buffer se limpia aquí, no al pulsar Inicio: todo lo capturado
            # durante el pre-posicionado es la planta viajando al punto de
            # partida, y meterlo en la ventana de identificación sería
            # exactamente el problema que esta fase viene a evitar.
            #
            # Solo si el pre-posicionado ocurrió de verdad: si no, se respetaría
            # mal el `clear_buffer=false` de quien arrancó el ensayo.
            if self._on_capture_start is not None:
                try:
                    self._on_capture_start()
                except Exception:
                    pass

            # El reloj arranca con la grabación, no con el pre-posicionado.
            with self._lock:
                self._monotonic_start = time.monotonic()
                self._started_at = time.time()
                self._elapsed_s = 0.0
                self._phase = PHASE_BASELINE

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
                    # La intención sigue en pie: el próximo ensayo se rearma solo.
                    "intent": self.write_intent,
                    "reason": (
                        "El ensayo terminó. El actuador quedó en su valor inicial "
                        "y la app soltó la variable."
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
            "presettle": self._presettle,
            "write_intent": self._write_intent,
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
