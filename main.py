from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from api.routes.opcua import router as opcua_router
from application.services.identification_pipeline_service import IdentificationPipelineService
from application.services.identification_service import IdentificationService
from application.services.opcua_session_service import OpcUaSessionService
from application.services.realtime_service import RealtimeService
from application.services.step_detector_service import StepDetectorService
from websocket.handlers import handle_ws_message
from websocket.manager import ConnectionManager
from fastapi.middleware.cors import CORSMiddleware


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
STATIC_DIR = WEB_DIR / "static"
TEMPLATES_DIR = WEB_DIR / "templates"

app = FastAPI()

ALLOWED_ORIGINS = [
    "http://localhost:5501",
    "http://127.0.0.1:5501",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

manager = ConnectionManager()
realtime_service = RealtimeService(max_buffer_size=5000)
identification_service = IdentificationService()
step_detector_service = StepDetectorService(min_step_delta=1.0)
pipeline_service = IdentificationPipelineService(
    identification_service=identification_service,
    step_detector_service=step_detector_service,
)

event_loop: asyncio.AbstractEventLoop | None = None
last_identification_result: dict | None = None
last_step_index: int | None = None
min_separation_samples = 20


def reset_runtime_state() -> None:
    global last_identification_result, last_step_index

    realtime_service.clear()
    last_identification_result = None
    last_step_index = None

    app.state.last_identification_result = None
    app.state.last_step_index = None


def get_current_use_percent() -> bool:
    latest = realtime_service.get_latest_sample()
    return bool(latest is not None and latest.get("signal_type") == 1)


def on_sample(sample: dict) -> None:
    global event_loop, last_identification_result, last_step_index

    realtime_service.add_sample(sample)
    latest_normalized = realtime_service.get_latest_sample()

    if event_loop is not None:
        try:
            asyncio.run_coroutine_threadsafe(
                manager.broadcast_json(
                    {
                        "type": "sample",
                        "data": latest_normalized if latest_normalized is not None else sample,
                    }
                ),
                event_loop,
            )
        except Exception:
            pass

    try:
        use_percent = get_current_use_percent()
        series = realtime_service.get_signal_series(use_percent=use_percent)

        if len(series.time) < 40:
            return

        step_index = step_detector_service.find_latest_rising_step_index(series.actuator)
        if step_index is None:
            return

        if last_step_index is not None and abs(step_index - last_step_index) < min_separation_samples:
            return

        result = pipeline_service.process_series(series)
        if result is None:
            return

        last_identification_result = result
        last_step_index = step_index

        app.state.last_identification_result = last_identification_result
        app.state.last_step_index = last_step_index

        if event_loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    manager.broadcast_json(
                        {
                            "type": "identification_result",
                            "data": result,
                        }
                    ),
                    event_loop,
                )
            except Exception:
                pass

    except Exception:
        pass


opcua_session_service = OpcUaSessionService(
    on_sample=on_sample,
    period_s=0.2,
    reset_runtime_state=reset_runtime_state,
)

app.state.manager = manager
app.state.realtime_service = realtime_service
app.state.identification_service = identification_service
app.state.step_detector_service = step_detector_service
app.state.pipeline_service = pipeline_service
app.state.opcua_session_service = opcua_session_service
app.state.last_identification_result = None
app.state.last_step_index = None

app.include_router(opcua_router)


@app.on_event("startup")
async def startup_event() -> None:
    global event_loop
    event_loop = asyncio.get_running_loop()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    opcua_session_service.stop()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.get("/health")
async def health() -> dict:
    latest = realtime_service.get_latest_sample()

    return {
        "status": "ok",
        "buffer_size": realtime_service.get_buffer_size(),
        "has_latest": latest is not None,
        "has_identification": last_identification_result is not None,
        "opcua_authenticated": opcua_session_service.is_authenticated,
        "opcua_url": opcua_session_service.current_url,
        "opcua_user": opcua_session_service.current_user,
        "use_percent": get_current_use_percent(),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)

    try:
        latest = realtime_service.get_latest_sample()

        if latest is not None:
            await manager.send_json(
                websocket,
                {
                    "type": "latest",
                    "data": latest,
                },
            )

        if last_identification_result is not None:
            await manager.send_json(
                websocket,
                {
                    "type": "identification_result",
                    "data": last_identification_result,
                },
            )

        while True:
            message = await websocket.receive_json()
            await handle_ws_message(
                message=message,
                realtime_service=realtime_service,
                manager=manager,
                websocket=websocket,
                latest_identification_result=last_identification_result,
            )

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)