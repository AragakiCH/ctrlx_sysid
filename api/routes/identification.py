from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.schemas.identification import IdentificationResult
from api.schemas.opcua import ErrorResponse

router = APIRouter(prefix="/api/identification", tags=["Identificación"])


@router.get(
    "/latest",
    response_model=IdentificationResult,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Todavía no se ha identificado ningún modelo",
        }
    },
    summary="Último resultado de identificación",
    description=(
        "Devuelve el resultado más reciente.\n\n"
        "**Es el mismo payload** que el WebSocket empuja en el mensaje\n"
        "`{\"type\": \"identification_result\"}`. Existe para poder inspeccionarlo\n"
        "desde Swagger o Postman sin tener que abrir una conexión WebSocket.\n\n"
        "La identificación es automática: el backend la dispara solo cuando\n"
        "detecta un escalón en el actuador. Si nunca hubo uno, responde 404."
    ),
)
def latest_identification(request: Request) -> dict:
    result = getattr(request.app.state, "last_identification_result", None)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Aún no hay identificación. Se genera automáticamente cuando el "
                "actuador da un escalón y hay suficientes muestras posteriores."
            ),
        )

    return result
