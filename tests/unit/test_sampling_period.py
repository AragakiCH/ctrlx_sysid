"""
Periodo de muestreo real y saneado del eje de tiempo.

Las observaciones del sintonizador reportaban dos síntomas que resultaron ser
el mismo problema: "ya terminó el ensayo pero solo hay 21 muestras" y un ajuste
con R² de −878 % pero ganancia correcta. Ambos vienen de confiar en dos cosas
que nadie verificaba: que el muestreo iba al periodo configurado, y que el eje
de tiempo era monótono.
"""

import math

import pytest

from application.services.realtime_service import RealtimeService
from application.services.test_config_service import TestConfigService


def cargar(svc, dt, n, wrap=None, tau=0.1, t_step=10.0):
    """Carga un ensayo sintético. Con `wrap`, el tiempo se reinicia cada N s."""
    for i in range(n):
        t = i * dt
        u = 25.0 if t < t_step else 50.0
        te = t - t_step
        y = 20.0 if te <= 0 else 20.0 + 25.0 * (1 - math.exp(-te / tau))

        svc.add_sample(
            {
                "time": t if wrap is None else t % wrap,
                "actuator": u,
                "sensor": y,
                "setpoint": 50.0,
                "signal_type": 0,
            }
        )


@pytest.fixture
def servicio():
    config = TestConfigService(sample_period_s=0.2)
    config.set_scales({"actuator": "pct", "sensor": "pct", "setpoint": "pct"})
    return RealtimeService(scale_provider=config.scale_for)


class TestPeriodoMedido:
    def test_mide_el_periodo_real(self, servicio):
        cargar(servicio, dt=0.95, n=40)
        assert servicio.measured_period_s() == pytest.approx(0.95, abs=0.01)

    def test_hacen_falta_al_menos_tres_muestras(self, servicio):
        cargar(servicio, dt=0.2, n=2)
        assert servicio.measured_period_s() is None

    def test_un_pico_aislado_no_mueve_la_mediana(self, servicio):
        """Un hipo de red no debe cambiar la estimación del periodo."""
        for i, t in enumerate([0.0, 0.2, 0.4, 0.6, 5.0, 5.2, 5.4, 5.6]):
            servicio.add_sample(
                {"time": t, "actuator": 25.0, "sensor": 20.0, "setpoint": 50.0}
            )

        assert servicio.measured_period_s() == pytest.approx(0.2, abs=0.01)

    def test_el_reporte_delata_el_muestreo_lento(self, servicio):
        cargar(servicio, dt=0.95, n=40)
        reporte = servicio.sampling_report(nominal_period_s=0.2)

        assert reporte["ratio"] == pytest.approx(4.75, abs=0.05)
        assert reporte["effective_rate_hz"] == pytest.approx(1.05, abs=0.05)

    def test_muestreo_correcto_da_ratio_uno(self, servicio):
        cargar(servicio, dt=0.2, n=60)
        assert servicio.sampling_report(nominal_period_s=0.2)["ratio"] == pytest.approx(
            1.0, abs=0.05
        )


class TestEjeDeTiempoMonotono:
    def test_un_contador_que_se_reinicia_no_produce_dt_negativos(self, servicio):
        """
        `IF rTimeSec >= 20 THEN rTimeSec := 0` da un eje en diente de sierra.
        Al integrar el modelo con dt negativo la respuesta simulada se dispara.
        """
        cargar(servicio, dt=0.95, n=60, wrap=20.0)
        serie = servicio.get_signal_series(use_percent=True)

        assert all(
            serie.time[i] > serie.time[i - 1] for i in range(1, len(serie.time))
        )

    def test_el_tiempo_monotono_no_pierde_muestras(self, servicio):
        cargar(servicio, dt=0.2, n=100)
        serie = servicio.get_signal_series(use_percent=True)

        assert len(serie.time) == 100


class TestSetpointOpcional:
    def test_sin_setpoint_las_muestras_siguen_contando(self, servicio):
        """
        El SP es opcional en la vista. Exigirlo aquí vaciaba la serie entera:
        los gráficos se veían llenos y el backend reportaba cero muestras.
        """
        for i in range(50):
            servicio.add_sample(
                {
                    "time": i * 0.2,
                    "actuator": 25.0 if i < 25 else 50.0,
                    "sensor": 20.0,
                    "setpoint": None,
                    "signal_type": 0,
                }
            )

        serie = servicio.get_signal_series(use_percent=True)

        assert len(serie.time) == 50
        assert len(serie.setpoint) == 50

    def test_sin_sensor_si_se_descarta(self, servicio):
        """El sensor no es opcional: sin salida no hay nada que identificar."""
        for i in range(50):
            servicio.add_sample(
                {"time": i * 0.2, "actuator": 25.0, "sensor": None, "setpoint": 50.0}
            )

        assert len(servicio.get_signal_series(use_percent=True).time) == 0


class TestVentanaConPeriodoMedido:
    def test_el_periodo_medido_redimensiona_la_ventana(self):
        config = TestConfigService(sample_period_s=0.2)
        config.set_scales({"actuator": "pct"})
        config.set_step_config(
            step_from=25, step_to=50, duration_s=20, delay_s=10, sample_period_s=0.2
        )

        # Con el nominal se piden 50 muestras de respuesta...
        assert config.post_samples() == 50
        # ...pero a 0.95 s reales en 10 s solo caben 10.
        assert config.post_samples(period_s=0.95) == max(
            config.MIN_POST_SAMPLES, 10
        )

    def test_la_ventana_en_segundos_no_depende_del_periodo(self):
        config = TestConfigService(sample_period_s=0.2)
        config.set_step_config(duration_s=20, delay_s=10, sample_period_s=0.2)

        assert config.baseline_seconds() == pytest.approx(10.0)
        assert config.response_seconds() == pytest.approx(10.0)
