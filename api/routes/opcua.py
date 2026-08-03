from __future__ import annotations

import os
import socket

from fastapi import APIRouter, HTTPException, Request

from api.schemas.opcua import (
    DiscoverProgramsResponse,
    DiscoverVariablesResponse,
    ErrorResponse,
    LoginResponse,
    LogoutResponse,
    MappingResponse,
    OpcUaDiscoverItem,
    OpcUaDiscoverProgramsRequest,
    OpcUaDiscoverVariablesRequest,
    OpcUaLoginRequest,
    OpcUaMappingRequest,
    StatusResponse,
)

router = APIRouter(prefix="/api/opcua", tags=["OPC UA"])


# Respuestas de error comunes, para que Swagger las liste en cada endpoint.
CONNECTION_ERRORS = {
    400: {"model": ErrorResponse, "description": "Faltan datos o son inválidos"},
    502: {"model": ErrorResponse, "description": "Fallo al hablar con el PLC"},
}


def _probe_tcp(host: str, port: int, timeout: float = 0.7) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _normalize_candidates(raw_urls: list[str]) -> list[tuple[str, str, int, str]]:
    items: list[tuple[str, str, int, str]] = []
    seen: set[str] = set()

    for raw in raw_urls:
        u = (raw or "").strip()
        if not u or not u.startswith("opc.tcp://"):
            continue

        try:
            hostport = u.split("://", 1)[1].split("/", 1)[0]
            if ":" in hostport:
                host, port_s = hostport.split(":", 1)
                port = int(port_s)
            else:
                host = hostport
                port = 4840
        except Exception:
            continue

        key = f"{host}:{port}"
        if key in seen:
            continue
        seen.add(key)

        items.append((u, host, port, "candidate"))

    return items


@router.get(
    "/discover",
    response_model=list[OpcUaDiscoverItem],
    summary="Buscar servidores OPC UA en la red",
    description=(
        "Sondea candidatos y devuelve cuáles respondieron en el puerto TCP.\n\n"
        "Los candidatos salen de, en este orden:\n"
        "1. El host del propio request (si no es localhost).\n"
        "2. La variable de entorno `OPCUA_DISCOVERY_URLS` (separada por comas).\n"
        "3. Los fallbacks `127.0.0.1`, `localhost` y `192.168.1.1`, todos en el 4840.\n\n"
        "Los que tienen `tcp_ok: true` van primero. **No requiere credenciales.**"
    ),
)
def discover_opcua(request: Request) -> list[OpcUaDiscoverItem]:
    candidates: list[str] = []

    # 1) Host del request, por si entras directo al equipo
    host_hdr = request.headers.get("host", "")
    host_only = host_hdr.split(":")[0].strip() if host_hdr else ""
    if host_only and host_only not in {"127.0.0.1", "localhost"}:
        candidates.append(f"opc.tcp://{host_only}:4840")

    # 2) Env opcional: OPCUA_DISCOVERY_URLS=url1,url2,...
    env_urls = os.getenv("OPCUA_DISCOVERY_URLS", "").strip()
    if env_urls:
        candidates.extend([x.strip() for x in env_urls.split(",") if x.strip()])

    # 3) Fallbacks simples
    candidates.extend(
        [
            "opc.tcp://127.0.0.1:4840",
            "opc.tcp://localhost:4840",
            "opc.tcp://192.168.1.1:4840",
        ]
    )

    normalized = _normalize_candidates(candidates)

    items: list[OpcUaDiscoverItem] = []
    for url, host, port, source in normalized:
        tcp_ok = _probe_tcp(host, port)
        items.append(
            OpcUaDiscoverItem(
                url=url,
                host=host,
                port=port,
                tcp_ok=tcp_ok,
                source=source,
            )
        )

    items.sort(key=lambda x: (not x.tcp_ok, x.host, x.port))
    return items


