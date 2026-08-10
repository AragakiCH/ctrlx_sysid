from __future__ import annotations

from domain.models.identification_result import IdentificationResult
from domain.services.controller_tuner import ControllerTuner
from domain.services.fopdt_identifier import FOPDTIdentifier
from domain.services.integrating_identifier import IntegratingIdentifier
from domain.services.model_ranker import ModelRanker
from domain.services.signal_processor import SignalProcessor
from domain.services.sopdt_identifier import SOPDTIdentifier


class IdentificationService:
    def __init__(self) -> None:
        self.signal_processor = SignalProcessor()
        self.fopdt_identifier = FOPDTIdentifier()
        self.sopdt_identifier = SOPDTIdentifier()
        self.integrating_identifier = IntegratingIdentifier()
        self.controller_tuner = ControllerTuner()
        self.model_ranker = ModelRanker()

    def identify_fopdt(
        self,
        time_data: list[float],
        actuator_data: list[float],
        sensor_data: list[float],
    ) -> IdentificationResult:
        result = self.fopdt_identifier.identify(
            time_data=time_data,
            actuator_data=actuator_data,
            sensor_data=sensor_data,
        )
        result.pid_tunings = self.controller_tuner.tune_fopdt(result.model)
        return result

    def identify_sopdt(
        self,
        time_data: list[float],
        actuator_data: list[float],
        sensor_data: list[float],
    ) -> IdentificationResult:
        result = self.sopdt_identifier.identify(
            time_data=time_data,
            actuator_data=actuator_data,
            sensor_data=sensor_data,
        )
        result.pid_tunings = self.controller_tuner.tune_sopdt(result.model)
        return result

    def identify_integrating(
        self,
        time_data: list[float],
        actuator_data: list[float],
        sensor_data: list[float],
    ) -> IdentificationResult:
        result = self.integrating_identifier.identify(
            time_data=time_data,
            actuator_data=actuator_data,
            sensor_data=sensor_data,
        )
        result.pid_tunings = self.controller_tuner.tune_integrating(result.model)
        return result

    def identify_from_series(self, series, order: str = "fopdt") -> IdentificationResult:
        self.signal_processor.validate_identification_window(
            time_data=series.time,
            actuator_data=series.actuator,
            sensor_data=series.sensor,
        )

        if order == "fopdt":
            return self.identify_fopdt(series.time, series.actuator, series.sensor)

        if order == "sopdt":
            return self.identify_sopdt(series.time, series.actuator, series.sensor)

        if order == "integrating":
            return self.identify_integrating(series.time, series.actuator, series.sensor)

        if order == "auto":
            results = []

            for fn in (
                self.identify_fopdt,
                self.identify_sopdt,
                self.identify_integrating,
            ):
                try:
                    results.append(fn(series.time, series.actuator, series.sensor))
                except Exception:
                    pass

            best = self.model_ranker.best(results)
            if best is None:
                raise ValueError("No se pudo identificar ningún modelo válido.")

            return best

        raise ValueError(f"Orden/modelo no soportado: {order}")

    def compare_models(self, series) -> list[IdentificationResult]:
        """Ajusta los tres modelos y los devuelve rankeados por R²."""
        results, _ = self.compare_models_detailed(series)
        return results

    def compare_models_detailed(
        self, series
    ) -> tuple[list[IdentificationResult], list[dict]]:
        """
        Como `compare_models`, pero devuelve también los modelos DESCARTADOS
        con su motivo.

        Antes un modelo que fallaba se tragaba con `except: pass` y
        desaparecía de la vista sin explicación: el usuario veía tres tarjetas
        en un ensayo y dos en el siguiente, sin manera de saber si el modelo
        no aplicaba, si los datos no daban, o si algo se había roto.
        """
        self.signal_processor.validate_identification_window(
            time_data=series.time,
            actuator_data=series.actuator,
            sensor_data=series.sensor,
        )

        results: list[IdentificationResult] = []
        discarded: list[dict] = []

        candidatos = (
            ("fopdt", self.identify_fopdt),
            ("sopdt", self.identify_sopdt),
            ("integrating", self.identify_integrating),
        )

        for model_type, fn in candidatos:
            try:
                results.append(fn(series.time, series.actuator, series.sensor))
            except Exception as exc:
                discarded.append(
                    {
                        "model_type": model_type,
                        "reason": str(exc) or type(exc).__name__,
                    }
                )

        return self.model_ranker.rank(results), discarded

    def is_good_result(self, result: IdentificationResult) -> bool:
        if result.fit_quality < 0.5:
            return False

        if result.model.model_type == "fopdt":
            return result.model.tau is not None and result.model.tau > 1e-4

        if result.model.model_type == "sopdt":
            return (
                result.model.tau1 is not None and result.model.tau1 > 1e-4 and
                result.model.tau2 is not None and result.model.tau2 > 1e-4
            )

        if result.model.model_type == "integrating":
            return abs(result.model.gain) > 1e-6

        return False