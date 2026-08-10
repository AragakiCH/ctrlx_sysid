"""
Coste de cada muestra en peticiones OPC UA.

Muestrear a 20 ms solo es posible si cada muestra cuesta UNA ida y vuelta. La
versión anterior resolvía el árbol en cada ciclo —browse de los hijos, browse
del nombre de cada uno, browse de su `2:Value` y read— así que con seis
variables se iban unas 19 peticiones por muestra: casi mil por segundo a 20 ms,
imposible sobre TCP. El síntoma en la vista era una señal escalonada y el aviso
de "el muestreo real es 10× el configurado".
"""

import pytest

from infrastructure.ctrlx.node_repository import NodeRepository

VARIABLES = [
    ("rTimeSec", 1.0),
    ("rActuator", 25.0),
    ("rSensor", 20.4),
    ("rSetPoint", 25.0),
    ("uiSignalType", 1),
    ("arrTimeSec", 0.0),
]


class Contador:
    def __init__(self):
        self.n = 0
        self.detalle = []

    def registrar(self, que):
        self.n += 1
        self.detalle.append(que)


@pytest.fixture
def contador():
    return Contador()


@pytest.fixture
def programa(contador):
    class NodoValor:
        def __init__(self, valor):
            self.valor = valor

        def get_value(self):
            contador.registrar("read")
            return self.valor

    class Nodo:
        def __init__(self, nombre, valor):
            self._nombre = nombre
            self._valor = NodoValor(valor)
            self.nodeid = type("N", (), {"to_string": lambda s: f"ns=2;s={nombre}"})()

        def get_browse_name(self):
            contador.registrar("browse_name")
            return type("B", (), {"Name": self._nombre})()

        def get_child(self, path):
            contador.registrar("browse_value")
            return self._valor

    class Programa:
        def __init__(self):
            self._hijos = [Nodo(n, v) for n, v in VARIABLES]
            self.nodeid = type("N", (), {"to_string": lambda s: "ns=2;s=PLC_PRG"})()

        def get_children(self):
            contador.registrar("browse_children")
            return self._hijos

    return Programa()


@pytest.fixture
def repo(contador):
    class Opc:
        def value_node(self, node):
            return node.get_child(["2:Value"])

        def read_values(self, nodes):
            contador.registrar("get_values")
            return [n.valor for n in nodes]

    return NodeRepository(Opc())


# --------------------------------------------------------------------------- #


def test_la_lectura_devuelve_todas_las_variables(repo, programa):
    valores = repo.read_program_values(programa)

    assert valores == {n: v for n, v in VARIABLES}


def test_las_muestras_siguientes_cuestan_una_sola_peticion(repo, programa, contador):
    """Lo que hace viable el muestreo rápido."""
    repo.read_program_values(programa)  # primera: resuelve la estructura

    for _ in range(10):
        contador.n = 0
        contador.detalle = []
        repo.read_program_values(programa)

        assert contador.n == 1
        assert contador.detalle == ["get_values"]


def test_la_estructura_se_resuelve_una_sola_vez(repo, programa, contador):
    for _ in range(20):
        repo.read_program_values(programa)

    assert contador.detalle.count("browse_children") == 1
    assert contador.detalle.count("browse_name") == len(VARIABLES)
    assert contador.detalle.count("browse_value") == len(VARIABLES)


def test_invalidar_el_layout_fuerza_a_resolver_de_nuevo(repo, programa, contador):
    """Tras reconectar, los nodos viejos apuntan a una sesión muerta."""
    repo.read_program_values(programa)
    repo.invalidate_layout()

    contador.detalle = []
    repo.read_program_values(programa)

    assert "browse_children" in contador.detalle


def test_un_fallo_de_conexion_marca_todas_las_variables(programa):
    """
    Un dict a medias parecería válido y el modelo saldría de datos parciales.
    """

    class OpcRoto:
        def value_node(self, node):
            return node.get_child(["2:Value"])

        def read_values(self, nodes):
            raise ConnectionError("sesión cerrada")

    valores = NodeRepository(OpcRoto()).read_program_values(programa)

    assert len(valores) == len(VARIABLES)
    assert all(str(v).startswith("READ_ERROR") for v in valores.values())


def test_programa_sin_variables_no_pide_nada(contador):
    class Vacio:
        nodeid = type("N", (), {"to_string": lambda s: "ns=2;s=vacio"})()

        def get_children(self):
            contador.registrar("browse_children")
            return []

    class Opc:
        def value_node(self, node):
            return node

        def read_values(self, nodes):
            contador.registrar("get_values")
            return []

    assert NodeRepository(Opc()).read_program_values(Vacio()) == {}
    assert "get_values" not in contador.detalle


def test_se_cae_a_lectura_individual_si_el_servidor_no_soporta_lotes():
    """No todos los servidores OPC UA implementan la lectura por lotes."""
    from infrastructure.ctrlx.opcua_client import CtrlxOpcUaClient

    class NodoSuelto:
        def __init__(self, v):
            self.v = v

        def get_value(self):
            return self.v

    cliente = CtrlxOpcUaClient(url="opc.tcp://x:4840")

    # Sin conexión, `self.client` lanza -> debe caer a la lectura individual.
    assert cliente.read_values([NodoSuelto(1.0), NodoSuelto(2.0)]) == [1.0, 2.0]


def test_sin_nodos_devuelve_lista_vacia():
    from infrastructure.ctrlx.opcua_client import CtrlxOpcUaClient

    assert CtrlxOpcUaClient(url="opc.tcp://x:4840").read_values([]) == []
