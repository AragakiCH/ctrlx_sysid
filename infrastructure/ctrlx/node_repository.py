from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional

try:  # opcua solo hace falta para hablar con el PLC de verdad
    from opcua import ua
except ImportError:  # pragma: no cover
    ua = None


# Sugerencias por defecto. NO se usan para filtrar: solo para pre-seleccionar
# una opción razonable en la vista cuando el usuario abre el selector.
SIGNAL_ALIASES: dict[str, list[str]] = {
    "time": [
        "rTimeSec",
        "rTiempoSeg",
        "arrTimeSec",
        "time",
        "tiempo",
    ],
    "actuator": [
        "rActuator",
        "AO_Actuador_mA",
        "AO_Actuador",
        "actuator",
        "actuador",
    ],
    "sensor": [
        "rSensor",
        "AI_Sensor_mA",
        "AI_Sensor",
        "sensor",
        "sensor_ai",
    ],
    "setpoint": [
        "rSetPoint",
        "SP_mA",
        "SP",
        "setpoint",
        "set_point",
    ],
    "signal_type": [
        "uiSignalType",
        "signal_type",
        "uiTipoSenal",
        "tipo_senal",
    ],
}

SIGNAL_ROLES = tuple(SIGNAL_ALIASES.keys())

# Tipos que tienen sentido como señal continua para identificación.
NUMERIC_VARIANTS = {
    "Float",
    "Double",
    "SByte",
    "Byte",
    "Int16",
    "UInt16",
    "Int32",
    "UInt32",
    "Int64",
    "UInt64",
}


@dataclass
class PlcVariable:
    name: str
    node_id: str
    data_type: str
    value: Any
    numeric: bool
    readable: bool
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_name(name: str) -> str:
    """Normaliza un browse name para comparaciones tolerantes."""
    return (
        (name or "")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )


def _variant_type_name(node) -> str:
    if ua is None:
        return "UNKNOWN"

    try:
        return ua.VariantType(node.get_data_type_as_variant_type()).name
    except Exception:
        return "UNKNOWN"


def _is_numeric_value(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class NodeRepository:
    """
    Explora el nodo de un programa PLC y devuelve TODAS sus variables,
    sin asumir nombres. La elección de qué variable es actuador/sensor/etc.
    es responsabilidad de la vista.
    """

    def __init__(self, opc_client) -> None:
        self._opc = opc_client

    def resolve_program_node(self, program_name: str):
        root = self._opc.get_root_node()

        return self._opc.browse_by_names(
            root,
            "Objects",
            "Datalayer",
            "plc",
            "app",
            "Application",
            "sym",
            program_name,
        )

    def list_programs(self) -> list[str]:
        root = self._opc.get_root_node()

        sym_node = self._opc.browse_by_names(
            root,
            "Objects",
            "Datalayer",
            "plc",
            "app",
            "Application",
            "sym",
        )

        if sym_node is None:
            raise RuntimeError("Conectó al OPC UA, pero no se encontró el nodo 'sym'.")

        programs: list[str] = []
        for child in sym_node.get_children():
            try:
                browse_name = child.get_browse_name().Name
                if browse_name:
                    programs.append(browse_name)
            except Exception:
                continue

        return programs

    def list_variables(self, program_node, include_values: bool = True) -> list[PlcVariable]:
        """Devuelve TODAS las variables hijas del programa, sin filtrar por nombre."""
        variables: list[PlcVariable] = []

        for child in program_node.get_children():
            try:
                name = child.get_browse_name().Name
            except Exception:
                continue

            if not name:
                continue

            try:
                node_id = child.nodeid.to_string()
            except Exception:
                node_id = ""

            data_type = _variant_type_name(child)

            value: Any = None
            error: Optional[str] = None
            readable = True

            if include_values:
                try:
                    value = self._opc.read_value(child)
                except Exception as exc:
                    readable = False
                    error = str(exc)

            numeric = data_type in NUMERIC_VARIANTS or _is_numeric_value(value)

            if not (_is_numeric_value(value) or isinstance(value, (str, bool))):
                value = None

            variables.append(
                PlcVariable(
                    name=name,
                    node_id=node_id,
                    data_type=data_type,
                    value=value,
                    numeric=numeric,
                    readable=readable,
                    error=error,
                )
            )

        variables.sort(key=lambda v: v.name.lower())
        return variables

    def read_program_values(self, program_node) -> dict[str, Any]:
        """Lectura cruda nombre -> valor de todos los hijos del programa."""
        values: dict[str, Any] = {}

        for child in program_node.get_children():
            try:
                name = child.get_browse_name().Name
            except Exception:
                continue

            if not name:
                continue

            try:
                values[name] = self._opc.read_value(child)
            except Exception as exc:
                values[name] = f"READ_ERROR: {exc}"

        return values


def suggest_mapping(variable_names: list[str]) -> dict[str, Optional[str]]:
    """
    Propone un mapeo inicial rol -> nombre de variable usando los alias
    conocidos. Es solo una sugerencia para pre-seleccionar en la UI;
    si no encuentra nada devuelve None para ese rol.
    """
    normalized = {normalize_name(n): n for n in variable_names}
    suggestion: dict[str, Optional[str]] = {}

    for role, aliases in SIGNAL_ALIASES.items():
        chosen: Optional[str] = None

        for alias in aliases:
            key = normalize_name(alias)
            if key in normalized:
                chosen = normalized[key]
                break

        if chosen is None:
            # segundo intento: coincidencia parcial
            for alias in aliases:
                key = normalize_name(alias)
                if not key:
                    continue
                for norm, original in normalized.items():
                    if key in norm:
                        chosen = original
                        break
                if chosen:
                    break

        suggestion[role] = chosen

    return suggestion


def resolve_mapping(mapping: Optional[dict], variable_names: list[str]) -> dict[str, Optional[str]]:
    """
    Valida un mapeo enviado por la vista contra las variables reales.
    Los roles no especificados caen a la sugerencia por alias.
    """
    fallback = suggest_mapping(variable_names)
    available = {normalize_name(n): n for n in variable_names}

    resolved: dict[str, Optional[str]] = {}

    for role in SIGNAL_ROLES:
        requested = (mapping or {}).get(role)
        requested = requested.strip() if isinstance(requested, str) else None

        if requested:
            key = normalize_name(requested)
            resolved[role] = available.get(key, requested)
        else:
            resolved[role] = fallback.get(role)

    return resolved
