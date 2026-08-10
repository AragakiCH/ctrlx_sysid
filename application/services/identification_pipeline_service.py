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

        discarded: list[dict] = []

        if order and order != "auto":
            try:
                results = [self.identification_service.identify_from_series(window, order=order)]
            except Exception:
                return None
        else:
            results, discarded = self.identification_service.compare_models_detailed(window)

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
            "baseline": self.describe_baseline(window),
            # Modelos que no convergieron, con el motivo. Sin esto simplemente
            # faltarían tarjetas en la vista y no habría forma de saber si el
            # modelo no aplicaba o si los datos no daban para ajustarlo.
            "discarded": discarded,
            "models": [self.serialize_result(r) for r in results],
        }

    def describe_baseline(self, window) -> dict:
        """
        ¿Estaba la planta en reposo cuando empezó la ventana?

        Todo el ajuste parte de ahí: `initial_y` se toma como el valor de
        régimen permanente que corresponde a `initial_u`, y la respuesta
        simulada arranca desde ese punto suponiendo equilibrio. Si el sensor
        todavía venía moviéndose por un cambio anterior, ese supuesto es falso:
        la ganancia absorbe la deriva y la constante de tiempo sale desplazada,
        aunque el R² pueda seguir viéndose alto.

        Es lo que pasa cuando se encadenan escalones sin dejar asentar el
        proceso entre uno y otro.
        """
        actuador = window.actuator
        sensor = window.sensor

        vacio = {
            "samples": 0,
            "drift": 0.0,
            "response": 0.0,
            "ratio": 0.0,
            "settled": True,
        }

        if len(actuador) < 3:
            return vacio

        # Primer punto donde el actuador se mueve: ahí termina la línea base.
        step_in_window = None
        for i in range(1, len(actuador)):
            if abs(actuador[i] - actuador[i - 1]) >= self.step_detector_service.min_step_delta:
                step_in_window = i
                break

        if step_in_window is None or step_in_window < 2:
            return vacio

        base = sensor[:step_in_window]
        deriva = max(base) - min(base)
        respuesta = abs(sensor[-1] - sensor[0])

        ratio = deriva / respuesta if respuesta > 1e-9 else 0.0

        return {
            "samples": step_in_window,
            "drift": round(deriva, 4),
            "response": round(respuesta, 4),
            "ratio": round(ratio, 4),
            # Un 10 % de la respuesta es tolerancia razonable para ruido de
            # medición; por encima de eso ya no es ruido, es que no asentó.
            "settled": ratio <= 0.10,
        }