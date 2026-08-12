from __future__ import annotations

"""
Ciclo de tarea del PLC.

El `Intervalo` del MainTask no es una variable del programa: vive en la
configuración de tareas del proyecto de ctrlX PLC Engineering, no se exporta en
la Configuración de símbolos y por tanto no existe ningún nodo OPC UA que
escribir.

Estos endpoints trabajan sobre una **variable puente** que el usuario declara
en PLC_PRG y que código IEC dentro del PLC usa para ajustar el intervalo. Ver
`docs/CICLO_DE_TAREA.md`.
"""

from fastapi import APIRouter, HTTPException, Request

from api.schemas.task_cycle import (
    TaskCycleConfigRequest,
    TaskCycleResponse,
    TaskCycleSetRequest,
)
from api.schemas.simulation import ErrorResponse

router = APIRouter(prefix="/api/plc/task-cycle", tags=["Ciclo de tarea"])

VALIDATION_ERRORS = {
    400: {"model": ErrorResponse, "description": "Parámetros inválidos"},
}


def _service(request: Request):
    return request.app.state.task_cycle_service


@router.get(
    "",
    response_model=TaskCycleResponse,
    summary="Estado del ciclo de tarea",
    description=(
        "Ciclo que reporta el PLC, si la variable puente es escribible y si el "
        "tiempo de muestreo pedido está por debajo del ciclo (sobremuestreo)."
    ),
)
async def get_task_cycle(request: Request):
    return _service(request).status()


@router.post(
    "/config",
    response_model=TaskCycleResponse,
    responses=VALIDATION_ERRORS,
    summary="Configurar la variable puente y la sincronización",
)
async def configure_task_cycle(payload: TaskCycleConfigRequest, request: Request):
    return _service(request).configure(
        variable=payload.variable,
        sync_enabled=payload.sync_enabled,
    )


@router.post(
    "",
    response_model=TaskCycleResponse,
    responses=VALIDATION_ERRORS,
    summary="Escribir el ciclo de tarea",
    description=(
        "Escribe el ciclo pedido en la variable puente. Se rechaza fuera de "
        "`min_cycle_ms`..`max_cycle_ms`: un ciclo demasiado corto deja al PLC "
        "sin margen para terminar y el watchdog lo manda a STOP."
    ),
)
async def set_task_cycle(payload: TaskCycleSetRequest, request: Request):
    try:
        return _service(request).set_cycle_ms(payload.cycle_ms)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
