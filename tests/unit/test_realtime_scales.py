"""El RealtimeService convierte cada rol con la escala que fijó la vista."""

import pytest

from application.services.realtime_service import RealtimeService
from application.services.test_config_service import TestConfigService


def make_sample(actuator=12.0, sensor=8.0, setpoint=16.0, signal_type=1, t=0.0):
    return {
        "time": t,
        "actuator": actuator,
        "sensor": sensor,
        "setpoint": setpoint,
        "signal_type": signal_type,
    }


class TestSinProveedor:
    """Comportamiento heredado: manda la variable `uiSignalType` del PLC."""

    def test_signal_type_1_convierte_desde_4_20_ma(self):
        service = RealtimeService()
        out = service.normalize_sample(make_sample(signal_type=1))

        assert out["actuator_pct"] == pytest.approx(50.0)
        assert out["sensor_pct"] == pytest.approx(25.0)
        assert out["setpoint_pct"] == pytest.approx(75.0)

    def test_signal_type_0_deja_el_valor_como_porcentaje(self):
        service = RealtimeService()
        out = service.normalize_sample(make_sample(signal_type=0))

        assert out["actuator_pct"] == pytest.approx(12.0)
        assert out["sensor_pct"] == pytest.approx(8.0)


class TestConProveedor:
    """Con TestConfigService inyectado, manda lo que eligió el usuario."""

    def test_la_escala_de_la_vista_pisa_a_signal_type(self):
        config = TestConfigService()
        config.set_scales({"actuator": "pct", "sensor": "pct", "setpoint": "pct"})

        service = RealtimeService(scale_provider=config.scale_for)
        # signal_type=1 diría "son mA", pero la vista declaró porcentaje.
        out = service.normalize_sample(make_sample(signal_type=1))

        assert out["actuator_pct"] == pytest.approx(12.0)
        assert out["sensor_pct"] == pytest.approx(8.0)

    def test_cada_rol_puede_tener_su_propia_escala(self):
        config = TestConfigService()
        config.set_scales({"actuator": "ma", "sensor": "v", "setpoint": "pct"})

        service = RealtimeService(scale_provider=config.scale_for)
        out = service.normalize_sample(
            make_sample(actuator=12.0, sensor=5.0, setpoint=40.0)
        )

        assert out["actuator_pct"] == pytest.approx(50.0)   # 12 mA de 4-20
        assert out["sensor_pct"] == pytest.approx(50.0)     # 5 V de 0-10
        assert out["setpoint_pct"] == pytest.approx(40.0)   # ya es %

    def test_incluye_las_escalas_usadas_en_la_muestra(self):
        config = TestConfigService()
        config.set_scales({"sensor": "v"})

        service = RealtimeService(scale_provider=config.scale_for)
        out = service.normalize_sample(make_sample())

        assert out["scales"] == {"actuator": "ma", "sensor": "v", "setpoint": "ma"}

    def test_cambiar_la_escala_afecta_a_las_muestras_nuevas(self):
        config = TestConfigService()
        service = RealtimeService(scale_provider=config.scale_for)

        antes = service.normalize_sample(make_sample(actuator=12.0))
        config.set_scales({"actuator": "pct"})
        despues = service.normalize_sample(make_sample(actuator=12.0))

        assert antes["actuator_pct"] == pytest.approx(50.0)
        assert despues["actuator_pct"] == pytest.approx(12.0)

    def test_valor_no_numerico_queda_en_none(self):
        config = TestConfigService()
        service = RealtimeService(scale_provider=config.scale_for)
        out = service.normalize_sample(make_sample(sensor=None))

        assert out["sensor_pct"] is None
        assert out["actuator_pct"] is not None


class TestRecomputePercent:
    """
    Cambiar la escala debe reinterpretar también lo ya capturado.

    Si solo afectara a las muestras nuevas, un ensayo grabado con la escala
    equivocada quedaría inservible y habría que repetirlo en el PLC.
    """

    def test_reinterpreta_el_buffer_existente(self):
        config = TestConfigService()
        config.set_scales({"actuator": "pct"})
        service = RealtimeService(scale_provider=config.scale_for)

        for i in range(10):
            service.add_sample(make_sample(actuator=12.0, t=i * 0.2))

        assert service.get_latest_sample()["actuator_pct"] == pytest.approx(12.0)

        config.set_scales({"actuator": "ma"})
        recalculadas = service.recompute_percent()

        assert recalculadas == 10
        assert service.get_latest_sample()["actuator_pct"] == pytest.approx(50.0)

    def test_conserva_el_orden_y_la_cantidad(self):
        config = TestConfigService()
        service = RealtimeService(scale_provider=config.scale_for)

        for i in range(50):
            service.add_sample(make_sample(t=i * 0.2))

        antes = [s["time"] for s in service.get_all_samples()]
        config.set_scales({"actuator": "v"})
        service.recompute_percent()

        assert [s["time"] for s in service.get_all_samples()] == antes

    def test_no_pierde_el_valor_crudo(self):
        config = TestConfigService()
        service = RealtimeService(scale_provider=config.scale_for)
        service.add_sample(make_sample(actuator=12.0))

        config.set_scales({"actuator": "pct"})
        service.recompute_percent()

        latest = service.get_latest_sample()
        assert latest["actuator"] == pytest.approx(12.0)
        assert latest["actuator_pct"] == pytest.approx(12.0)

    def test_buffer_vacio_no_falla(self):
        service = RealtimeService()
        assert service.recompute_percent() == 0


class TestSeriesEnPorcentaje:
    def test_get_signal_series_usa_los_campos_pct(self):
        config = TestConfigService()
        config.set_scales({"actuator": "ma", "sensor": "ma", "setpoint": "ma"})
        service = RealtimeService(scale_provider=config.scale_for)

        for i in range(5):
            service.add_sample(make_sample(t=i * 0.2))

        series = service.get_signal_series(use_percent=True)

        assert len(series.time) == 5
        assert series.actuator[0] == pytest.approx(50.0)
        assert series.sensor[0] == pytest.approx(25.0)
