"""Esquemas de request/response de los endpoints OPC UA.

Todo lo que aparece aquí se refleja automáticamente en Swagger (/docs).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Mapeo de señales
# --------------------------------------------------------------------------- #


class SignalMapping(BaseModel):
    """
    Qué variable del PLC cumple cada rol.

    Todos los campos son opcionales: el rol que se deje en `null` se resuelve
    en el backend con los alias por defecto (rActuator, rSensor, rTimeSec...).
    """

    time: Optional[str] = Field(None, description="Variable con el tiempo del proceso, en segundos")
    actuator: Optional[str] = Field(None, description="Variable de la salida del controlador (MV)")
    sensor: Optional[str] = Field(None, description="Variable de la medición del proceso (PV)")
    setpoint: Optional[str] = Field(None, description="Variable del setpoint (opcional)")
    signal_type: Optional[str] = Field(
        None,
        description="Variable que indica la escala. Si vale 1 el backend convierte de 4-20 mA a %.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "time": "rTimeSec",
                "actuator": "rActuator",
                "sensor": "rSensor",
                "setpoint": "rSetPoint",
                "signal_type": "uiSignalType",
            }
        }
    }


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


class OpcUaCredentials(BaseModel):
    user: str = Field(..., description="Usuario OPC UA del ctrlX")
    password: str = Field(..., description="Contraseña OPC UA")
    url: str = Field(..., description="Endpoint OPC UA, formato opc.tcp://host:puerto")

    model_config = {
        "json_schema_extra": {
            "example": {
                "user": "boschrexroth",
                "password": "********",
                "url": "opc.tcp://192.168.1.1:4840",
            }
        }
    }


class OpcUaDiscoverProgramsRequest(OpcUaCredentials):
    pass


class OpcUaDiscoverVariablesRequest(OpcUaCredentials):
    program_name: str = Field(..., description="Programa PLC devuelto por /discover-programs")

    model_config = {
        "json_schema_extra": {
            "example": {
                "user": "boschrexroth",
                "password": "********",
                "url": "opc.tcp://192.168.1.1:4840",
                "program_name": "PLC_PRG",
            }
        }
    }


class OpcUaLoginRequest(OpcUaCredentials):
    program_name: str = Field(..., description="Programa PLC a monitorear")
    mapping: Optional[SignalMapping] = Field(
        None,
        description="Mapeo elegido en la vista. Si se omite se usan los alias por defecto.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "user": "boschrexroth",
                "password": "********",
                "url": "opc.tcp://192.168.1.1:4840",
                "program_name": "PLC_PRG",
                "mapping": {
                    "time": "Tiempo_Proceso",
                    "actuator": "Valvula_Salida",
                    "sensor": "Temp_Horno",
                    "setpoint": "Consigna",
                    "signal_type": None,
                },
            }
        }
    }


class OpcUaMappingRequest(BaseModel):
    mapping: SignalMapping = Field(..., description="Nuevo mapeo rol -> variable")


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #


class OpcUaDiscoverItem(BaseModel):
    """Un candidato de servidor OPC UA encontrado en la red."""

    url: str
    host: str
    port: int
    tcp_ok: bool = Field(..., description="True si el puerto TCP respondió al sondeo")
    source: str = Field(..., description="De dónde salió el candidato")

    model_config = {
        "json_schema_extra": {
            "example": {
                "url": "opc.tcp://192.168.1.1:4840",
                "host": "192.168.1.1",
                "port": 4840,
                "tcp_ok": True,
                "source": "candidate",
            }
        }
    }


class DiscoverProgramsResponse(BaseModel):
    ok: bool
    url: str
    user: str
    programs: list[str] = Field(..., description="Programas colgados del nodo 'sym'")

    model_config = {
        "json_schema_extra": {
            "example": {
                "ok": True,
                "url": "opc.tcp://192.168.1.1:4840",
                "user": "boschrexroth",
                "programs": ["PLC_PRG", "PRG_Horno"],
            }
        }
    }


class PlcVariable(BaseModel):
    """Una variable del programa PLC."""

    name: str = Field(..., description="Browse name en el PLC")
    node_id: str = Field(..., description="NodeId OPC UA")
    data_type: str = Field(..., description="Tipo OPC UA: Float, Double, Int16... o UNKNOWN")
    value: Optional[Any] = Field(None, description="Valor en el momento de la lectura")
    numeric: bool = Field(..., description="True si sirve como señal continua para identificar")
    readable: bool = Field(..., description="False si la lectura falló")
    error: Optional[str] = Field(None, description="Motivo del fallo de lectura, si lo hubo")

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "rSensor",
                "node_id": "ns=2;s=plc/app/Application/sym/PLC_PRG/rSensor",
                "data_type": "Float",
                "value": 7.21,
                "numeric": True,
                "readable": True,
                "error": None,
            }
        }
    }


class DiscoverVariablesResponse(BaseModel):
    """
    Todas las variables del programa, sin filtrar por nombre.

    `suggested_mapping` es solo una sugerencia para pre-seleccionar los
    desplegables: la vista decide el mapeo final.
    """

    ok: bool
    url: str
    user: str
    program_name: str
    roles: list[str] = Field(..., description="Roles que espera el backend")
    variables: list[PlcVariable]
    suggested_mapping: dict[str, Optional[str]]

    model_config = {
        "json_schema_extra": {
            "example": {
                "ok": True,
                "url": "opc.tcp://192.168.1.1:4840",
                "user": "boschrexroth",
                "program_name": "PLC_PRG",
                "roles": ["time", "actuator", "sensor", "setpoint", "signal_type"],
                "variables": [
                    {
                        "name": "rActuator",
                        "node_id": "ns=2;s=.../rActuator",
                        "data_type": "Float",
                        "value": 12.0,
                        "numeric": True,
                        "readable": True,
                        "error": None,
                    },
                    {
                        "name": "rSensor",
                        "node_id": "ns=2;s=.../rSensor",
                        "data_type": "Float",
                        "value": 7.21,
                        "numeric": True,
                        "readable": True,
                        "error": None,
                    },
                ],
                "suggested_mapping": {
                    "time": "rTimeSec",
                    "actuator": "rActuator",
                    "sensor": "rSensor",
                    "setpoint": "rSetPoint",
                    "signal_type": None,
                },
            }
        }
    }


class LoginResponse(BaseModel):
    ok: bool
    url: str
    user: str
    program_name: str
    mapping: dict[str, Optional[str]] = Field(
        ..., description="Mapeo efectivo tras completar con alias los roles vacíos"
    )
    started: bool = Field(..., description="True si el hilo de lectura cíclica arrancó")

    model_config = {
        "json_schema_extra": {
            "example": {
                "ok": True,
                "url": "opc.tcp://192.168.1.1:4840",
                "user": "boschrexroth",
                "program_name": "PLC_PRG",
                "mapping": {
                    "time": "rTimeSec",
                    "actuator": "rActuator",
                    "sensor": "rSensor",
                    "setpoint": "rSetPoint",
                    "signal_type": "uiSignalType",
                },
                "started": True,
            }
        }
    }


class MappingResponse(BaseModel):
    ok: bool
    mapping: dict[str, Optional[str]]


class LogoutResponse(BaseModel):
    ok: bool
    logged_out: bool


class StatusResponse(BaseModel):
    authenticated: bool = Field(..., description="Hay credenciales cargadas")
    connected: bool = Field(..., description="El hilo de lectura está vivo")
    url: Optional[str]
    user: Optional[str]
    buffer_size: int = Field(..., description="Muestras en el buffer circular")
    has_latest: bool
    has_identification: bool
    last_error: Optional[str]
    last_login_ts: Optional[float] = Field(None, description="Epoch UNIX del último login")
    program_name: Optional[str]
    mapping: Optional[dict[str, Optional[str]]]

    model_config = {
        "json_schema_extra": {
            "example": {
                "authenticated": True,
                "connected": True,
                "url": "opc.tcp://192.168.1.1:4840",
                "user": "boschrexroth",
                "buffer_size": 1250,
                "has_latest": True,
                "has_identification": True,
                "last_error": None,
                "last_login_ts": 1754212800.42,
                "program_name": "PLC_PRG",
                "mapping": {
                    "time": "rTimeSec",
                    "actuator": "rActuator",
                    "sensor": "rSensor",
                    "setpoint": "rSetPoint",
                    "signal_type": "uiSignalType",
                },
            }
        }
    }


class HealthResponse(BaseModel):
    status: str
    buffer_size: int
    has_latest: bool
    has_identification: bool
    opcua_authenticated: bool
    opcua_url: Optional[str]
    opcua_user: Optional[str]
    use_percent: bool = Field(
        ..., description="True si las señales se están normalizando a 0-100 %"
    )


class ErrorResponse(BaseModel):
    """Formato de error de FastAPI."""

    detail: str

    model_config = {
        "json_schema_extra": {"example": {"detail": "Falta la URL OPC UA."}}
    }
