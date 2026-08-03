import pytest

from domain.models.transfer_function import TransferFunctionModel
from domain.services.controller_tuner import ControllerTuner


@pytest.fixture
def tuner():
    return ControllerTuner()


@pytest.fixture
def fopdt_model():
    return TransferFunctionModel(
        model_type="fopdt",
        gain=2.0,
        tau=10.0,
        dead_time=2.0,
    )


def test_fopdt_devuelve_los_cuatro_metodos(tuner, fopdt_model):
    tunings = tuner.tune_fopdt(fopdt_model)
    methods = {t.method for t in tunings}

    assert methods == {"IMC", "Ziegler-Nichols", "Cohen-Coon", "SIMC"}


def test_todas_las_sintonias_traen_ti_y_descripcion(tuner, fopdt_model):
    for t in tuner.tune_fopdt(fopdt_model):
        assert t.ti is not None and t.ti > 0
        assert t.td is not None
        assert t.description


def test_ki_es_coherente_con_kp_y_ti(tuner, fopdt_model):
    for t in tuner.tune_fopdt(fopdt_model):
        assert t.ki == pytest.approx(t.kp / t.ti, rel=1e-9)
        assert t.kd == pytest.approx(t.kp * t.td, rel=1e-9)


def test_ziegler_nichols_usa_la_formula_de_reaccion(tuner, fopdt_model):
    zn = next(t for t in tuner.tune_fopdt(fopdt_model) if t.method == "Ziegler-Nichols")

    k, tau, l = fopdt_model.gain, fopdt_model.tau, fopdt_model.dead_time
    assert zn.kp == pytest.approx(1.2 * tau / (k * l))
    assert zn.ti == pytest.approx(2.0 * l)
    assert zn.td == pytest.approx(0.5 * l)


def test_simc_sigue_la_regla_estandar(tuner, fopdt_model):
    simc = next(t for t in tuner.tune_fopdt(fopdt_model) if t.method == "SIMC")

    k, tau, l = fopdt_model.gain, fopdt_model.tau, fopdt_model.dead_time
    assert simc.kp == pytest.approx(tau / (k * 2.0 * l))
    assert simc.ti == pytest.approx(min(tau, 8.0 * l))


def test_kp_baja_cuando_crece_el_tiempo_muerto(tuner):
    """Más tiempo muerto => control más suave."""
    poco = tuner.tune_fopdt(
        TransferFunctionModel(model_type="fopdt", gain=2.0, tau=10.0, dead_time=1.0)
    )
    mucho = tuner.tune_fopdt(
        TransferFunctionModel(model_type="fopdt", gain=2.0, tau=10.0, dead_time=5.0)
    )

    kp_poco = next(t for t in poco if t.method == "SIMC").kp
    kp_mucho = next(t for t in mucho if t.method == "SIMC").kp

    assert kp_mucho < kp_poco


def test_sopdt_usa_media_regla_de_skogestad(tuner):
    model = TransferFunctionModel(
        model_type="sopdt",
        gain=2.0,
        tau1=12.0,
        tau2=4.0,
        dead_time=1.0,
    )

    tunings = tuner.tune_sopdt(model)

    assert tunings
    assert all("(SOPDT eq.)" in t.method for t in tunings)

    # tau_eq = 12 + 4/2 = 14 ; L_eq = 1 + 4/2 = 3
    simc = next(t for t in tunings if t.method.startswith("SIMC"))
    assert simc.kp == pytest.approx(14.0 / (2.0 * 2.0 * 3.0))


def test_integrating_depende_del_tiempo_muerto(tuner):
    """Antes esta sintonía era constante por un bug: L siempre era 0."""
    corto = tuner.tune_integrating(
        TransferFunctionModel(model_type="integrating", gain=0.05, dead_time=1.0)
    )
    largo = tuner.tune_integrating(
        TransferFunctionModel(model_type="integrating", gain=0.05, dead_time=6.0)
    )

    assert corto and largo
    assert corto[0].kp != largo[0].kp
    assert largo[0].kp < corto[0].kp


def test_no_sintoniza_modelo_de_otro_tipo(tuner, fopdt_model):
    assert tuner.tune_sopdt(fopdt_model) == []
    assert tuner.tune_integrating(fopdt_model) == []


def test_ganancia_cero_no_revienta(tuner):
    model = TransferFunctionModel(model_type="fopdt", gain=0.0, tau=10.0, dead_time=2.0)
    assert tuner.tune_fopdt(model) == []


def test_dispatcher_generico(tuner, fopdt_model):
    assert tuner.tune(fopdt_model) == tuner.tune_fopdt(fopdt_model)
