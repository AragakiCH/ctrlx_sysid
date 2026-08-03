from infrastructure.ctrlx.plc_reader import PLCReader


def make_reader(mapping=None):
    return PLCReader(
        url="opc.tcp://x:4840",
        user="u",
        password="p",
        program_name="PLC_PRG",
        mapping=mapping,
    )


RAW_CUSTOM = {
    "Tiempo_Proceso": 12.5,
    "Valvula_Salida": 11.2,
    "Temp_Horno": 8.4,
    "Consigna": 12.0,
    "Modo": 1,
}

RAW_CLASSIC = {
    "rTimeSec": 5.99,
    "rActuator": 12.0,
    "rSensor": 7.21,
    "rSetPoint": 12.0,
    "uiSignalType": 1,
}


def test_lee_variables_con_nombres_personalizados():
    reader = make_reader(
        {
            "time": "Tiempo_Proceso",
            "actuator": "Valvula_Salida",
            "sensor": "Temp_Horno",
            "setpoint": "Consigna",
            "signal_type": "Modo",
        }
    )

    effective = reader._resolve_effective_mapping(RAW_CUSTOM)

    assert reader._value_for_role(RAW_CUSTOM, "actuator", effective) == 11.2
    assert reader._value_for_role(RAW_CUSTOM, "sensor", effective) == 8.4
    assert reader._value_for_role(RAW_CUSTOM, "time", effective) == 12.5


def test_sin_mapping_cae_a_los_alias_clasicos():
    reader = make_reader(None)
    effective = reader._resolve_effective_mapping(RAW_CLASSIC)

    assert reader._value_for_role(RAW_CLASSIC, "actuator", effective) == 12.0
    assert reader._value_for_role(RAW_CLASSIC, "sensor", effective) == 7.21


def test_mapping_parcial_completa_con_alias():
    reader = make_reader({"sensor": "rSensor"})
    effective = reader._resolve_effective_mapping(RAW_CLASSIC)

    assert reader._value_for_role(RAW_CLASSIC, "sensor", effective) == 7.21
    assert reader._value_for_role(RAW_CLASSIC, "time", effective) == 5.99


def test_variable_inexistente_no_revienta():
    reader = make_reader({"actuator": "NoExiste"})
    effective = reader._resolve_effective_mapping(RAW_CUSTOM)

    assert reader._value_for_role(RAW_CUSTOM, "actuator", effective) is None


def test_set_mapping_cambia_la_resolucion_en_caliente():
    reader = make_reader({"actuator": "Valvula_Salida"})
    effective = reader._resolve_effective_mapping(RAW_CUSTOM)
    assert reader._value_for_role(RAW_CUSTOM, "actuator", effective) == 11.2

    reader.set_mapping({"actuator": "Consigna"})
    effective = reader._resolve_effective_mapping(RAW_CUSTOM)
    assert reader._value_for_role(RAW_CUSTOM, "actuator", effective) == 12.0
