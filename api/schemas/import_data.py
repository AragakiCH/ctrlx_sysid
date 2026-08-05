"""Esquemas de la importación de ensayos desde archivo."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ImportedVariableSummary(BaseModel):
    name: str = Field(..., description="Nombre tal como aparece en el archivo")
    samples: int
    min: Optional[float]
    max: Optional[float]
    first: Optional[float]
    last: Optional[float]
    preview: list[float] = Field(..., description="Primeros valores, para el modal")
    constant: bool = Field(
        ..., description="True si la señal no se mueve: no sirve como actuador ni sensor"
    )


class ParseResponse(BaseModel):
    """Lo que devuelve /parse: qué trae el archivo, para armar el modal de mapeo."""

    ok: bool
    token: str = Field(
        ...,
        description=(
            "Identificador del archivo parseado. Se envía luego en /load para "
            "no volver a subir el archivo."
        ),
    )
    source_name: str
    format: str = Field(..., description="codesys-trace | csv | xlsx")
    samples: int
    duration_s: float
    sample_period_s: float
    variables: list[ImportedVariableSummary]
    suggested_mapping: dict[str, Optional[str]] = Field(
        ...,
        description=(
            "Sugerencia por heurística sobre los NOMBRES del archivo (busca "
            "'out'/'pid' para actuador, 'sp'/'setpoint' para consigna...). "
            "Solo pre-selecciona el modal; el usuario decide."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "ok": True,
                "token": "a3f8c2",
                "source_name": "data_sinto_05_08.trace.csv",
                "format": "codesys-trace",
                "samples": 1517,
                "duration_s": 30.32,
                "sample_period_s": 0.02,
                "variables": [
                    {
                        "name": "Main_Control.PID_OUT",
                        "samples": 1517,
                        "min": 25.0,
                        "max": 50.0,
                        "first": 25.0,
                        "last": 50.0,
                        "preview": [25.0, 25.0, 25.0],
                        "constant": False,
                    }
                ],
                "suggested_mapping": {
                    "actuator": "Main_Control.PID_OUT",
                    "sensor": "Main_Control.Velocidad_Scaled",
                    "setpoint": "DB_HMI.HMI_SP_Local_Automatico",
                },
            }
        }
    }


class ImportMapping(BaseModel):
    """Qué variable del ARCHIVO cumple cada rol."""

    actuator: str = Field(..., description="Obligatoria: la entrada del proceso")
    sensor: str = Field(..., description="Obligatoria: la respuesta del proceso")
    setpoint: Optional[str] = Field(None, description="Opcional")


class LoadRequest(BaseModel):
    token: str = Field(..., description="El devuelto por /parse")
    mapping: ImportMapping
    scale: str = Field(
        "pct",
        description=(
            "Escala de las señales del archivo (ma | pct | v). Un trace de "
            "CODESYS suele venir en unidades de ingeniería 0-100: pct."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "token": "a3f8c2",
                "mapping": {
                    "actuator": "Main_Control.PID_OUT",
                    "sensor": "Main_Control.Velocidad_Scaled",
                    "setpoint": "DB_HMI.HMI_SP_Local_Automatico",
                },
                "scale": "pct",
            }
        }
    }


class ImportStatus(BaseModel):
    source: str = Field(..., description="realtime | imported")
    active: bool = Field(..., description="True si el buffer contiene datos de archivo")
    source_name: Optional[str] = None
    format: Optional[str] = None
    samples: Optional[int] = None
    sample_period_s: Optional[float] = None
    mapping: Optional[dict] = None
    loaded_at: Optional[float] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "source": "imported",
                "active": True,
                "source_name": "data_sinto_05_08.trace.csv",
                "format": "codesys-trace",
                "samples": 1517,
                "sample_period_s": 0.02,
                "mapping": {
                    "actuator": "Main_Control.PID_OUT",
                    "sensor": "Main_Control.Velocidad_Scaled",
                    "setpoint": "DB_HMI.HMI_SP_Local_Automatico",
                },
                "loaded_at": 1754413200.5,
            }
        }
    }


class LoadResponse(ImportStatus):
    ok: bool = True
    buffer_size: int = 0
