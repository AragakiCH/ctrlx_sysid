from __future__ import annotations

from domain.models.identification_result import IdentificationResult
from domain.models.transfer_function import TransferFunctionModel
from domain.services.signal_processor import SignalProcessor


class FOPDTIdentifier:
    @staticmethod
    def _find_time_at_fraction(
        time_data: list[float],
        sensor_data: list[float],
        initial_y: float,
        final_y: float,
        fraction: float,
        start_index: int = 0,
    ) -> float | None:
        target = initial_y + fraction * (final_y - initial_y)

        for i in range(start_index, len(sensor_data)):
            y = sensor_data[i]

            if (final_y >= initial_y and y >= target) or (final_y < initial_y and y <= target):
                return time_data[i]

        return None

    @staticmethod
    def simulate_response(
        time_data: list[float],
        gain: float,
        tau: float,
        dead_time: float,
        initial_u: float,
        initial_y: float,
        actuator_data: list[float],
    ) -> list[float]:
        if tau <= 1e-9:
            return [initial_y for _ in time_data]

        simulated = []

        for t in time_data:
            effective_time = t - dead_time

            if effective_time <= time_data[0]:
                simulated.append(initial_y)
                continue

            u = initial_u
            for i in range(len(time_data)):
                if time_data[i] <= effective_time:
                    u = actuator_data[i]
                else:
                    break

            y = initial_y + gain * (u - initial_u) * (1.0 - pow(2.718281828, -(effective_time - time_data[0]) / tau))
            simulated.append(y)

        return simulated

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

    def identify(
        self,
        time_data: list[float],
        actuator_data: list[float],
        sensor_data: list[float],
    ) -> IdentificationResult:
        SignalProcessor.validate_identification_window(time_data, actuator_data, sensor_data)
        step_info = SignalProcessor.detect_step_info(time_data, actuator_data, sensor_data)

        gain = step_info.delta_y / step_info.delta_u

        t28 = self._find_time_at_fraction(
            time_data,
            sensor_data,
            step_info.initial_y,
            step_info.final_y,
            0.283,
            start_index=step_info.step_index,
        )
        t63 = self._find_time_at_fraction(
            time_data,
            sensor_data,
            step_info.initial_y,
            step_info.final_y,
            0.632,
            start_index=step_info.step_index,
        )

        if t28 is None or t63 is None:
            raise ValueError("No se pudo estimar tau y dead time con el método FOPDT.")

        dead_time = max(0.0, 1.5 * t28 - 0.5 * t63 - step_info.step_time)
        tau = max(1e-6, 1.5 * (t63 - t28))

        simulated = self.simulate_response(
            time_data=time_data,
            gain=gain,
            tau=tau,
            dead_time=dead_time,
            initial_u=step_info.initial_u,
            initial_y=step_info.initial_y,
            actuator_data=actuator_data,
        )

        fit_quality = self.calculate_r2(sensor_data, simulated)

        model = TransferFunctionModel(
            model_type="fopdt",
            gain=gain,
            tau=tau,
            dead_time=dead_time,
            numerator=[gain],
            denominator=[tau, 1.0],
            tf_string=f"{gain:.4f} * exp(-{dead_time:.4f}s) / ({tau:.4f}s + 1)",
        )

        return IdentificationResult(
            model=model,
            fit_quality=fit_quality,
            simulated=simulated,
            pid_tunings=[],
            message="Modelo FOPDT identificado correctamente.",
        )