from __future__ import annotations

from domain.models.identification_result import IdentificationResult
from domain.models.transfer_function import TransferFunctionModel
from domain.services.signal_processor import SignalProcessor


class SOPDTIdentifier:
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
        tau1: float,
        tau2: float,
        dead_time: float,
        initial_u: float,
        initial_y: float,
        actuator_data: list[float],
    ) -> list[float]:
        if tau1 <= 1e-9 or tau2 <= 1e-9:
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

            dt = effective_time - time_data[0]
            exp1 = pow(2.718281828, -dt / tau1)
            exp2 = pow(2.718281828, -dt / tau2)

            if abs(tau1 - tau2) < 1e-9:
                # caso casi crítico
                response_factor = 1.0 - exp1 * (1.0 + dt / tau1)
            else:
                response_factor = 1.0 - (
                    (tau1 * exp1 - tau2 * exp2) / (tau1 - tau2)
                )

            y = initial_y + gain * (u - initial_u) * response_factor
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

        t35 = self._find_time_at_fraction(
            time_data,
            sensor_data,
            step_info.initial_y,
            step_info.final_y,
            0.353,
            start_index=step_info.step_index,
        )
        t85 = self._find_time_at_fraction(
            time_data,
            sensor_data,
            step_info.initial_y,
            step_info.final_y,
            0.853,
            start_index=step_info.step_index,
        )

        if t35 is None or t85 is None:
            raise ValueError("No se pudo estimar SOPDT con los cruces de fracción.")

        # aproximación práctica
        x = max(1e-6, t85 - t35)

        dead_time = max(0.0, t35 - 0.25 * x - step_info.step_time)
        tau1 = max(1e-6, 0.6 * x)
        tau2 = max(1e-6, 0.4 * x)

        simulated = self.simulate_response(
            time_data=time_data,
            gain=gain,
            tau1=tau1,
            tau2=tau2,
            dead_time=dead_time,
            initial_u=step_info.initial_u,
            initial_y=step_info.initial_y,
            actuator_data=actuator_data,
        )

        fit_quality = self.calculate_r2(sensor_data, simulated)

        model = TransferFunctionModel(
            model_type="sopdt",
            gain=gain,
            tau1=tau1,
            tau2=tau2,
            dead_time=dead_time,
            numerator=[gain],
            denominator=[tau1 * tau2, tau1 + tau2, 1.0],
            tf_string=(
                f"{gain:.4f} * exp(-{dead_time:.4f}s) / "
                f"(({tau1:.4f}s + 1)({tau2:.4f}s + 1))"
            ),
        )

        return IdentificationResult(
            model=model,
            fit_quality=fit_quality,
            simulated=simulated,
            pid_tunings=[],
            message="Modelo SOPDT identificado correctamente.",
        )