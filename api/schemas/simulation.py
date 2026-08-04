from __future__ import annotations

"""
Schemas del ensayo de lazo abierto y de la escala física de cada señal.

Nada de esto escribe en el PLC: son parámetros que la vista fija y el backend
usa para convertir unidades, dimensionar la ventana de identificación y elegir
el modelo.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

ScaleKey = Literal["ma", "pct", "v"]
OrderKey = Literal["auto", "fopdt", "sopdt", "integrating", "0", "1", "2"]


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


class SignalScales(BaseModel):
    """En qué escala física está la variable de cada rol."""

    actuator: Optional[ScaleKey] = Field(
        None,
        description="Escala del actuador (u). `ma` = 4-20 mA, `pct` = 0-100 %, `v` = 0-10 V",
    )
    sensor: Optional[ScaleKey] = Field(
        None, description="Escala del sensor / variable de proceso (y)"
    )
    setpoint: Optional[ScaleKey] = Field(None, description="Escala del setpoint")

    model_config = {
        "json_schema_extra": {
            "example": {"actuator": "ma", "sensor": "ma", "setpoint": "pct"}
        }
    }


class ScalesRequest(BaseModel):
    scales: SignalScales = Field(
        ..., description="Escala por rol. Los roles omitidos no cambian."
    )


class TestConfigRequest(BaseModel):
    """Condiciones del ensayo — caja negra (paso 2 de la vista)."""

    step_from: Optional[float] = Field(
        None, description="Valor inicial del actuador, en su escala física"
    )
    step_to: Optional[float] = Field(
        None, description="Valor final del actuador, en su escala física"
    )
    duration_s: Optional[float] = Field(
        None, gt=0, description="Duración total del ensayo (s)"
    )
    delay_s: Optional[float] = Field(
        None, ge=0, description="Segundos de línea base antes de aplicar el escalón"
    )
    order: Optional[OrderKey] = Field(
        None,
        description=(
            "Modelo a ajustar. `auto` compara los tres y se queda con el mejor R². "
            "Se aceptan también los valores del combo de la vista: `1`=FOPDT, "
            "`2`=SOPDT, `0`=integrante."
        ),
    )
    sample_period_s: Optional[float] = Field(
        None,
        gt=0,
        description="Tiempo de muestreo (s). Debe coincidir con el ciclo del ctrlX.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "step_from": 8.0,
                "step_to": 12.0,
                "duration_s": 120,
                "delay_s": 10,
                "order": "auto",
                "sample_period_s": 0.2,
            }
        }
    }


class ConvertRequest(BaseModel):
    """Conversión puntual de un valor entre escalas."""

    value: float = Field(..., description="Valor a convertir")
    from_scale: ScaleKey = Field(..., description="Escala de origen")
    to_scale: ScaleKey = Field("pct", description="Escala de destino")

    model_config = {
        "json_schema_extra": {
            "example": {"value": 12.0, "from_scale": "ma", "to_scale": "pct"}
        }
    }


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #


class ScaleDetail(BaseModel):
    key: str
    label: str
    unit: str
    min: float
    max: float
    span: float


class ScalesResponse(BaseModel):
    scales: dict[str, str] = Field(..., description="Escala vigente por rol")
    detail: dict[str, ScaleDetail] = Field(
        ..., description="Definición completa de cada escala en uso"
    )
    available: list[ScaleDetail] = Field(
        ..., description="Catálogo de escalas soportadas"
    )
    recomputed_samples: int = Field(
        0,
        description=(
            "Muestras del buffer que se reinterpretaron con la escala nueva. "
            "Solo viene en la respuesta del POST."
        ),
    )


class StepDerived(BaseModel):
    """Todo lo que el backend calcula a partir del escalón configurado."""

    step_from_pct: float = Field(..., description="Valor inicial expresado en % de span")
    step_to_pct: float = Field(..., description="Valor final expresado en % de span")
    delta: float = Field(..., description="Salto en unidades de ingeniería")
    delta_pct: float = Field(..., description="Salto en % de span")
    direction: str = Field(..., description="`up` o `down`")
    step_threshold_pct: float = Field(
        ..., description="Umbral con el que el detector considera que hubo escalón"
    )
    pre_samples: int = Field(
        ..., description="Muestras de línea base que entran en la ventana"
    )
    post_samples: int = Field(
        ..., description="Muestras de respuesta que entran en la ventana"
    )
    expected_samples: int = Field(
        ..., description="Muestras totales esperadas del ensayo"
    )
    step_at_s: float = Field(..., description="Instante del salto, en segundos")


class TestConfigResponse(BaseModel):
    step_from: float
    step_to: float
    duration_s: float
    delay_s: float
    order: str
    sample_period_s: float
    actuator_scale: ScaleDetail
    derived: StepDerived


class ConvertResponse(BaseModel):
    value: float
    from_scale: str
    to_scale: str
    result: float
    percent: float = Field(
        ..., description="El mismo valor expresado en % de span"
    )


class StepPreviewResponse(BaseModel):
    time: list[float]
    actuator: list[float]
    unit: str
    scale: ScaleDetail
    step_at_s: float
    duration_s: float
    from_value: float
    to_value: float


class TestSnapshotResponse(BaseModel):
    ok: bool
    scales: dict[str, str]
    detail: dict[str, ScaleDetail]
    available: list[ScaleDetail]
    step: TestConfigResponse
    orders: list[str]


class ErrorResponse(BaseModel):
    detail: str
