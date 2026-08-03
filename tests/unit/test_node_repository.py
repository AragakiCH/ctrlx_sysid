from infrastructure.ctrlx.node_repository import (
    SIGNAL_ROLES,
    normalize_name,
    resolve_mapping,
    suggest_mapping,
)


def test_normalize_name_ignora_mayusculas_guiones_y_espacios():
    assert normalize_name("AO_Actuador mA") == normalize_name("ao-actuadorma")


def test_sugerencia_reconoce_los_nombres_clasicos():
    names = ["rTimeSec", "rActuator", "rSensor", "rSetPoint", "uiSignalType", "otra"]
    suggestion = suggest_mapping(names)

    assert suggestion["time"] == "rTimeSec"
    assert suggestion["actuator"] == "rActuator"
    assert suggestion["sensor"] == "rSensor"
    assert suggestion["setpoint"] == "rSetPoint"
    assert suggestion["signal_type"] == "uiSignalType"


def test_sugerencia_devuelve_none_si_no_reconoce_nada():
    suggestion = suggest_mapping(["varA", "varB", "varC"])

    assert suggestion["actuator"] is None
    assert suggestion["time"] is None


def test_sugerencia_cubre_todos_los_roles():
    assert set(suggest_mapping(["x"]).keys()) == set(SIGNAL_ROLES)


def test_el_mapeo_del_usuario_gana_sobre_los_alias():
    """Lo elegido en la vista manda, aunque exista una variable con nombre clásico."""
    names = ["rActuator", "MiValvula", "rSensor", "MiTemperatura", "t"]

    resolved = resolve_mapping(
        {"actuator": "MiValvula", "sensor": "MiTemperatura"},
        names,
    )

    assert resolved["actuator"] == "MiValvula"
    assert resolved["sensor"] == "MiTemperatura"


def test_roles_vacios_caen_al_alias():
    names = ["rActuator", "rSensor", "rTimeSec"]

    resolved = resolve_mapping({"actuator": "rActuator"}, names)

    assert resolved["sensor"] == "rSensor"
    assert resolved["time"] == "rTimeSec"


def test_mapeo_none_usa_solo_alias():
    names = ["rActuator", "rSensor", "rTimeSec"]

    assert resolve_mapping(None, names) == suggest_mapping(names)


def test_resuelve_ignorando_diferencias_de_formato():
    resolved = resolve_mapping({"actuator": "mi_valvula"}, ["MiValvula"])
    assert resolved["actuator"] == "MiValvula"


def test_nombres_totalmente_personalizados():
    """Caso real: un programa PLC sin ninguno de los nombres por defecto."""
    names = ["Tiempo_Proceso", "Valvula_Salida", "Temp_Horno", "Consigna", "Modo"]

    resolved = resolve_mapping(
        {
            "time": "Tiempo_Proceso",
            "actuator": "Valvula_Salida",
            "sensor": "Temp_Horno",
            "setpoint": "Consigna",
            "signal_type": "Modo",
        },
        names,
    )

    assert resolved["time"] == "Tiempo_Proceso"
    assert resolved["actuator"] == "Valvula_Salida"
    assert resolved["sensor"] == "Temp_Horno"
    assert resolved["setpoint"] == "Consigna"
    assert resolved["signal_type"] == "Modo"
