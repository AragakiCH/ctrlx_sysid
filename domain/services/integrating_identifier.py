from __future__ import annotations

from domain.models.identification_result import IdentificationResult
from domain.models.transfer_function import TransferFunctionModel
from domain.services.signal_processor import SignalProcessor


class IntegratingIdentifier:
    @staticmethod
    def calculate_r2(measured: list[float], simulated: list[float]) -> float:
        if not measured or not simulated or len(measured) != len(simulated):
            return 0.0

        y_mean = sum(measured) / len(measured)
        ss_tot = sum((y - y_mean) ** 2 for y in measured)
        ss_res = sum((measured[i] - simulated[i]) ** 2 for i in range(len(measured)))

        if ss_tot <= 1e-12:
            return 0.0

        return 1.0 - (ss_res / ss_tot)

    @staticmethod
    def simulate_response(
        time_data: list[float],
        gain: float,
        dead_time: float,
        initial_u: float,
        initial_y: float,
        actuator_data: list[float],
    ) -> list[float]:
        simulated = [initial_y]

        for i in range(1, len(time_data)):
            t = time_data[i]
            effective_time = t - dead_time

            if effective_time <= time_data[0]:
                simulated.append(simulated[-1])
                continue

            u = initial_u
            for j in range(len(time_data)):
                if time_data[j] <= effective_time:
                    u = actuator_data[j]
                else:
                    break

            dt = time_data[i] - time_data[i - 1]
            dy = gain * (u - initial_u) * dt
            simulated.append(simulated[-1] + dy)

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

        # estima pendiente usando la parte final de la curva
        n = len(sensor_data)
        i0 = max(step_info.step_index + 3, int(n * 0.6))
        if i0 >= n - 2:
            raise ValueError("No hay suficientes datos posteriores para estimar modelo integrador.")

        dt = time_data[-1] - time_data[i0]
        if dt <= 1e-9:
            raise ValueError("Ventana inválida para estimar pendiente integradora.")

        slope = (sensor_data[-1] - sensor_data[i0]) / dt
        gain = slope / step_info.delta_u

        # aproximación simple de dead time:
        dead_time = max(0.0, time_data[step_info.step_index] - step_info.step_time)

        simulated = self.simulate_response(
            time_data=time_data,
            gain=gain,
            dead_time=dead_time,
            initial_u=step_info.initial_u,
            initial_y=step_info.initial_y,
            actuator_data=actuator_data,
        )

        fit_quality = self.calculate_r2(sensor_data, simulated)

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