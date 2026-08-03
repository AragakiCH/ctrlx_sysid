from __future__ import annotations

import math

from domain.models.identification_result import IdentificationResult
from domain.models.transfer_function import TransferFunctionModel
from domain.services.fit_metrics import r_squared
from domain.services.signal_processor import SignalProcessor


class FOPDTIdentifier:
    """
    Identifica K*exp(-Ls)/(tau*s+1) por el método de los dos puntos
    (28.3% y 63.2% del cambio total), de Smith.
    """

    @staticmethod
    def _find_time_at_fraction(
        time_data: list[float],
        sensor_data: list[float],
        initial_y: float,
        final_y: float,
        fraction: float,
        start_index: int = 0,
    ) -> float | None:
        """
        Instante en que la salida cruza `fraction` del recorrido total.
        Interpola linealmente entre muestras para no quedar atado al
        periodo de muestreo.
        """
        target = initial_y + fraction * (final_y - initial_y)
        rising = final_y >= initial_y

        for i in range(start_index, len(sensor_data)):
            y = sensor_data[i]

            if (rising and y >= target) or (not rising and y <= target):
                if i == start_index:
                    return time_data[i]

                y_prev = sensor_data[i - 1]
                denom = y - y_prev

                if abs(denom) < 1e-12:
                    return time_data[i]

                frac = (target - y_prev) / denom
                frac = min(max(frac, 0.0), 1.0)
                return time_data[i - 1] + frac * (time_data[i] - time_data[i - 1])

        return None

    @staticmethod
    def delayed_input(
        time_data: list[float],
        actuator_data: list[float],
        dead_time: float,
        initial_u: float,
    ) -> list[float]:
        """u(t - L), usando retención de orden cero entre muestras."""
        delayed: list[float] = []
        u_index = 0
        t0 = time_data[0]

        for t in time_data:
            effective_time = t - dead_time

            if effective_time < t0:
                delayed.append(initial_u)
                continue

            while (
                u_index + 1 < len(time_data)
                and time_data[u_index + 1] <= effective_time
            ):
                u_index += 1

            delayed.append(actuator_data[u_index])

        return delayed

    @classmethod
    def simulate_response(
        cls,
        time_data: list[float],
        gain: float,
        tau: float,
        dead_time: float,
        initial_u: float,
        initial_y: float,
        actuator_data: list[float],
    ) -> list[float]:
        """
        Integra el modelo de primer orden contra el actuador real.

        Se hace de forma recursiva (y[i+1] = y_ss + (y[i]-y_ss)*e^(-dt/tau))
        en vez de aplicar la fórmula del escalón desde t0. Esto importa
        porque la ventana de identificación incluye muestras ANTES del
        escalón: con la fórmula cerrada el exponencial arrancaba en
        time_data[0] y la curva simulada quedaba desfasada.

        Además es O(n) y admite cualquier forma de u(t), no solo un escalón.
        """
        if not time_data:
            return []

        if tau <= 1e-9:
            return [initial_y for _ in time_data]

        delayed = cls.delayed_input(time_data, actuator_data, dead_time, initial_u)

        simulated = [initial_y]

        for i in range(1, len(time_data)):
            dt = time_data[i] - time_data[i - 1]
            # valor de régimen permanente para la entrada actual
            y_ss = initial_y + gain * (delayed[i] - initial_u)
            decay = math.exp(-dt / tau) if dt > 0 else 1.0
            simulated.append(y_ss + (simulated[-1] - y_ss) * decay)

        return simulated

    @staticmethod
    def calculate_r2(measured: list[float], simulated: list[float]) -> float:
        return r_squared(measured, simulated)

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

        fit_quality = r_squared(sensor_data, simulated)

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
