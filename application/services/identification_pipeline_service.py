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
            "pid_tunings": [
                {
                    "method": pid.method,
                    "kp": pid.kp,
                    "ki": pid.ki,
                    "kd": pid.kd,
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
    ) -> dict | None:
        step_index = self.step_detector_service.find_latest_rising_step_index(series.actuator)
        if step_index is None:
            return None

        post_available = len(series.time) - step_index
        if post_available < 30:
            return None

        window = self.step_detector_service.extract_window_from_step_index(
            series,
            step_index=step_index,
            pre_samples=pre_samples,
            post_samples=post_samples,
        )

        if window is None:
            return None

        results = self.identification_service.compare_models(window)
        if not results:
            return None

        best = results[0]

        return {
            "step_index": step_index,
            "winner": best.model.model_type,
            "models": [self.serialize_result(r) for r in results],
        }