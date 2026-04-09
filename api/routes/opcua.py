from __future__ import annotations

import os
import socket
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


router = APIRouter(prefix="/api/opcua", tags=["opcua"])


class OpcUaLoginRequest(BaseModel):
    user: str
    password: str
    url: str


class OpcUaDiscoverItem(BaseModel):
    url: str
    host: str
    port: int
    tcp_ok: bool
    source: str


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


@router.get("/discover", response_model=list[OpcUaDiscoverItem])
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


@router.post("/login")
def opcua_login(body: OpcUaLoginRequest, request: Request) -> dict:
    session_service = request.app.state.opcua_session_service

    try:
        return session_service.login(
            url=body.url,
            user=body.user,
            password=body.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/logout")
def opcua_logout(request: Request) -> dict:
    session_service = request.app.state.opcua_session_service
    return session_service.logout(clear_runtime=True)


@router.get("/status")
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