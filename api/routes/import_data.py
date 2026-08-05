from __future__ import annotations

"""
Importación de ensayos desde archivo (trace de CODESYS, CSV tabular o xlsx).

Flujo en dos pasos:

1. `POST /parse` — sube el archivo. El backend lo parsea y devuelve las
   variables encontradas con un resumen, más un `token`. El dataset queda
   retenido en memoria un rato.
2. `POST /load` — con el `token` y el mapeo rol -> variable del archivo, se
   llena el buffer y se activa el **modo importado**: las muestras del PLC se
   ignoran hasta `POST /clear`.

Está partido en dos porque entre medio hay una decisión humana (qué variable
es qué) y no tiene sentido volver a subir el archivo para tomarla.
"""

import secrets
import time
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from api.schemas.import_data import (
    ImportStatus,
    LoadRequest,
    LoadResponse,
    ParseResponse,
)
from api.schemas.opcua import ErrorResponse
from application.services.import_service import ImportedDataset, parse_file
from domain.services.scale_converter import normalize_scale_key

router = APIRouter(prefix="/api/import", tags=["Importación"])

VALIDATION_ERRORS = {
    400: {"model": ErrorResponse, "description": "Archivo o mapeo inválido"},
}

# Datasets parseados a la espera de que el usuario decida el mapeo.
# En memoria y con expiración: esto corre en un ctrlX, no puede acumular
# archivos indefinidamente.
_PENDING: dict[str, tuple[float, ImportedDataset]] = {}
_PENDING_TTL_S = 15 * 60
_PENDING_MAX = 5

MAX_FILE_BYTES = 20 * 1024 * 1024  # los traces reales pesan cientos de KB


def _prune_pending() -> None:
    now = time.time()
    expirados = [k for k, (ts, _) in _PENDING.items() if now - ts > _PENDING_TTL_S]
    for k in expirados:
        _PENDING.pop(k, None)

    # Si aún sobra, fuera los más viejos.
    while len(_PENDING) > _PENDING_MAX:
        oldest = min(_PENDING, key=lambda k: _PENDING[k][0])
        _PENDING.pop(oldest, None)


def _suggest_mapping(dataset: ImportedDataset) -> dict[str, Optional[str]]:
    """
    Heurística sobre los nombres del archivo, solo para pre-seleccionar.

    Busca pistas típicas: 'out'/'pid'/'mv'/'cv' para el actuador, 'sp'/'set'
    para la consigna, y el resto (o 'pv'/'scaled'/'sensor') como sensor.
    """
    nombres = [v.name for v in dataset.variables]

    def busca(pistas: tuple[str, ...], excluir: set[str]) -> Optional[str]:
        for n in nombres:
            low = n.lower()
            if n not in excluir and any(p in low for p in pistas):
                return n
        return None

    usados: set[str] = set()

    setpoint = busca(("setpoint", "set_point", "sp_", "_sp", "consigna", "local_auto"), usados)
    if setpoint:
        usados.add(setpoint)

    actuator = busca(("pid_out", "out", "mv", "cv", "actuador", "actuator", "salida"), usados)
    if actuator:
        usados.add(actuator)

    sensor = busca(("pv", "scaled", "sensor", "veloc", "temp", "nivel", "presion", "entrada"), usados)
    if sensor is None:
        sensor = next((n for n in nombres if n not in usados), None)

    return {"actuator": actuator, "sensor": sensor, "setpoint": setpoint}


