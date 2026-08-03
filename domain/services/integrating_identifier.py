from __future__ import annotations

from domain.models.identification_result import IdentificationResult
from domain.models.transfer_function import TransferFunctionModel
from domain.services.fit_metrics import linear_fit, r_squared
from domain.services.signal_processor import SignalProcessor


class IntegratingIdentifier:
    """Identifica modelos integradores con tiempo muerto: K*exp(-Ls)/s"""

    @staticmethod
    def calculate_r2(measured: list[float], simulated: list[float]) -> float:
        return r_squared(measured, simulated)

    @staticmethod
    def estimate_dead_time(
        time_data: list[float],
        sensor_data: list[float],
        step_index: int,
        ramp_start_index: int | None = None,
    ) -> float:
        """
        Tiempo muerto por intersección de la tangente (método clásico).

        Se ajusta una recta al tramo de rampa y se busca dónde corta el
        valor que tenía la salida en el instante del escalón. Ese cruce es
        el arranque real de la rampa.

        Un umbral por porcentaje (p. ej. "5% del recorrido") NO sirve aquí:
        en un integrador el recorrido total depende de cuánto dure la
        ventana, así que el mismo proceso daría tiempos muertos distintos
        según el largo del registro.
        """
        n = len(sensor_data)
        if step_index >= n - 2:
            return 0.0

        if ramp_start_index is None:
            ramp_start_index = max(step_index + 3, int(n * 0.6))

        if ramp_start_index >= n - 1:
            return 0.0

        slope, intercept = linear_fit(
            time_data[ramp_start_index:],
            sensor_data[ramp_start_index:],
        )

        if abs(slope) < 1e-12:
            return 0.0

        y_at_step = sensor_data[step_index]
        step_time = time_data[step_index]

        ramp_start_time = (y_at_step - intercept) / slope
        dead_time = ramp_start_time - step_time

        # Acotado a la ventana: un retardo negativo o mayor que el registro
        # no tiene sentido físico y solo ensuciaría la sintonía.
        return max(0.0, min(dead_time, time_data[-1] - step_time))

    @staticmethod
    def simulate_response(
        time_data: list[float],
        gain: float,
        dead_time: float,
        initial_u: float,
        initial_y: float,
        actuator_data: list[float],
    ) -> list[float]:
        """
        Integra numéricamente y(t) = y0 + K * ∫(u(t-L) - u0) dt.

        El índice del actuador avanza monótonamente, así que el coste es
        O(n) y no O(n²) como al buscar desde cero en cada muestra.
        """
        if not time_data:
            return []

        simulated = [initial_y]
        u_index = 0
        t0 = time_data[0]

        for i in range(1, len(time_data)):
            effective_time = time_data[i] - dead_time

            if effective_time < t0:
                simulated.append(simulated[-1])
                continue

            while (
                u_index + 1 < len(time_data)
                and time_data[u_index + 1] <= effective_time
            ):
                u_index += 1

            u = actuator_data[u_index]
            dt = time_data[i] - time_data[i - 1]
            simulated.append(simulated[-1] + gain * (u - initial_u) * dt)

        return simulated

    def identify(
        self,
        time_data: list[float],
        actuator_data: list[float],
        sensor_data: list[float],
    ) -> IdentificationResult:
        SignalProcessor.validate_identification_window(time_data, actuator_data, sensor_data)
        step_info = SignalProcessor.detect_step_info(time_data, actuator_data, sensor_data)

        if abs(step_info.delta_u) < 1e-9:
            raise ValueError("No hay cambio suficiente en el actuador para modelo integrador.")

        # Pendiente por mínimos cuadrados sobre el tramo posterior al escalón.
        n = len(sensor_data)
        i0 = max(step_info.step_index + 3, int(n * 0.6))
        if i0 >= n - 2:
            raise ValueError("No hay suficientes datos posteriores para estimar modelo integrador.")

        if time_data[-1] - time_data[i0] <= 1e-9:
            raise ValueError("Ventana inválida para estimar pendiente integradora.")

        slope, _ = linear_fit(time_data[i0:], sensor_data[i0:])
        gain = slope / step_info.delta_u

        dead_time = self.estimate_dead_time(
            time_data=time_data,
            sensor_data=sensor_data,
            step_index=step_info.step_index,
            ramp_start_index=i0,
        )

        simulated = self.simulate_response(
            time_data=time_data,
            gain=gain,
            dead_time=dead_time,
            initial_u=step_info.initial_u,
            initial_y=step_info.initial_y,
            actuator_data=actuator_data,
        )

        fit_quality = r_squared(sensor_data, simulated)

        model = TransferFunctionModel(
            model_type="integrating",
            gain=gain,
            dead_time=dead_time,
            numerator=[gain],
            denominator=[1.0, 0.0],
            tf_string=f"{gain:.4f} * exp(-{dead_time:.4f}s) / s",
        )

        return IdentificationResult(
            model=model,
            fit_quality=fit_quality,
            simulated=simulated,
            pid_tunings=[],
            message="Modelo integrador identificado correctamente.",
        )
