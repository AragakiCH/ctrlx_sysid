"""
La línea base tiene que estar asentada antes del escalón.

Todo el ajuste parte de ahí: `initial_y` se toma como el régimen permanente que
corresponde a `initial_u`, y la respuesta simulada arranca desde ese punto
suponiendo equilibrio. Si el sensor venía moviéndose por un cambio anterior, la
ganancia absorbe la deriva y el modelo describe algo que no pasó — aunque el R²
pueda seguir viéndose alto.

Es el caso de encadenar escalones sin dejar asentar el proceso entre uno y otro.
"""

import pytest

from application.services.identification_pipeline_service import (
    IdentificationPipelineService,
)
from application.services.identification_service import IdentificationService
from application.services.step_detector_service import StepDetectorService
from domain.models.signals import SignalSeries


@pytest.fixture
def pipeline():
    return IdentificationPipelineService(
        identification_service=IdentificationService(),
        step_detector_service=StepDetectorService(min_step_delta=5.0),
    )


def ventana(actuador, sensor, dt=0.2):
    n = len(actuador)
    return SignalSeries(
        time=[i * dt for i in range(n)],
        actuator=actuador,
        sensor=sensor,
        setpoint=[actuador[-1]] * n,
        signal_type=1,
    )


class TestPlantaEnReposo:
    def test_linea_base_plana_esta_asentada(self, pipeline):
        u = [25.0] * 20 + [50.0] * 60
        y = [20.0] * 20 + [20.0 + 25 * (1 - 0.9**i) for i in range(60)]

        base = pipeline.describe_baseline(ventana(u, y))

        assert base["settled"] is True
        assert base["drift"] == pytest.approx(0.0, abs=1e-6)

    def test_ruido_pequeno_sigue_contando_como_asentada(self, pipeline):
        """Un 10 % de la respuesta es tolerancia razonable de medición."""
        u = [25.0] * 20 + [50.0] * 60
        y = [20.0 + (0.3 if i % 2 else -0.3) for i in range(20)]
        y += [20.0 + 25 * (1 - 0.9**i) for i in range(60)]

        base = pipeline.describe_baseline(ventana(u, y))

        assert base["settled"] is True

    def test_sensor_aun_subiendo_no_esta_asentada(self, pipeline):
        """El caso de la escalera: el escalón llega antes de que la planta pare."""
        u = [25.0] * 20 + [50.0] * 60
        # La línea base viene remontando de un escalón anterior.
        y = [8.0 + 12.0 * (1 - 0.85**i) for i in range(20)]
        y += [y[-1] + 25 * (1 - 0.9**i) for i in range(60)]

        base = pipeline.describe_baseline(ventana(u, y))

        assert base["settled"] is False
        assert base["ratio"] > 0.10
        assert base["drift"] > 0

    def test_sin_escalon_en_la_ventana_no_opina(self, pipeline):
        u = [25.0] * 50
        y = [20.0] * 50

        base = pipeline.describe_baseline(ventana(u, y))

        assert base["samples"] == 0
        assert base["settled"] is True

    def test_escalon_al_principio_no_deja_linea_base(self, pipeline):
        """Sin muestras previas no hay nada que evaluar, y no se inventa un aviso."""
        u = [25.0] + [50.0] * 50
        y = [20.0] + [20.0 + 25 * (1 - 0.9**i) for i in range(50)]

        base = pipeline.describe_baseline(ventana(u, y))

        assert base["samples"] == 0
        assert base["settled"] is True

    def test_ventana_minuscula_no_revienta(self, pipeline):
        assert pipeline.describe_baseline(ventana([25.0, 50.0], [20.0, 30.0]))[
            "samples"
        ] == 0


class TestPayload:
    def test_el_resultado_incluye_la_linea_base(self, pipeline):
        u = [25.0] * 20 + [50.0] * 80
        y = [20.0] * 20 + [20.0 + 25 * (1 - 0.93**i) for i in range(80)]

        resultado = pipeline.process_series(
            ventana(u, y), pre_samples=15, post_samples=70
        )

        assert resultado is not None
        assert "baseline" in resultado
        assert resultado["baseline"]["settled"] is True

    def test_simulated_cuadra_con_la_ventana(self, pipeline):
        """
        Es lo que rompía el gráfico: la UI pintaba `simulated` contra el tiempo
        del buffer completo, así que el modelo salía desplazado al principio y
        cortado a los pocos segundos.
        """
        u = [25.0] * 20 + [50.0] * 80
        y = [20.0] * 20 + [20.0 + 25 * (1 - 0.93**i) for i in range(80)]

        resultado = pipeline.process_series(
            ventana(u, y), pre_samples=15, post_samples=70
        )

        cuenta = resultado["window"]["count"]
        assert len(resultado["window"]["time"]) == cuenta
        for modelo in resultado["models"]:
            assert len(modelo["simulated"]) == cuenta
