import pytest

from application.services.identification_pipeline_service import (
    IdentificationPipelineService,
)
from application.services.identification_service import IdentificationService
from application.services.realtime_service import RealtimeService
from application.services.step_detector_service import StepDetectorService
from domain.models.signals import SignalSeries


@pytest.fixture
def pipeline():
    return IdentificationPipelineService(
        identification_service=IdentificationService(),
        step_detector_service=StepDetectorService(min_step_delta=1.0),
    )


@pytest.fixture
def series(fopdt_series):
    return SignalSeries(
        time=fopdt_series["time"],
        actuator=fopdt_series["actuator"],
        sensor=fopdt_series["sensor"],
        setpoint=[12.0] * len(fopdt_series["time"]),
        signal_type=1,
    )


def test_pipeline_devuelve_ganador_y_modelos(pipeline, series):
    result = pipeline.process_series(series, pre_samples=10, post_samples=120)

    assert result is not None
    assert result["winner"] in {"fopdt", "sopdt", "integrating"}
    assert len(result["models"]) >= 1


def test_serializa_la_curva_simulada(pipeline, series):
    """Sin esto el gráfico medido-vs-modelo nunca se dibujaba."""
    result = pipeline.process_series(series, pre_samples=10, post_samples=120)

    for model in result["models"]:
        assert isinstance(model["simulated"], list)
        assert len(model["simulated"]) == result["window"]["count"]


def test_la_ventana_cuadra_con_la_simulacion(pipeline, series):
    result = pipeline.process_series(series, pre_samples=10, post_samples=120)
    window = result["window"]

    assert len(window["time"]) == len(window["sensor"]) == window["count"]


def test_serializa_ti_td_y_descripcion(pipeline, series):
    result = pipeline.process_series(series, pre_samples=10, post_samples=120)

    tunings = [t for m in result["models"] for t in m["pid_tunings"]]
    assert tunings

    for t in tunings:
        assert "ti" in t and "td" in t
        assert t["description"]


def test_serializa_numerador_y_denominador(pipeline, series):
    result = pipeline.process_series(series, pre_samples=10, post_samples=120)

    for model in result["models"]:
        assert model["numerator"]
        assert model["denominator"]


def test_sin_escalon_no_identifica(pipeline):
    flat = SignalSeries(
        time=[i * 0.5 for i in range(100)],
        actuator=[4.0] * 100,
        sensor=[8.0] * 100,
        setpoint=[12.0] * 100,
        signal_type=1,
    )

    assert pipeline.process_series(flat) is None


def test_realtime_service_normaliza_a_porcentaje():
    service = RealtimeService(max_buffer_size=10)
    service.add_sample(
        {"time": 1.0, "actuator": 12.0, "sensor": 4.0, "setpoint": 20.0, "signal_type": 1}
    )

    latest = service.get_latest_sample()

    assert latest["actuator_pct"] == pytest.approx(50.0)
    assert latest["sensor_pct"] == pytest.approx(0.0)
    assert latest["setpoint_pct"] == pytest.approx(100.0)


def test_realtime_service_descarta_muestras_incompletas():
    service = RealtimeService(max_buffer_size=10)
    service.add_sample({"time": 1.0, "actuator": 12.0, "sensor": None, "setpoint": 20.0})
    service.add_sample({"time": 2.0, "actuator": 12.0, "sensor": 5.0, "setpoint": 20.0})

    assert len(service.get_signal_series().time) == 1
