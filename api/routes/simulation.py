from __future__ import annotations

"""
Endpoints del ensayo de lazo abierto y de la escala de cada señal.

Todo se resuelve en memoria del backend: **no se escribe ninguna variable del
PLC**. El operador aplica el escalón desde el ctrlX y aquí solo se guardan las
condiciones para poder convertir unidades, dimensionar la ventana de
identificación y elegir el modelo.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from api.schemas.simulation import (
    ConvertRequest,
    ConvertResponse,
    ErrorResponse,
    ScalesRequest,
    ScalesResponse,
    StepPreviewResponse,
    TestConfigRequest,
    TestConfigResponse,
    TestSnapshotResponse,
)
from domain.services.scale_converter import convert as convert_value
from domain.services.scale_converter import to_percent

router = APIRouter(prefix="/api/test", tags=["Ensayo"])

VALIDATION_ERRORS = {
    400: {"model": ErrorResponse, "description": "Parámetros inválidos"},
}


def _service(request: Request):
    return request.app.state.test_config_service


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #


@router.get(
    "",
    response_model=TestSnapshotResponse,
    summary="Configuración completa del ensayo",
    description=(
        "Devuelve de una sola vez la escala de cada rol, el catálogo de escalas "
        "soportadas, las condiciones del escalón y todos los derivados.\n\n"
        "Es lo que conviene pedir al cargar la vista para pintar los combos y los "
        "campos del paso 1 y 2 sin hacer varias llamadas."
    ),
)
def get_snapshot(request: Request) -> dict:
    return _service(request).describe()


# --------------------------------------------------------------------------- #
# Escalas
# --------------------------------------------------------------------------- #


@router.get(
    "/scales",
    response_model=ScalesResponse,
    summary="Escala vigente por rol",
    description=(
        "`ma` = 4-20 mA, `pct` = 0-100 %, `v` = 0-10 V.\n\n"
        "Internamente el backend trabaja en **% de span**: es la única escala en la "
        "que la ganancia del modelo es adimensional y las sintonías PID son "
        "comparables entre lazos. La escala declarada aquí es la que se usa para "
        "traducir cada lectura cruda del PLC a ese %."
    ),
)
def get_scales(request: Request) -> dict:
    return _service(request).describe_scales()


@router.post(
    "/scales",
    response_model=ScalesResponse,
    responses=VALIDATION_ERRORS,
    summary="Fijar la escala de cada rol",
    description=(
        "Cambia en qué escala física se interpreta la variable de cada rol. Los "
        "roles omitidos conservan su valor.\n\n"
        "Los campos `actuator_pct`, `sensor_pct` y `setpoint_pct` se recalculan "
        "**también para las muestras que ya están en el buffer**, de modo que un "
        "ensayo ya capturado queda reinterpretado con la escala nueva y no hace "
        "falta repetirlo.\n\n"
        "No se escribe nada en el PLC."
    ),
)
def set_scales(body: ScalesRequest, request: Request) -> dict:
    try:
        payload = _service(request).set_scales(body.scales.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload["recomputed_samples"] = request.app.state.realtime_service.recompute_percent()

    return payload


# --------------------------------------------------------------------------- #
# Configuración del escalón
# --------------------------------------------------------------------------- #


@router.get(
    "/config",
    response_model=TestConfigResponse,
    summary="Condiciones del ensayo",
    description="Valor inicial y final, retardo, duración, orden y todos los derivados.",
)
def get_config(request: Request) -> dict:
    return _service(request).describe_step_config()


@router.post(
    "/config",
    response_model=TestConfigResponse,
    responses=VALIDATION_ERRORS,
    summary="Guardar las condiciones del ensayo",
    description=(
        "Guarda el escalón que el operador va a aplicar. Los campos omitidos "
        "conservan su valor anterior.\n\n"
        "El backend usa estos números para tres cosas:\n\n"
        "1. **Umbral de detección**: se considera escalón un salto de al menos la "
        "mitad del `delta_pct` configurado, con un piso de 1 %. Así no se dispara "
        "con ruido ni se pierde el evento si el actuador no llega exacto al valor "
        "final.\n"
        "2. **Ventana de identificación**: `delay_s` define cuántas muestras de "
        "línea base se conservan antes del salto y `duration_s - delay_s` cuántas "
        "de respuesta se capturan después.\n"
        "3. **Modelo**: si `order` no es `auto`, se ajusta solo ese modelo.\n\n"
        "Devuelve 400 si el escalón no es identificable (valores fuera de escala, "
        "salto nulo, o muy pocas muestras después del escalón)."
    ),
)
def set_config(body: TestConfigRequest, request: Request) -> dict:
    try:
        return _service(request).set_step_config(
            step_from=body.step_from,
            step_to=body.step_to,
            duration_s=body.duration_s,
            delay_s=body.delay_s,
            order=body.order,
            sample_period_s=body.sample_period_s,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Utilidades de conversión
# --------------------------------------------------------------------------- #


@router.post(
    "/convert",
    response_model=ConvertResponse,
    responses=VALIDATION_ERRORS,
    summary="Convertir un valor entre escalas",
    description=(
        "Conversión lineal por span, pasando siempre por porcentaje:\n\n"
        "```\n"
        "%     = (valor - min) / (max - min) * 100\n"
        "valor = min + % / 100 * (max - min)\n"
        "```\n\n"
        "Sirve para los textos de ayuda del paso 2 (`8.0 mA = 25 %`) sin duplicar "
        "la fórmula en el JavaScript."
    ),
)
def convert(body: ConvertRequest) -> dict:
    result = convert_value(body.value, body.from_scale, body.to_scale)
    percent = to_percent(body.value, body.from_scale)

    if result is None or percent is None:
        raise HTTPException(status_code=400, detail="Valor no numérico.")

    return {
        "value": body.value,
        "from_scale": body.from_scale,
        "to_scale": body.to_scale,
        "result": result,
        "percent": percent,
    }


@router.get(
    "/preview",
    response_model=StepPreviewResponse,
    summary="Vista previa del escalón",
    description=(
        "Serie ideal del escalón configurado, lista para graficar en el paso 2.\n\n"
        "Con `scale` se puede pedir en otra escala (por ejemplo en `pct` aunque el "
        "actuador esté en `ma`) sin cambiar la configuración guardada."
    ),
)
def preview(
    request: Request,
    points: int = Query(200, ge=2, le=2000, description="Cantidad de puntos a generar"),
    scale: Optional[str] = Query(
        None, description="Escala de salida. Por defecto, la del actuador."
    ),
) -> dict:
    try:
        return _service(request).build_preview(points=points, scale_key=scale)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
