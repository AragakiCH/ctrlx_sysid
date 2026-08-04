import pytest

from domain.services.scale_converter import (
    convert,
    from_percent,
    get_scale,
    list_scales,
    normalize_scale_key,
    to_percent,
)


class TestNormalizeScaleKey:
    def test_reconoce_las_claves_de_la_vista(self):
        assert normalize_scale_key("ma") == "ma"
        assert normalize_scale_key("pct") == "pct"
        assert normalize_scale_key("v") == "v"

    def test_tolera_alias_y_mayusculas(self):
        assert normalize_scale_key("  MA ") == "ma"
        assert normalize_scale_key("Voltios") == "v"
        assert normalize_scale_key("porcentaje") == "pct"
        assert normalize_scale_key("4-20 mA") == "ma"

    def test_cae_al_default_si_no_reconoce(self):
        assert normalize_scale_key("bar") == "ma"
        assert normalize_scale_key(None) == "ma"


class TestToPercent:
    @pytest.mark.parametrize(
        "value, scale, expected",
        [
            (4.0, "ma", 0.0),
            (12.0, "ma", 50.0),
            (20.0, "ma", 100.0),
            (8.0, "ma", 25.0),
            (0.0, "v", 0.0),
            (5.0, "v", 50.0),
            (10.0, "v", 100.0),
            (37.5, "pct", 37.5),
        ],
    )
    def test_convierte_a_porcentaje_de_span(self, value, scale, expected):
        assert to_percent(value, scale) == pytest.approx(expected)

    def test_valores_fuera_de_rango_extrapolan(self):
        # No se hace clamp: si el PLC entrega 2 mA hay que verlo, no esconderlo.
        assert to_percent(2.0, "ma") == pytest.approx(-12.5)
        assert to_percent(24.0, "ma") == pytest.approx(125.0)

    def test_no_numerico_devuelve_none(self):
        assert to_percent(None, "ma") is None
        assert to_percent("12", "ma") is None
        assert to_percent(True, "ma") is None


class TestFromPercent:
    @pytest.mark.parametrize(
        "percent, scale, expected",
        [
            (0.0, "ma", 4.0),
            (50.0, "ma", 12.0),
            (100.0, "ma", 20.0),
            (50.0, "v", 5.0),
            (25.0, "pct", 25.0),
        ],
    )
    def test_convierte_desde_porcentaje(self, percent, scale, expected):
        assert from_percent(percent, scale) == pytest.approx(expected)


class TestConvert:
    def test_ma_a_voltios(self):
        assert convert(12.0, "ma", "v") == pytest.approx(5.0)

    def test_voltios_a_ma(self):
        assert convert(5.0, "v", "ma") == pytest.approx(12.0)

    def test_ida_y_vuelta_es_identidad(self):
        for value in (4.0, 8.0, 12.0, 16.0, 20.0):
            ida = convert(value, "ma", "v")
            vuelta = convert(ida, "v", "ma")
            assert vuelta == pytest.approx(value)

    def test_misma_escala_no_cambia_el_valor(self):
        assert convert(7.3, "ma", "ma") == pytest.approx(7.3)


class TestCatalogo:
    def test_lista_las_tres_escalas(self):
        keys = {s["key"] for s in list_scales()}
        assert keys == {"ma", "pct", "v"}

    def test_span_correcto(self):
        assert get_scale("ma").span == pytest.approx(16.0)
        assert get_scale("v").span == pytest.approx(10.0)
        assert get_scale("pct").span == pytest.approx(100.0)

    def test_contains(self):
        assert get_scale("ma").contains(4.0)
        assert get_scale("ma").contains(20.0)
        assert not get_scale("ma").contains(3.9)
        assert not get_scale("v").contains(12.0)
