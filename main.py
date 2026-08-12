from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from api.routes.health import router as health_router
from api.routes.identification import router as identification_router
from api.routes.import_data import router as import_router
from api.routes.task_cycle import router as task_cycle_router
from api.routes.opcua import router as opcua_router
from api.routes.simulation import router as test_router
from application.services.identification_pipeline_service import IdentificationPipelineService
from application.services.identification_service import IdentificationService
from application.services.opcua_session_service import OpcUaSessionService
from application.services.realtime_service import RealtimeService
from application.services.step_detector_service import StepDetectorService
from application.services.task_cycle_service import TaskCycleService
from application.services.test_config_service import TestConfigService
from application.services.test_runner_service import TestRunnerService
from websocket.handlers import handle_ws_message
from websocket.manager import ConnectionManager
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
STATIC_DIR = WEB_DIR / "static"
TEMPLATES_DIR = WEB_DIR / "templates"

# En Windows, mimetypes lee los tipos MIME del registro. Es frecuente que otro
# programa haya dejado ".css" o ".js" apuntando a "text/plain". StaticFiles usa
# mimetypes.guess_type, así que serviría la hoja de estilos con el MIME
# equivocado y Chrome/Brave la RECHAZAN por strict MIME checking: la página
# carga sin estilos aunque el archivo se descargue con 200 OK.
#
# Estas líneas fuerzan los tipos correctos y deben ir ANTES de montar
# StaticFiles. En Linux no cambian nada.
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")

APP_PREFIX = os.getenv("APP_PREFIX", "/api-sysid")

API_DESCRIPTION = """
Identificación de sistemas y sintonía PID sobre un ctrlX Core X3.

Lee señales de un programa PLC por OPC UA, detecta escalones en el actuador
y ajusta modelos FOPDT, SOPDT e integrador, entregando las constantes PID.

## Flujo de conexión

Los endpoints están pensados para usarse en este orden:

1. `GET  /api/opcua/discover` — buscar dispositivos en la red
2. `POST /api/opcua/discover-programs` — listar programas del PLC
3. `POST /api/opcua/discover-variables` — listar **todas** las variables del programa
4. `POST /api/opcua/login` — abrir sesión con el mapeo elegido
5. `WS   /ws` — recibir muestras y resultados en tiempo real

## Unidades

Cada muestra viaja en dos escalas:

- `actuator`, `sensor`, `setpoint`: el valor **crudo** tal como está en el PLC
- `actuator_pct`, `sensor_pct`, `setpoint_pct`: convertido a **0-100 % de span**

La escala física de cada rol se declara en `POST /api/test/scales`
(`ma` = 4-20 mA, `pct` = 0-100 %, `v` = 0-10 V) y la conversión ocurre
**íntegramente en el backend**: no se escribe ninguna variable del PLC.

Toda la identificación corre sobre el porcentaje, porque es la única escala en
la que la ganancia del modelo es adimensional y las sintonías PID resultan
comparables entre lazos.

## Ensayo

`POST /api/test/config` guarda el escalón que el operador va a aplicar desde el
ctrlX. El backend no lo comanda; usa los números para fijar el umbral de
detección, dimensionar la ventana de identificación y elegir el modelo.

## WebSocket

Swagger no documenta WebSockets. El protocolo completo de `/ws` está en el
`README.md` del repositorio.
"""

TAGS_METADATA = [
    {
        "name": "OPC UA",
        "description": "Conexión con el PLC, descubrimiento de variables y mapeo de señales.",
    },
    {
        "name": "Identificación",
        "description": (
            "Resultados del motor de identificación. Se generan **automáticamente** "
            "cuando se detecta un escalón; no hay endpoint para dispararla a mano."
        ),
    },
    {
        "name": "Importación",
        "description": (
            "Ensayos desde archivo: trace de CODESYS, CSV tabular o Excel. "
            "Mientras el modo importado está activo, las muestras del PLC se "
            "**ignoran** para no mezclar fuentes."
        ),
    },
    {
        "name": "Ensayo",
        "description": (
            "Escala física de cada señal (4-20 mA / 0-100 % / 0-10 V) y condiciones "
            "del escalón. Las conversiones son **internas del backend**; no se "
            "escribe nada en el PLC."
        ),
    },
    {"name": "Sistema", "description": "Chequeos de estado."},
    {"name": "Vistas", "description": "Páginas HTML de la aplicación web."},
]