@router.post(
    "/parse",
    response_model=ParseResponse,
    responses=VALIDATION_ERRORS,
    summary="Subir y analizar un archivo de ensayo",
    description=(
        "Acepta tres formatos, con detección automática:\n\n"
        "- **Trace de CODESYS / ctrlX** (`.trace.csv`): el export nativo del trace.\n"
        "- **CSV tabular**: encabezados en la primera fila, separador `,` o `;`.\n"
        "- **Excel** (`.xlsx`): primera hoja, misma estructura que el CSV.\n\n"
        "Devuelve las variables encontradas con resumen y preview —los nombres "
        "son los DEL ARCHIVO, no los roles— y un `token` para el paso siguiente. "
        "**No toca el buffer**: eso lo hace `/load` cuando el usuario ya decidió "
        "el mapeo.\n\n"
        "El tiempo se normaliza a segundos (los traces vienen en ms) y el "
        "periodo de muestreo se calcula del propio archivo."
    ),
)
async def parse_upload(file: UploadFile = File(...)) -> dict:
    content = await file.read()

    if not content:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")

    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"El archivo pesa más de {MAX_FILE_BYTES // (1024*1024)} MB.",
        )

    try:
        dataset = parse_file(file.filename or "archivo", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if len(dataset.time_s) < 40:
        raise HTTPException(
            status_code=400,
            detail=(
                f"El archivo solo trae {len(dataset.time_s)} muestras; se "
                "necesitan al menos 40 para identificar."
            ),
        )

    _prune_pending()
    token = secrets.token_hex(4)
    _PENDING[token] = (time.time(), dataset)

    return {
        "ok": True,
        "token": token,
        **dataset.describe(),
        "suggested_mapping": _suggest_mapping(dataset),
    }


@router.post(
    "/load",
    response_model=LoadResponse,
    responses=VALIDATION_ERRORS,
    summary="Cargar el archivo al buffer con el mapeo elegido",
    description=(
        "Convierte las series del archivo en muestras y **reemplaza el buffer** "
        "con ellas, activando el modo importado:\n\n"
        "- Las muestras del PLC se ignoran hasta `POST /clear`. Un buffer mezcla "
        "de archivo y tiempo real no describe ningún ensayo.\n"
        "- El **periodo de muestreo del ensayo pasa a ser el del archivo** (se "
        "actualiza en las condiciones de ensayo), para que las ventanas de "
        "identificación queden bien dimensionadas.\n"
        "- La escala elegida se aplica a los tres roles.\n\n"
        "Después de esto, `POST /api/identification/run`, los gráficos y el PID "
        "funcionan exactamente igual que con datos en vivo.\n\n"
        "Si hay un ensayo del runner en curso, devuelve 400: primero detenerlo."
    ),
)
def load_dataset(body: LoadRequest, request: Request) -> dict:
    state = request.app.state

    if state.test_runner_service.is_running():
        raise HTTPException(
            status_code=400,
            detail="Hay un ensayo en curso. Deténlo antes de importar datos.",
        )

    entry = _PENDING.get(body.token)
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail="El archivo expiró o el token no existe. Vuelve a subirlo.",
        )

    _, dataset = entry

    # Resolver las variables elegidas contra el archivo.
    roles = {"actuator": body.mapping.actuator, "sensor": body.mapping.sensor}
    if body.mapping.setpoint:
        roles["setpoint"] = body.mapping.setpoint

    series: dict[str, list[float]] = {}
    for role, nombre in roles.items():
        variable = dataset.variable(nombre)
        if variable is None:
            raise HTTPException(
                status_code=400,
                detail=f"La variable '{nombre}' (rol {role}) no existe en el archivo.",
            )
        series[role] = variable.values

    if body.mapping.actuator == body.mapping.sensor:
        raise HTTPException(
            status_code=400,
            detail=(
                "Actuador y sensor apuntan a la misma variable: con u == y no "
                "hay proceso que identificar."
            ),
        )

    scale = normalize_scale_key(body.scale)

    # La escala del archivo aplica a los tres roles, y el periodo de muestreo
    # del ensayo pasa a ser el del archivo. Sin esto, el umbral de detección y
    # el tamaño de la ventana estarían calculados para otro reloj.
    state.test_config_service.set_scales({r: scale for r in ("actuator", "sensor", "setpoint")})
    state.test_config_service.set_step_config(sample_period_s=dataset.sample_period_s)

    t0 = dataset.time_s[0]
    samples = []
    for i, t in enumerate(dataset.time_s):
        sample = {
            "timestamp": time.time(),
            "time": t - t0,  # re-basado a 0
            "actuator": series["actuator"][i],
            "sensor": series["sensor"][i],
            "setpoint": series.get("setpoint", [None] * len(dataset.time_s))[i],
            "source": "import",
        }
        samples.append(sample)

    info = {
        "source_name": dataset.source_name,
        "format": dataset.format,
        "samples": len(samples),
        "sample_period_s": dataset.sample_period_s,
        "mapping": dict(roles),
        "loaded_at": time.time(),
    }

    loaded = state.realtime_service.load_imported(samples, info)

    # La identificación previa era de otros datos.
    state.last_identification_result = None
    state.last_step_index = None

    _PENDING.pop(body.token, None)

    return {
        "ok": True,
        "source": "imported",
        "active": True,
        "buffer_size": loaded,
        **info,
    }


@router.post(
    "/clear",
    response_model=ImportStatus,
    summary="Salir del modo importado y volver a tiempo real",
    description=(
        "Descarta los datos de archivo, limpia el buffer y vuelve a aceptar "
        "muestras del PLC. Idempotente."
    ),
)
def clear_import(request: Request) -> dict:
    state = request.app.state

    state.realtime_service.restore_realtime()
    state.last_identification_result = None
    state.last_step_index = None

    return {"source": "realtime", "active": False}


@router.get(
    "/status",
    response_model=ImportStatus,
    summary="¿El buffer es de archivo o de tiempo real?",
    description=(
        "La vista lo consulta al cargar para mostrar (o no) el banner de "
        "modo importado."
    ),
)
def import_status(request: Request) -> dict:
    service = request.app.state.realtime_service

    if service.source != "imported":
        return {"source": "realtime", "active": False}

    return {"source": "imported", "active": True, **(service.import_info or {})}
