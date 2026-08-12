from __future__ import annotations

"""Esquemas del ciclo de tarea del PLC."""

from typing import Optional

from pydantic import BaseModel, Field


class TaskCycleConfigRequest(BaseModel):
    variable: Optional[str] = Field(
        None,
        description=(
            "Variable de PLC_PRG que hace de puente con el intervalo del "
            "MainTask. Cadena vacía para desactivar."
        ),
    )
    sync_enabled: Optional[bool] = Field(
        None,
        description=(
            "Si al cambiar el tiempo de muestreo se ajusta el ciclo del PLC. "
            "Solo baja el ciclo: muestrear más despacio que el PLC es normal "
            "y no justifica frenarlo."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {"variable": "uiTaskCycleMs", "sync_enabled": True}
        }
    }


class TaskCycleSetRequest(BaseModel):
    cycle_ms: float = Field(..., description="Ciclo de tarea pedido, en milisegundos")

    model_config = {"json_schema_extra": {"example": {"cycle_ms": 20.0}}}


class TaskCycleResponse(BaseModel):
    variable: Optional[str] = Field(None, description="Variable puente configurada")
    sync_enabled: bool = Field(
        ..., description="El tiempo de muestreo arrastra al ciclo del PLC"
    )
    cycle_ms: Optional[float] = Field(
        None, description="Ciclo que reporta el PLC. `null` si no se pudo leer."
    )
    writable: bool = Field(..., description="La variable puente acepta escritura")
    reason: Optional[str] = Field(None, description="Por qué no se puede escribir")
    oversampling: bool = Field(
        ...,
        description=(
            "El tiempo de muestreo pedido está por debajo del ciclo de tarea. "
            "Llegarían muestras repetidas: la variable todavía no cambió "
            "cuando se vuelve a leer."
        ),
    )
    min_cycle_ms: float = Field(..., description="Mínimo que acepta la app")
    max_cycle_ms: float = Field(..., description="Máximo que acepta la app")

    model_config = {
        "json_schema_extra": {
            "example": {
                "variable": "uiTaskCycleMs",
                "sync_enabled": True,
                "cycle_ms": 20.0,
                "writable": True,
                "reason": None,
                "oversampling": False,
                "min_cycle_ms": 5.0,
                "max_cycle_ms": 200.0,
            }
        }
    }
