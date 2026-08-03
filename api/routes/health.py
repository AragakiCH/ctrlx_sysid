from __future__ import annotations

from fastapi import APIRouter, Request

from api.schemas.opcua import HealthResponse

router = APIRouter(tags=["Sistema"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Estado del servicio",
    description=(
        "Chequeo rápido de vida. Responde 200 aunque no haya sesión OPC UA:\n"
        "el servicio está arriba, simplemente sin datos.\n\n"
        "`use_percent` indica si las señales se están convirtiendo de 4-20 mA a\n"
        "0-100 %. Depende de la variable mapeada como `signal_type`: si vale 1,\n"
        "se convierte."
    ),
)
def health(request: Request) -> dict:
    realtime_service = request.app.state.realtime_service
    session_service = request.app.state.opcua_session_service

    latest = realtime_service.get_latest_sample()

    return {
        "status": "ok",
        "buffer_size": realtime_service.get_buffer_size(),
        "has_latest": latest is not None,
        "has_identification": getattr(
            request.app.state, "last_identification_result", None
        )
        is not None,
        "opcua_authenticated": session_service.is_authenticated,
        "opcua_url": session_service.current_url,
        "opcua_user": session_service.current_user,
        "use_percent": bool(latest is not None and latest.get("signal_type") == 1),
    }
