import pytest

from application.services.test_config_service import TestConfigService, normalize_order


@pytest.fixture
def service():
    return TestConfigService(sample_period_s=0.2)


class TestNormalizeOrder:
    def test_acepta_los_valores_del_combo(self):
        assert normalize_order("1") == "fopdt"
        assert normalize_order("2") == "sopdt"
        assert normalize_order("0") == "integrating"
        assert normalize_order("auto") == "auto"

    def test_acepta_los_nombres_del_modelo(self):
        assert normalize_order("FOPDT") == "fopdt"
        assert normalize_order("integrante") == "integrating"

    def test_desconocido_cae_en_auto(self):
        assert normalize_order("tercer orden") == "auto"
        assert normalize_order(None) == "auto"


class TestScales:
    def test_default_es_4_20_ma(self, service):
        assert service.get_scales() == {
            "actuator": "ma",
            "sensor": "ma",
            "setpoint": "ma",
        }

    def test_set_parcial_no_toca_los_demas(self, service):
        service.set_scales({"sensor": "v"})

        assert service.scale_for("sensor") == "v"
        assert service.scale_for("actuator") == "ma"
        assert service.scale_for("setpoint") == "ma"

    def test_describe_incluye_catalogo(self, service):
        payload = service.describe_scales()

        assert set(payload["scales"]) == {"actuator", "sensor", "setpoint"}
        assert {s["key"] for s in payload["available"]} == {"ma", "pct", "v"}
        assert payload["detail"]["actuator"]["unit"] == "mA"


class TestStepConfig:
    def test_defaults_25_a_50_por_ciento(self, service):
        derived = service.describe_step_config()["derived"]

        assert derived["step_from_pct"] == pytest.approx(25.0)
        assert derived["step_to_pct"] == pytest.approx(50.0)
        assert derived["delta_pct"] == pytest.approx(25.0)
        assert derived["direction"] == "up"

    def test_ventana_sale_del_retardo_y_la_duracion(self, service):
        service.set_step_config(duration_s=60, delay_s=10, sample_period_s=0.2)

        # 10 s / 0.2 s = 50 muestras de línea base
        assert service.pre_samples() == 50
        # (60 - 10) / 0.2 = 250 muestras de respuesta
        assert service.post_samples() == 250
        assert service.expected_samples() == 300

    def test_umbral_es_la_mitad_del_salto(self, service):
        service.set_step_config(step_from=8.0, step_to=12.0)  # 25 % de salto
        assert service.step_threshold_pct() == pytest.approx(12.5)

    def test_umbral_tiene_piso_de_uno_por_ciento(self, service):
        service.set_step_config(step_from=8.0, step_to=8.1)  # salto minúsculo
        assert service.step_threshold_pct() == pytest.approx(1.0)

    def test_escalon_descendente(self, service):
        derived = service.set_step_config(step_from=16.0, step_to=8.0)["derived"]

        assert derived["direction"] == "down"
        assert derived["delta"] == pytest.approx(-8.0)
        assert derived["delta_pct"] == pytest.approx(-50.0)
        # El umbral es sobre el valor absoluto del salto.
        assert derived["step_threshold_pct"] == pytest.approx(25.0)

    def test_campos_omitidos_conservan_su_valor(self, service):
        service.set_step_config(step_from=6.0, step_to=18.0, duration_s=90)
        step = service.set_step_config(order="1")

        assert step["step_from"] == pytest.approx(6.0)
        assert step["step_to"] == pytest.approx(18.0)
        assert step["duration_s"] == pytest.approx(90.0)
        assert step["order"] == "fopdt"

    def test_la_escala_del_actuador_reinterpreta_los_valores(self, service):
        service.set_scales({"actuator": "v"})
        derived = service.set_step_config(step_from=2.5, step_to=5.0)["derived"]

        assert derived["step_from_pct"] == pytest.approx(25.0)
        assert derived["step_to_pct"] == pytest.approx(50.0)


class TestValidacion:
    def test_rechaza_valor_fuera_de_escala(self, service):
        service.set_scales({"actuator": "v"})

        with pytest.raises(ValueError, match="fuera de la escala"):
            service.set_step_config(step_from=2.0, step_to=12.0)

    def test_rechaza_salto_nulo(self, service):
        with pytest.raises(ValueError, match="no hay escalón"):
            service.set_step_config(step_from=10.0, step_to=10.0)

    def test_rechaza_retardo_mayor_que_duracion(self, service):
        with pytest.raises(ValueError, match="menor que la duración"):
            service.set_step_config(duration_s=30, delay_s=30)

    def test_rechaza_ventana_demasiado_corta(self, service):
        # (12 - 10) / 0.2 = 10 muestras de respuesta, por debajo del mínimo.
        with pytest.raises(ValueError, match="muestras después"):
            service.set_step_config(duration_s=12, delay_s=10, sample_period_s=0.2)

    def test_rechaza_muestreo_no_positivo(self, service):
        with pytest.raises(ValueError, match="mayor que cero"):
            service.set_step_config(sample_period_s=0.0)

    def test_una_config_invalida_no_deja_estado_a_medias(self, service):
        antes = service.describe_step_config()

        with pytest.raises(ValueError):
            service.set_step_config(step_from=10.0, step_to=10.0)

        assert service.describe_step_config() == antes


class TestPreview:
    def test_genera_el_escalon_en_la_escala_del_actuador(self, service):
        service.set_step_config(step_from=8.0, step_to=12.0, duration_s=100, delay_s=20)
        preview = service.build_preview(points=11)

        assert preview["unit"] == "mA"
        assert len(preview["time"]) == 11
        assert preview["time"][0] == pytest.approx(0.0)
        assert preview["time"][-1] == pytest.approx(100.0)
        # Antes de los 20 s vale 8 mA, después 12 mA.
        assert preview["actuator"][0] == pytest.approx(8.0)
        assert preview["actuator"][-1] == pytest.approx(12.0)

    def test_puede_pedirse_en_otra_escala_sin_cambiar_la_config(self, service):
        service.set_step_config(step_from=8.0, step_to=12.0)
        preview = service.build_preview(points=5, scale_key="pct")

        assert preview["unit"] == "%"
        assert preview["from_value"] == pytest.approx(25.0)
        assert preview["to_value"] == pytest.approx(50.0)
        # La configuración guardada sigue en mA.
        assert service.describe_step_config()["step_from"] == pytest.approx(8.0)