app = FastAPI(
    title="ctrlX System Identification API",
    description=API_DESCRIPTION,
    version="1.1.0",
    openapi_tags=TAGS_METADATA,
    contact={"name": "Equipo ctrlX SysID"},
)

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

##app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
##templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SAMPLE_PERIOD_S = 0.2

manager = ConnectionManager()

# Escalas por rol y condiciones del ensayo. Es la fuente de verdad de las
# conversiones mA/%/V: todo ocurre en el backend, sin escribir en el PLC.
test_config_service = TestConfigService(sample_period_s=SAMPLE_PERIOD_S)

realtime_service = RealtimeService(
    max_buffer_size=5000,
    scale_provider=test_config_service.scale_for,
)
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


def broadcast_from_thread(message: dict) -> None:
    """
    Publica un mensaje por WebSocket desde un hilo que no es el del event loop.

    Tanto el lector del PLC como el runner del ensayo corren en hilos propios,
    y `manager.broadcast_json` es una corrutina: hay que agendarla en el loop.
    Si el loop todavía no arrancó, el mensaje se descarta en silencio (solo pasa
    en el arranque, cuando aún no hay nadie conectado).
    """
    if event_loop is None:
        return

    try:
        asyncio.run_coroutine_threadsafe(manager.broadcast_json(message), event_loop)
    except Exception:
        pass


def sensor_percent_now() -> float | None:
    """Última lectura del sensor en % de span, o None si no hay muestras."""
    latest = realtime_service.get_latest_sample()
    if latest is None:
        return None

    valor = latest.get("sensor_pct")
    return float(valor) if isinstance(valor, (int, float)) else None


# Ejecuta el perfil del actuador con su propio reloj.
#
# `sensor_provider` y `on_capture_start` son lo que permite pre-posicionar la
# planta: el runner lleva el actuador al valor inicial, mira el sensor hasta que
# deja de moverse y recién entonces limpia el buffer y empieza a grabar. Sin
# esto la línea base guarda el viaje al punto de partida y el ajuste toma esa
# deriva como parte de la respuesta al escalón.
test_runner_service = TestRunnerService(
    test_config_service=test_config_service,
    on_event=broadcast_from_thread,
    sensor_provider=sensor_percent_now,
    on_capture_start=lambda: reset_runtime_state(),
)


def reset_runtime_state() -> None:
    global last_identification_result, last_step_index

    realtime_service.clear()
    last_identification_result = None
    last_step_index = None

    app.state.last_identification_result = None
    app.state.last_step_index = None


def get_current_use_percent() -> bool:
    """
    La identificación siempre corre en % de span.

    Ya no depende de `uiSignalType`: `RealtimeService` convierte cada rol con la
    escala que el usuario eligió en la vista (4-20 mA / 0-100 % / 0-10 V), así
    que los campos `_pct` son siempre la escala común correcta.
    """
    return True


def on_sample(sample: dict) -> None:
    global event_loop, last_identification_result, last_step_index

    # Etiqueta la muestra con lo que el ensayo estaba comandando en ese instante.
    # Va en campos aparte (`actuator_cmd`) y NO pisa el `actuator` leído del PLC:
    # así se puede comparar lo que se pidió contra lo que hizo la planta y
    # detectar que el actuador no obedeció, saturó o llegó tarde.
    command = test_runner_service.current_command()
    if command is not None:
        sample.update(command)

    realtime_service.add_sample(sample)
    latest_normalized = realtime_service.get_latest_sample()

    broadcast_from_thread(
        {
            "type": "sample",
            "data": latest_normalized if latest_normalized is not None else sample,
        }
    )

    try:
        use_percent = get_current_use_percent()
        series = realtime_service.get_signal_series(use_percent=use_percent)

        if len(series.time) < 40:
            return

        # El umbral y el tamaño de la ventana salen del ensayo configurado en la
        # vista (paso 2), no de constantes fijas.
        step_detector_service.min_step_delta = test_config_service.step_threshold_pct()

        step_index = step_detector_service.find_latest_rising_step_index(series.actuator)
        if step_index is None:
            return

        if last_step_index is not None and abs(step_index - last_step_index) < min_separation_samples:
            return

        result = pipeline_service.process_series(
            series,
            pre_samples=test_config_service.pre_samples(),
            post_samples=test_config_service.post_samples(),
            order=test_config_service.get_order(),
        )
        if result is None:
            return

        last_identification_result = result
        last_step_index = step_index

        app.state.last_identification_result = last_identification_result
        app.state.last_step_index = last_step_index

        broadcast_from_thread({"type": "identification_result", "data": result})

    except Exception:
        pass


