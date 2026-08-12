from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StepInfo:
    initial_u: float
    final_u: float
    delta_u: float
    initial_y: float
    final_y: float
    delta_y: float
    step_index: int
    step_time: float


class SignalProcessor:
    @staticmethod
    def ma_to_percent(values: list[float]) -> list[float]:
        return [((v - 4.0) / 16.0) * 100.0 for v in values]

    @staticmethod
    def percent_to_ma(values: list[float]) -> list[float]:
        return [4.0 + (v / 100.0) * 16.0 for v in values]

    @staticmethod
    def normalize(values: list[float]) -> list[float]:
        if not values:
            return []

        vmin = min(values)
        vmax = max(values)

        if abs(vmax - vmin) < 1e-12:
            return [0.0 for _ in values]

        return [(v - vmin) / (vmax - vmin) for v in values]

    @staticmethod
    def _baseline(values: list[float], step_index: int) -> float:
        """
        Valor de régimen permanente antes del escalón.

        Se usa la **mitad de la línea base más pegada al salto**: es el tramo
        que de verdad representa el estado estable de la entrada inicial. La
        mitad anterior puede contener el arranque del proceso o el final de una
        maniobra previa.

        Sin línea base (el escalón cae en la primera muestra) se cae al primer
        valor disponible, que es lo único que hay.
        """
        if step_index <= 0 or not values:
            return values[0] if values else 0.0

        desde = step_index - max(1, step_index // 2)
        tramo = values[desde:step_index]

        if not tramo:
            return values[0]

        ordenado = sorted(tramo)
        medio = len(ordenado) // 2

        if len(ordenado) % 2:
            return ordenado[medio]
        return (ordenado[medio - 1] + ordenado[medio]) / 2.0

    @staticmethod
    def detect_step_info(time_data: list[float], actuator_data: list[float], sensor_data: list[float]) -> StepInfo:
        if len(time_data) < 3 or len(actuator_data) < 3 or len(sensor_data) < 3:
            raise ValueError("No hay suficientes muestras para detectar escalón.")

        diffs = [abs(actuator_data[i] - actuator_data[i - 1]) for i in range(1, len(actuator_data))]
        step_index = diffs.index(max(diffs)) + 1

        # Régimen permanente ANTES del escalón: la mediana del tramo de línea
        # base más cercano al salto, no la primera muestra de la ventana.
        #
        # Tomar `sensor_data[0]` da por hecho que la ventana empieza con la
        # planta ya asentada, y muchas veces no es así: si la captura arrancó
        # con el proceso todavía subiendo desde cero, la primera muestra no es
        # el estado estable de `initial_u`. El modelo simulaba entonces desde
        # ese valor y TODA la línea base quedaba desplazada, con R² negativo
        # aunque la ganancia y la constante estuvieran bien estimadas.
        #
        # La mediana, y no la media, para que un pico de ruido no la arrastre.
        initial_u = SignalProcessor._baseline(actuator_data, step_index)
        final_u = actuator_data[-1]
        delta_u = final_u - initial_u

        initial_y = SignalProcessor._baseline(sensor_data, step_index)
        final_y = sensor_data[-1]
        delta_y = final_y - initial_y

        return StepInfo(
            initial_u=initial_u,
            final_u=final_u,
            delta_u=delta_u,
            initial_y=initial_y,
            final_y=final_y,
            delta_y=delta_y,
            step_index=step_index,
            step_time=time_data[step_index],
        )

    @staticmethod
    def validate_identification_window(time_data: list[float], actuator_data: list[float], sensor_data: list[float]) -> None:
        if len(time_data) < 20:
            raise ValueError("Se requieren al menos 20 muestras.")
        if len(time_data) != len(actuator_data) or len(time_data) != len(sensor_data):
            raise ValueError("Las series de tiempo, actuador y sensor deben tener la misma longitud.")

        step_info = SignalProcessor.detect_step_info(time_data, actuator_data, sensor_data)

        if abs(step_info.delta_u) < 1e-6:
            raise ValueError("No se detectó cambio en el actuador.")
        if abs(step_info.delta_y) < 1e-6:
            raise ValueError("No se detectó respuesta en el sensor.")