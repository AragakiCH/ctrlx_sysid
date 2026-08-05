"""Resolución y escritura de la variable del actuador en el PLCReader."""

import pytest

from infrastructure.ctrlx.plc_reader import PLCReader


class FakeNode:
    """Nodo OPC UA mínimo: browse name y valor."""

    def __init__(self, name, value=0.0, writable=True):
        self._name = name
        self.value = value
        self.writable = writable

    def get_browse_name(self):
        return type("BN", (), {"Name": self._name})()

    def get_children(self):
        return []


class FakeProgramNode:
    def __init__(self, nodes):
        self._nodes = nodes

    def get_children(self):
        return self._nodes


def make_reader(mapping=None, nodes=None, connected=True):
    reader = PLCReader(
        url="opc.tcp://x:4840",
        user="u",
        password="p",
        program_name="PLC_PRG",
        mapping=mapping,
    )

    if connected:
        reader._program_node = FakeProgramNode(nodes or [])

        # Se sustituye el cliente OPC UA por uno de mentira: aquí interesa la
        # resolución de la variable y el manejo de errores, no el protocolo.
        escrituras = []

        class FakeOpc:
            def is_writable(self, node):
                return node.writable

            def write_value(self, node, value):
                if not node.writable:
                    raise RuntimeError("BadUserAccessDenied")
                node.value = value
                escrituras.append((node._name, value))

        reader._opc = FakeOpc()
        reader.escrituras = escrituras

        # is_running mira el hilo; en el test no lo hay.
        type(reader).is_running = property(lambda self: True)

    return reader


@pytest.fixture(autouse=True)
def restaurar_is_running():
    original = PLCReader.is_running
    yield
    PLCReader.is_running = original


# --------------------------------------------------------------------------- #
# Resolución de la variable
# --------------------------------------------------------------------------- #


def test_usa_la_variable_mapeada_por_el_usuario():
    reader = make_reader(mapping={"actuator": "MiValvula"})
    assert reader.resolve_role_variable("actuator") == "MiValvula"


def test_sin_mapeo_cae_a_los_alias():
    nodes = [FakeNode("rActuator"), FakeNode("rSensor")]
    reader = make_reader(mapping=None, nodes=nodes)
    assert reader.resolve_role_variable("actuator") == "rActuator"


def test_escribe_en_la_variable_elegida():
    nodes = [FakeNode("rActuator"), FakeNode("MiValvula")]
    reader = make_reader(mapping={"actuator": "MiValvula"}, nodes=nodes)

    reader.write_role_value("actuator", 12.0)

    # Escribe en la que eligió el usuario, no en la del nombre clásico.
    assert reader.escrituras == [("MiValvula", 12.0)]


def test_resuelve_ignorando_mayusculas_y_guiones():
    nodes = [FakeNode("Valvula_Salida")]
    reader = make_reader(mapping={"actuator": "valvula salida"}, nodes=nodes)

    reader.write_role_value("actuator", 9.5)

    assert reader.escrituras == [("Valvula_Salida", 9.5)]


# --------------------------------------------------------------------------- #
# Comprobación previa
# --------------------------------------------------------------------------- #


def test_can_write_role_acepta_una_variable_escribible():
    nodes = [FakeNode("rActuator", writable=True)]
    reader = make_reader(mapping={"actuator": "rActuator"}, nodes=nodes)

    ok, motivo = reader.can_write_role("actuator")

    assert ok is True
    assert "rActuator" in motivo


def test_can_write_role_rechaza_una_de_solo_lectura():
    nodes = [FakeNode("rActuator", writable=False)]
    reader = make_reader(mapping={"actuator": "rActuator"}, nodes=nodes)

    ok, motivo = reader.can_write_role("actuator")

    assert ok is False
    assert "solo lectura" in motivo


def test_can_write_role_avisa_si_la_variable_no_existe():
    reader = make_reader(mapping={"actuator": "NoExiste"}, nodes=[FakeNode("otra")])

    ok, motivo = reader.can_write_role("actuator")

    assert ok is False
    assert "no existe" in motivo


def test_can_write_role_avisa_si_no_hay_conexion():
    reader = make_reader(connected=False)

    ok, motivo = reader.can_write_role("actuator")

    assert ok is False
    assert "no está conectado" in motivo


# --------------------------------------------------------------------------- #
# Errores
# --------------------------------------------------------------------------- #


def test_escribir_sin_conexion_lanza():
    reader = make_reader(mapping={"actuator": "rActuator"}, connected=False)

    with pytest.raises(RuntimeError, match="Sin conexión"):
        reader.write_role_value("actuator", 12.0)


def test_escribir_en_variable_inexistente_lanza():
    reader = make_reader(mapping={"actuator": "NoExiste"}, nodes=[FakeNode("otra")])

    with pytest.raises(RuntimeError, match="no existe"):
        reader.write_role_value("actuator", 12.0)


def test_escribir_en_una_de_solo_lectura_propaga_el_error():
    nodes = [FakeNode("rActuator", writable=False)]
    reader = make_reader(mapping={"actuator": "rActuator"}, nodes=nodes)

    with pytest.raises(RuntimeError, match="BadUserAccessDenied"):
        reader.write_role_value("actuator", 12.0)


def test_cambiar_el_mapeo_invalida_el_cache():
    """Si no, se seguiría escribiendo en la variable anterior."""
    nodes = [FakeNode("rActuator"), FakeNode("OtraValvula")]
    reader = make_reader(mapping={"actuator": "rActuator"}, nodes=nodes)

    reader.write_role_value("actuator", 10.0)
    reader.set_mapping({"actuator": "OtraValvula"})
    reader.write_role_value("actuator", 11.0)

    assert reader.escrituras == [("rActuator", 10.0), ("OtraValvula", 11.0)]