opcua_session_service = OpcUaSessionService(
    on_sample=on_sample,
    period_s=SAMPLE_PERIOD_S,
    reset_runtime_state=reset_runtime_state,
)

task_cycle_service = TaskCycleService(opcua_session_service)

app.state.manager = manager
app.state.realtime_service = realtime_service
app.state.identification_service = identification_service
app.state.step_detector_service = step_detector_service
app.state.pipeline_service = pipeline_service
app.state.opcua_session_service = opcua_session_service
app.state.test_config_service = test_config_service
app.state.task_cycle_service = task_cycle_service
app.state.test_runner_service = test_runner_service
# Las rutas la usan para limpiar el buffer al arrancar un ensayo.
app.state.reset_runtime_state = reset_runtime_state
app.state.last_identification_result = None
app.state.last_step_index = None

app.include_router(opcua_router)
app.include_router(identification_router)
app.include_router(test_router)
app.include_router(import_router)
app.include_router(task_cycle_router)
app.include_router(health_router)

@app.on_event("startup")
async def startup_event() -> None:
    global event_loop
    event_loop = asyncio.get_running_loop()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    # El ensayo primero: si se apaga el lector antes, el runner seguiría
    # comandando contra una sesión ya cerrada.
    test_runner_service.stop()
    opcua_session_service.stop()


@app.get(
    "/",
    response_class=HTMLResponse,
    tags=["Vistas"],
    summary="Pantalla de login",
    description="Formulario de conexión: host, credenciales, programa y mapeo de señales.",
)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"app_prefix": APP_PREFIX},
    )


@app.get(
    "/app",
    response_class=HTMLResponse,
    tags=["Vistas"],
    summary="Aplicación principal",
    description="Gráficos en tiempo real, resultados de identificación, sintonía PID y Bode.",
)
async def app_view(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_prefix": APP_PREFIX},
    )


# /health vive en api/routes/health.py


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

        # Se lee de app.state, no del global: `POST /api/identification/run`
        # también escribe ahí y así el WS ve el resultado recalculado.
        if app.state.last_identification_result is not None:
            await manager.send_json(
                websocket,
                {
                    "type": "identification_result",
                    "data": app.state.last_identification_result,
                },
            )

        # Si se recarga la página en mitad de un ensayo, el cliente nuevo tiene
        # que poder engancharse sin esperar al siguiente tick.
        if test_runner_service.is_running():
            await manager.send_json(
                websocket,
                {
                    "type": "test_state",
                    "data": {
                        **test_runner_service.get_state(),
                        "plan": test_runner_service.get_plan(),
                    },
                },
            )

        while True:
            message = await websocket.receive_json()
            await handle_ws_message(
                message=message,
                realtime_service=realtime_service,
                manager=manager,
                websocket=websocket,
                latest_identification_result=app.state.last_identification_result,
                test_runner_service=test_runner_service,
            )

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)



if __name__ == "__main__":
    # Los .js, .css y .html se leen del disco en cada request, pero las rutas de
    # Python solo se registran al arrancar. Sin recarga automática es fácil
    # editar el backend, recargar el navegador y recibir 404 en un endpoint que
    # "ya existe" en el archivo.
    #
    # Se vigilan solo los directorios de Python: si `web/` estuviera incluido,
    # cada retoque al JS reiniciaría el proceso y con él se caería la sesión
    # OPC UA y el buffer de muestras en medio de un ensayo.
    reload_dirs = [
        str(BASE_DIR / name)
        for name in ("api", "application", "domain", "infrastructure", "websocket")
        if (BASE_DIR / name).is_dir()
    ]

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8001")),
        reload=os.getenv("RELOAD", "1") != "0",
        reload_dirs=reload_dirs,
        reload_includes=["main.py"],
    )