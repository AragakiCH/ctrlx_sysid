from __future__ import annotations

from application.services.identification_service import IdentificationService
from application.services.step_detector_service import StepDetectorService
from domain.models.identification_result import IdentificationResult


class IdentificationPipelineService:
    def __init__(
        self,
        identification_service: IdentificationService,
        step_detector_service: StepDetectorService,
    ) -> None:
        self.identification_service = identification_service
        self.step_detector_service = step_detector_service

    def serialize_result(self, result: IdentificationResult) -> dict:
        payload = {
            "model_type": result.model.model_type,
            "gain": result.model.gain,
            "dead_time": result.model.dead_time,
            "fit_quality": result.fit_quality,
            "tf_string": result.model.tf_string,
            "numerator": result.model.numerator,
            "denominator": result.model.denominator,
            # La curva simulada alimenta el gráfico medido-vs-modelo.
            "simulated": result.simulated,
            "pid_tunings": [
                {
                    "method": pid.method,
                    "kp": pid.kp,
                    "ki": pid.ki,
                    "kd": pid.kd,
                    "ti": pid.ti,
                    "td": pid.td,
                    "lambda": pid.lambda_c,
                    "description": pid.description,
                }
                for pid in result.pid_tunings
            ],
        }

        if result.model.model_type == "fopdt":
            payload["tau"] = result.model.tau

        elif result.model.model_type == "sopdt":
            payload["tau1"] = result.model.tau1
            payload["tau2"] = result.model.tau2

        return payload

    def process_series(
        self,
        series,
        pre_samples: int = 10,
        post_samples: int = 40,
        order: str = "auto",
    ) -> dict | None:
        """
        `order` viene de las condiciones de ensayo (paso 2 de la vista):

        * `auto` — ajusta FOPDT, SOPDT e integrante y rankea por R².
        * `fopdt` / `sopdt` / `integrating` — ajusta solo ese modelo. Igual se
          devuelve como lista de un elemento para no cambiar el contrato con la UI.
        """
        step_index = self.step_detector_service.find_latest_rising_step_index(series.actuator)
        if step_index is None:
            return None

        # No identificar hasta tener la respuesta completa que pidió el ensayo.
        # Si se ajusta con una ventana truncada la curva todavía no llegó al
        # nuevo estable y tanto K como tau salen subestimados.
        post_available = len(series.time) - step_index
        if post_available < max(30, post_samples):
            return None

        window = self.step_detector_service.extract_window_from_step_index(
            series,
            step_index=step_index,
            pre_samples=pre_samples,
            post_samples=post_samples,
        )

        if window is None:
            return None

        if order and order != "auto":
            try:
                results = [self.identification_service.identify_from_series(window, order=order)]
            except Exception:
                return None
        else:
            results = self.identification_service.compare_models(window)

        if not results:
            return None

        best = results[0]

        return {
            "step_index": step_index,
            "order": order,
            "winner": best.model.model_type,
            # Ventana usada para identificar. La UI la necesita para graficar
            # el medido contra el simulado: el buffer completo tiene otra
            # longitud y no cuadraría con `simulated`.
            "window": {
                "time": window.time,
                "actuator": window.actuator,
                "sensor": window.sensor,
                "setpoint": window.setpoint,
                "count": len(window.time),
            },
            "models": [self.serialize_result(r) for r in results],
        }