from __future__ import annotations

import math

from domain.models.identification_result import IdentificationResult
from domain.models.transfer_function import TransferFunctionModel
from domain.services.fit_metrics import r_squared
from domain.services.fopdt_identifier import FOPDTIdentifier
from domain.services.optimizer import clamp, nelder_mead
from domain.services.signal_processor import SignalProcessor


class SOPDTIdentifier:
    """
    Identifica K*exp(-Ls)/((tau1*s+1)(tau2*s+1)).

    Estrategia en dos pasos:
      1. Semilla analítica con los cruces al 35.3% y 85.3% (Smith).
      2. Refinamiento numérico de (K, tau1, tau2, L) minimizando el error
         cuadrático contra los datos medidos.

    Sin el paso 2 el modelo de 2º orden casi siempre perdía el ranking
    frente al FOPDT aunque el proceso fuera realmente de 2º orden.
    """

    MAX_ITER = 400

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

                frac = clamp((target - y_prev) / denom, 0.0, 1.0)
                return time_data[i - 1] + frac * (time_data[i] - time_data[i - 1])

        return None

    @staticmethod
    def _response_factor(dt: float, tau1: float, tau2: float) -> float:
        """
        Respuesta al escalón normalizada de dos polos reales en serie.
        Se conserva como referencia analítica y para tests.
        """
        if abs(tau1 - tau2) < 1e-9:
            # polos repetidos (amortiguamiento crítico)
            return 1.0 - math.exp(-dt / tau1) * (1.0 + dt / tau1)

        exp1 = math.exp(-dt / tau1)
        exp2 = math.exp(-dt / tau2)
        return 1.0 - ((tau1 * exp1 - tau2 * exp2) / (tau1 - tau2))

    @classmethod
    def simulate_response(
        cls,
        time_data: list[float],
        gain: float,
        tau1: float,
        tau2: float,
        dead_time: float,
        initial_u: float,
        initial_y: float,
        actuator_data: list[float],
    ) -> list[float]:
        """
        Integra dos primeros órdenes en cascada contra el actuador real.

        Igual que en el FOPDT: se simula recursivamente en vez de aplicar
        la fórmula del escalón desde time_data[0], porque la ventana
        incluye muestras previas al escalón.
        """
        if not time_data:
            return []

        if tau1 <= 1e-9 or tau2 <= 1e-9:
            return [initial_y for _ in time_data]

        delayed = FOPDTIdentifier.delayed_input(
            time_data, actuator_data, dead_time, initial_u
        )

        simulated = [initial_y]
        stage1 = 0.0  # salida de la primera etapa, en desviación

        for i in range(1, len(time_data)):
            dt = time_data[i] - time_data[i - 1]
            decay1 = math.exp(-dt / tau1) if dt > 0 else 1.0
            decay2 = math.exp(-dt / tau2) if dt > 0 else 1.0

            target1 = gain * (delayed[i] - initial_u)
            stage1 = target1 + (stage1 - target1) * decay1

            deviation = simulated[-1] - initial_y
            deviation = stage1 + (deviation - stage1) * decay2

            simulated.append(initial_y + deviation)

        return simulated

    @staticmethod
    def calculate_r2(measured: list[float], simulated: list[float]) -> float:
        return r_squared(measured, simulated)

    # ------------------------------------------------------------------ #
    # Ajuste numérico
    # ------------------------------------------------------------------ #

    def _refine(
        self,
        time_data: list[float],
        actuator_data: list[float],
        sensor_data: list[float],
        seed: tuple[float, float, float, float],
        initial_u: float,
        initial_y: float,
    ) -> tuple[float, float, float, float]:
        """Refina (K, tau1, tau2, L) minimizando el error cuadrático."""
        t_range = max(time_data[-1] - time_data[0], 1e-6)
        max_tau = t_range * 5.0
        max_delay = t_range * 0.5

        def unpack(params: list[float]) -> tuple[float, float, float, float]:
            k = params[0]
            tau1 = clamp(abs(params[1]), 1e-4, max_tau)
            tau2 = clamp(abs(params[2]), 1e-4, max_tau)
            delay = clamp(abs(params[3]), 0.0, max_delay)
            return k, tau1, tau2, delay

        def objective(params: list[float]) -> float:
            k, tau1, tau2, delay = unpack(params)

            simulated = self.simulate_response(
                time_data=time_data,
                gain=k,
                tau1=tau1,
                tau2=tau2,
                dead_time=delay,
                initial_u=initial_u,
                initial_y=initial_y,
                actuator_data=actuator_data,
            )

            return sum(
                (sensor_data[i] - simulated[i]) ** 2 for i in range(len(sensor_data))
            )

        x0 = list(seed)
        step = [
            max(abs(seed[0]) * 0.2, 1e-3),
            max(seed[1] * 0.3, 1e-3),
            max(seed[2] * 0.3, 1e-3),
            max(t_range * 0.05, 1e-3),
        ]

        best, best_score = nelder_mead(objective, x0, step=step, max_iter=self.MAX_ITER)

        # Si el refinamiento no mejoró la semilla, quedarse con la semilla.
        if best_score > objective(x0):
            return unpack(x0)

        return unpack(best)

    # ------------------------------------------------------------------ #

    def identify(
        self,
        time_data: list[float],
        actuator_data: list[float],
        sensor_data: list[float],
    ) -> IdentificationResult:
        SignalProcessor.validate_identification_window(time_data, actuator_data, sensor_data)
        step_info = SignalProcessor.detect_step_info(time_data, actuator_data, sensor_data)

        gain_seed = step_info.delta_y / step_info.delta_u

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

        # Semilla analítica
        x = max(1e-6, t85 - t35)
        seed = (
            gain_seed,
            max(1e-6, 0.6 * x),
            max(1e-6, 0.4 * x),
            max(0.0, t35 - 0.25 * x - step_info.step_time),
        )

        gain, tau1, tau2, dead_time = self._refine(
            time_data=time_data,
            actuator_data=actuator_data,
            sensor_data=sensor_data,
            seed=seed,
            initial_u=step_info.initial_u,
            initial_y=step_info.initial_y,
        )

        # Convención: tau1 es la constante dominante.
        if tau2 > tau1:
            tau1, tau2 = tau2, tau1

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

        fit_quality = r_squared(sensor_data, simulated)

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