@router.post(
    "/discover-programs",
    response_model=DiscoverProgramsResponse,
    responses=CONNECTION_ERRORS,
    summary="Listar los programas PLC del dispositivo",
    description=(
        "Conecta con las credenciales dadas y devuelve los programas colgados de\n"
        "`Objects/Datalayer/plc/app/Application/sym`.\n\n"
        "Es el **paso 2** del flujo: sirve para llenar el desplegable de programas."
    ),
)
def discover_programs(body: OpcUaDiscoverProgramsRequest, request: Request) -> dict:
    session_service = request.app.state.opcua_session_service

    try:
        return session_service.discover_programs(
            url=body.url,
            user=body.user,
            password=body.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/discover-variables",
    response_model=DiscoverVariablesResponse,
    responses=CONNECTION_ERRORS,
    summary="Listar TODAS las variables de un programa",
    description=(
        "Devuelve todas las variables del programa con su tipo y su valor actual,\n"
        "**sin filtrar por nombre**. La vista decide cuál es actuador, sensor,\n"
        "setpoint, tiempo y tipo de señal.\n\n"
        "`suggested_mapping` trae una propuesta basada en los nombres clásicos\n"
        "(`rActuator`, `rSensor`, ...) para pre-seleccionar los desplegables. Es\n"
        "solo una sugerencia: si el programa usa otros nombres, esos roles vienen\n"
        "en `null` y los elige el usuario.\n\n"
        "Es el **paso 3** del flujo."
    ),
)
def discover_variables(body: OpcUaDiscoverVariablesRequest, request: Request) -> dict:
    session_service = request.app.state.opcua_session_service

    try:
        return session_service.discover_variables(
            url=body.url,
            user=body.user,
            password=body.password,
            program_name=body.program_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/login",
    response_model=LoginResponse,
    responses=CONNECTION_ERRORS,
    summary="Abrir sesión y arrancar la lectura cíclica",
    description=(
        "Valida la conexión, guarda el mapeo y arranca el hilo que lee el PLC\n"
        "cada 200 ms. A partir de aquí el WebSocket empieza a emitir `sample`.\n\n"
        "Limpia el buffer y cualquier identificación previa.\n\n"
        "El campo `mapping` es opcional; los roles que se omitan se resuelven con\n"
        "los alias por defecto. `mapping` en la respuesta es el **mapeo efectivo**,\n"
        "ya completado.\n\n"
        "Es el **paso 4** del flujo."
    ),
)
def opcua_login(body: OpcUaLoginRequest, request: Request) -> dict:
    session_service = request.app.state.opcua_session_service

    try:
        return session_service.login(
            url=body.url,
            user=body.user,
            password=body.password,
            program_name=body.program_name,
            mapping=body.mapping.model_dump() if body.mapping else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/mapping",
    response_model=MappingResponse,
    responses=CONNECTION_ERRORS,
    summary="Cambiar el mapeo de señales en caliente",
    description=(
        "Reasigna qué variable cumple cada rol sin cerrar la sesión ni reconectar.\n\n"
        "**Limpia el buffer y descarta la identificación en curso**, porque las\n"
        "muestras viejas fueron tomadas con otro mapeo y mezclarlas daría un\n"
        "modelo sin sentido.\n\n"
        "Devuelve 400 si no hay sesión activa."
    ),
)
def update_mapping(body: OpcUaMappingRequest, request: Request) -> dict:
    session_service = request.app.state.opcua_session_service

    try:
        return session_service.update_mapping(body.mapping.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Cerrar sesión y detener la lectura",
    description=(
        "Para el hilo de lectura, borra credenciales y mapeo, y limpia el buffer.\n"
        "Es idempotente: llamarlo sin sesión activa igual devuelve `ok: true`."
    ),
)
def opcua_logout(request: Request) -> dict:
    session_service = request.app.state.opcua_session_service
    return session_service.logout(clear_runtime=True)


@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Estado de la sesión OPC UA",
    description=(
        "Útil para diagnosticar. Ojo con la diferencia entre los dos flags:\n\n"
        "- `authenticated`: hay credenciales cargadas.\n"
        "- `connected`: el hilo de lectura está **vivo ahora mismo**.\n\n"
        "Pueden diferir: si se cae la red, `authenticated` sigue en `true` mientras\n"
        "el lector reintenta con backoff exponencial."
    ),
)
def opcua_status(request: Request) -> dict:
    session_service = request.app.state.opcua_session_service
    realtime_service = request.app.state.realtime_service

    latest_identification_result = getattr(
        request.app.state,
        "last_identification_result",
        None,
    )

    latest_sample = realtime_service.get_latest_sample()

    return session_service.get_status(
        buffer_size=realtime_service.get_buffer_size(),
        has_latest=latest_sample is not None,
        has_identification=latest_identification_result is not None,
    )
