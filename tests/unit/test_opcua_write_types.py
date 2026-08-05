"""
Regresión del BadTypeMismatch al escribir el actuador.

El suite anterior stubbeaba `write_value` completo, así que nunca ejecutaba
el cuerpo real. Dos bugs vivieron ahí sin que nadie los viera:

  1. `ua` no estaba importado en opcua_client.py, así que toda referencia a
     `ua.` lanzaba NameError. El `except Exception` lo tragaba y la escritura
     caía a un `set_value(value)` sin tipar -> el ctrlX contestaba
     BadTypeMismatch.
  2. Aun con el import puesto, `ua.Variant(25.0, Int16)` falla al serializar:
     hay que convertir el VALOR, no solo etiquetar el Variant.

Estos tests ejercitan el camino real, sin stubs.
"""

import pytest

from opcua import ua

from infrastructure.ctrlx.opcua_client import CtrlxOpcUaClient, coerce_to_variant


class FakeNode:
    """Nodo mínimo que imita lo que usa write_value."""

    def __init__(self, variant_type):
        self._variant_type = variant_type
        self.written = None

    # El ctrlX expone la variable como objeto con hijo "2:Value".
    # Acá la variable es el nodo mismo.
    def get_child(self, path):
        raise Exception("sin hijo 2:Value")

    def get_data_type_as_variant_type(self):
        return self._variant_type

    def set_value(self, value):
        self.written = value


def test_modulo_importa_ua():
    """El NameError original: `ua` tiene que estar disponible en el módulo."""
    from infrastructure.ctrlx import opcua_client

    assert getattr(opcua_client, "ua", None) is not None


@pytest.mark.parametrize(
    "variant_type,esperado_python",
    [
        (ua.VariantType.SByte, int),
        (ua.VariantType.Byte, int),
        (ua.VariantType.Int16, int),
        (ua.VariantType.UInt16, int),
        (ua.VariantType.Int32, int),
        (ua.VariantType.UInt32, int),
        (ua.VariantType.Int64, int),
        (ua.VariantType.UInt64, int),
        (ua.VariantType.Float, float),
        (ua.VariantType.Double, float),
    ],
)
def test_write_value_manda_el_tipo_declarado(variant_type, esperado_python):
    """El ensayo comanda floats (25.0, 50.0) sea cual sea el tipo del nodo."""
    node = FakeNode(variant_type)
    CtrlxOpcUaClient("opc.tcp://x:4840").write_value(node, 25.0)

    enviado = node.written
    assert isinstance(enviado, ua.Variant), "debe mandarse un Variant tipado"
    assert enviado.VariantType is variant_type
    assert type(enviado.Value) is esperado_python
    assert enviado.Value == pytest.approx(25.0)


@pytest.mark.parametrize("variant_type", [ua.VariantType.Int16, ua.VariantType.Int32])
def test_variant_entero_es_serializable(variant_type):
    """
    La prueba que faltaba: que el Variant realmente se pueda serializar.
    Con un float adentro, struct.pack revienta con
    'required argument is not an integer'.
    """
    from opcua.ua.ua_binary import variant_to_binary

    node = FakeNode(variant_type)
    CtrlxOpcUaClient("opc.tcp://x:4840").write_value(node, 25.0)

    variant_to_binary(node.written)  # no debe lanzar


def test_valor_fraccionario_se_redondea_no_se_trunca():
    node = FakeNode(ua.VariantType.Int16)
    CtrlxOpcUaClient("opc.tcp://x:4840").write_value(node, 49.7)

    assert node.written.Value == 50


def test_booleano():
    node = FakeNode(ua.VariantType.Boolean)
    CtrlxOpcUaClient("opc.tcp://x:4840").write_value(node, 1.0)

    assert node.written.VariantType is ua.VariantType.Boolean
    assert node.written.Value is True


def test_sin_tipo_del_servidor_cae_a_valor_crudo():
    """Si el servidor no declara el tipo, se deja que la librería infiera."""

    class SinTipo(FakeNode):
        def get_data_type_as_variant_type(self):
            raise Exception("el servidor no contesta el DataType")

    node = SinTipo(None)
    CtrlxOpcUaClient("opc.tcp://x:4840").write_value(node, 25.0)

    assert node.written == 25.0


def test_error_de_escritura_se_propaga():
    """
    Antes, un fallo real de escritura se enmascaraba reintentando sin tipar.
    Eso convertía un error claro en un BadTypeMismatch repetido.
    """

    class NoEscribible(FakeNode):
        def set_value(self, value):
            raise ua.UaStatusCodeError(ua.StatusCodes.BadUserAccessDenied)

    node = NoEscribible(ua.VariantType.Int16)

    with pytest.raises(ua.UaStatusCodeError):
        CtrlxOpcUaClient("opc.tcp://x:4840").write_value(node, 25.0)


def test_coerce_no_toca_tipos_desconocidos():
    assert coerce_to_variant("texto", ua.VariantType.String) == "texto"
