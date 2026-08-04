from __future__ import annotations

from domain.models.signals import SignalSeries


class StepDetectorService:
    def __init__(self, min_step_delta: float = 1.0) -> None:
        self.min_step_delta = min_step_delta

    def find_latest_rising_step_index(self, actuator_data: list[float]) -> int | None:
        """
        Busca el último escalón positivo (subida) suficientemente grande.
        Recorre desde el final para agarrar el evento más reciente.
        """
        if len(actuator_data) < 2:
            return None

        for i in range(len(actuator_data) - 1, 0, -1):
            delta = actuator_data[i] - actuator_data[i - 1]
            if delta >= self.min_step_delta:
                return i

        return None

    def find_previous_step_index(
        self, actuator_data: list[float], before_index: int
    ) -> int | None:
        """
        Busca el escalón anterior a `before_index`, de cualquier signo.

        Se usa para no dejar que la línea base se estire hacia atrás más allá de
        la transición previa. En un programa de PLC que cicla (por ejemplo 4 mA
        durante 5 s y 12 mA durante 15 s, en bucle), pedir 10 s de línea base
        haría que la ventana empezara en la fase alta del ciclo anterior: el
        actuador valdría lo mismo al principio y al final de la ventana, y el
        identificador concluiría que no hubo escalón.
        """
        if before_index <= 0:
            return None

        for i in range(before_index - 1, 0, -1):
            if abs(actuator_data[i] - actuator_data[i - 1]) >= self.min_step_delta:
                return i

        return None

    def find_latest_step_index(self, actuator_data: list[float]) -> int | None:
        """
        Busca el último escalón de cualquier signo.
        """
        if len(actuator_data) < 2:
            return None

        for i in range(len(actuator_data) - 1, 0, -1):
            delta = abs(actuator_data[i] - actuator_data[i - 1])
            if delta >= self.min_step_delta:
                return i

        return None

    def extract_window_from_step_index(
        self,
        series: SignalSeries,
        step_index: int,
        pre_samples: int = 10,
        post_samples: int = 40,
    ) -> SignalSeries | None:
        if step_index < 0 or step_index >= len(series.time):
            return None

        start = max(0, step_index - pre_samples)

        # La línea base no puede cruzar la transición anterior: si lo hiciera,
        # la ventana contendría dos escalones y el ajuste no tendría sentido.
        previous_step = self.find_previous_step_index(series.actuator, step_index)
        if previous_step is not None:
            start = max(start, previous_step)

        end = min(len(series.time), step_index + post_samples)

        if end - start < 20:
            return None

        window_time = series.time[start:end]
        t0 = window_time[0]
        window_time = [t - t0 for t in window_time]

        return SignalSeries(
            time=window_time,
            actuator=series.actuator[start:end],
            sensor=series.sensor[start:end],
            setpoint=series.setpoint[start:end] if series.setpoint else [],
            signal_type=series.signal_type,
        )