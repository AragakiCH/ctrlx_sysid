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
    TestPlan,
    TestRunState,
    TestSnapshotResponse,
    TestStartResponse,
    WriterRequest,
    WriterResponse,
)
from domain.services.scale_converter import convert as convert_value
from domain.services.scale_converter import to_percent

router = APIRouter(prefix="/api/test", tags=["Ensayo"])

VALIDATION_ERRORS = {
    400: {"model": ErrorResponse, "description": "Parámetros inválidos"},
}


def _service(request: Request):
    return request.app.state.test_config_service


def _runner(request: Request):
    return request.app.state.test_runner_service


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
    # El periodo no solo dimensiona ventanas: es el ritmo al que el lector
    # consulta el PLC. Si solo se guardara aquí, el campo de la vista no
    # tendría ningún efecto sobre el muestreo real.
    if body.sample_period_s is not None:
        try:
            request.app.state.opcua_session_service.set_period(body.sample_period_s)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Si el usuario activó la sincronización, bajar el ciclo del MainTask
        # cuando el muestreo pedido queda por debajo. No se propaga el fallo:
        # que el PLC no acepte el cambio no invalida la configuración del
        # ensayo, y el estado del ciclo se consulta aparte.
        try:
            request.app.state.task_cycle_service.sync_with_period(body.sample_period_s)
        except Exception as exc:
            print(f"[CICLO] No se pudo sincronizar el ciclo de tarea: {exc}")

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
    "/series",
    summary="Serie completa del buffer",
    description=(
        "Arrays paralelos de tiempo, actuador, sensor y setpoint con TODO lo "
        "que hay en el buffer (en vivo o importado). Con `percent=true` en "
        "escala 0-100 %; si no, en la escala cruda.\n\n"
        "Es lo que la vista usa para redibujar los gráficos tras importar un "
        "archivo o al recargar la página."
    ),
)
def get_series(
    request: Request,
    percent: bool = Query(False, description="Devolver en % de span"),
) -> dict:
    return request.app.state.realtime_service.get_series_payload(use_percent=percent)


# --------------------------------------------------------------------------- #
# Ejecución del ensayo
# --------------------------------------------------------------------------- #


@router.get(
    "/plan",
    response_model=TestPlan,
    responses=VALIDATION_ERRORS,
    summary="Perfil del actuador, muestra a muestra",
    description=(
        "Devuelve el perfil que el backend va a comandar: **una entrada por "
        "muestra** del ensayo, al mismo periodo con el que se ejecutará.\n\n"
        "No confundir con `/preview`, que reparte N puntos solo para dibujar la "
        "vista previa del paso 2. Aquí `time[i]` es el instante real de la "
        "muestra `i`.\n\n"
        "Si hay un ensayo en curso devuelve el plan de ESE ensayo (congelado al "
        "arrancar); si no, el que se generaría con la configuración actual."
    ),
)
def get_plan(request: Request) -> dict:
    try:
        return _runner(request).get_plan()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/run",
    response_model=TestRunState,
    summary="Estado del ensayo",
    description=(
        "Avance del ensayo en curso. Responde siempre 200: si no hay ninguno, "
        "`status` vale `idle`.\n\n"
        "Los mismos datos llegan por WebSocket en cada `test_tick`, así que la "
        "vista normalmente no necesita hacer polling sobre este endpoint. Está "
        "para diagnosticar y para reconstruir el estado al recargar la página."
    ),
)
def get_run_state(request: Request) -> dict:
    return _runner(request).get_state()


@router.post(
    "/start",
    response_model=TestStartResponse,
    responses=VALIDATION_ERRORS,
    summary="Arrancar el ensayo",
    description=(
        "Arranca el reloj del ensayo en el backend y empieza a emitir eventos "
        "por WebSocket: `test_started` con el plan completo, luego un "
        "`test_tick` por muestra, y `test_finished` al terminar.\n\n"
        "**El reloj vive en el backend, no en el navegador.** Así el ensayo "
        "sobrevive a que se cierre la pestaña y no depende de los timers del "
        "navegador, que se estrangulan en segundo plano.\n\n"
        "Por defecto limpia el buffer de muestras y descarta la identificación "
        "anterior, para que el ensayo nuevo no arrastre datos del anterior.\n\n"
        "**En esta fase no se escribe nada en el PLC**: el runner solo calcula y "
        "publica el valor comandado. El operador sigue aplicando el escalón "
        "desde el ctrlX. `writes_enabled` indica si eso ya cambió.\n\n"
        "Devuelve 400 si ya hay un ensayo corriendo o si la configuración del "
        "paso 2 no es válida."
    ),
)
def start_test(
    request: Request,
    clear_buffer: bool = Query(
        True,
        description="Vaciar el buffer y la identificación previa antes de arrancar.",
    ),
    presettle: bool = Query(
        True,
        description=(
            "Llevar el actuador al valor inicial y esperar a que el sensor se "
            "estabilice antes de empezar a grabar. Requiere escritura armada."
        ),
    ),
) -> dict:
    runner = _runner(request)

    if runner.is_running():
        raise HTTPException(
            status_code=400,
            detail="Ya hay un ensayo en curso. Deténlo antes de arrancar otro.",
        )

    if clear_buffer:
        # Se limpia ANTES de arrancar: si se hiciera después, las primeras
        # muestras del ensayo nuevo se borrarían junto con las viejas.
        #
        # Con `presettle` el runner vuelve a limpiarlo al terminar de asentar la
        # planta, que es el momento en que de verdad empieza la grabación.
        request.app.state.reset_runtime_state()

    try:
        return runner.start(presettle=presettle)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/writer",
    response_model=WriterResponse,
    summary="Estado de la escritura al PLC",
    description=(
        "Dice si el ensayo va a **mover el actuador** o solo dibujar, y si la "
        "variable mapeada admite escritura."
    ),
)
def get_writer(request: Request) -> dict:
    runner = _runner(request)
    session = request.app.state.opcua_session_service

    writable, detail = session.check_writable("actuator")
    variable = None

    reader = getattr(session, "_reader", None)
    if reader is not None:
        variable = reader.resolve_role_variable("actuator")

    # `enabled` refleja la INTENCIÓN, no si el escritor está construido en este
    # instante. Entre ensayos la variable se suelta a propósito; si se reportara
    # el estado interno, la casilla se desmarcaría sola al terminar cada ensayo
    # y el usuario tendría que rearmarla cada vez.
    return {
        "enabled": runner.write_intent,
        "role": "actuator",
        "variable": variable,
        "writable": writable,
        "detail": detail,
    }


@router.post(
    "/writer",
    response_model=WriterResponse,
    responses=VALIDATION_ERRORS,
    summary="Armar o desarmar la escritura al PLC",
    description=(
        "**Mientras no se arme, el ensayo no toca el PLC**: calcula el perfil y "
        "lo publica, pero el actuador no se mueve. Armar es un paso aparte a "
        "propósito, para que pulsar *Inicio* no mueva equipo por accidente.\n\n"
        "Al armar se comprueba que haya sesión OPC UA, que el rol `actuator` "
        "tenga una variable asignada y que el servidor la declare escribible. "
        "Si algo falla se devuelve 400 y la escritura queda desarmada.\n\n"
        "Se escribe sobre **la misma variable que se eligió en el paso 1**: la "
        "que esté mapeada al rol `actuator` en ese momento.\n\n"
        "No se puede armar ni desarmar con un ensayo en curso: cambiar quién "
        "gobierna el actuador a mitad de camino dejaría un registro donde una "
        "parte del escalón se aplicó y otra no."
    ),
)
def set_writer(body: WriterRequest, request: Request) -> dict:
    runner = _runner(request)
    session = request.app.state.opcua_session_service

    if runner.is_running():
        raise HTTPException(
            status_code=400,
            detail=(
                "Hay un ensayo en curso. Deténlo antes de cambiar la escritura: "
                "si no, una parte del escalón se aplicaría y otra no."
            ),
        )

    if not body.enabled:
        runner.disarm()
        return {
            "enabled": False,
            "role": "actuator",
            "variable": None,
            "writable": False,
            "detail": "Escritura desarmada. El ensayo solo dibujará el perfil.",
        }

    writable, detail = session.check_writable("actuator")

    if not writable:
        runner.disarm()
        raise HTTPException(status_code=400, detail=detail)

    reader = getattr(session, "_reader", None)
    variable = reader.resolve_role_variable("actuator") if reader else None

    # Se guarda la FÁBRICA, no el escritor ya construido: al terminar cada
    # ensayo se suelta la variable para devolvérsela a la HMI, y en el arranque
    # siguiente se reconstruye contra la sesión OPC UA vigente.
    runner.arm(lambda: session.make_writer("actuator"))

    return {
        "enabled": True,
        "role": "actuator",
        "variable": variable,
        "writable": True,
        "detail": (
            f"Escritura armada sobre '{variable}'. Cada ensayo va a mover el "
            "actuador y al terminar devolverá la variable a la HMI."
        ),
    }


@router.post(
    "/reset",
    response_model=TestRunState,
    summary="Reiniciar la sesión de trabajo",
    description=(
        "Deja el backend como recién arrancado, **sin cerrar la sesión OPC UA**:\n\n"
        "1. Detiene el ensayo en curso, devolviendo el actuador a su valor "
        "inicial y desarmando la escritura.\n"
        "2. Sale del modo importado, si estaba activo.\n"
        "3. Vacía el buffer de muestras y descarta la identificación.\n\n"
        "La configuración del paso 1 y 2 (mapeo, escalas, escalón) **se "
        "conserva**: reiniciar sirve para repetir un ensayo que salió mal, no "
        "para volver a configurarlo todo.\n\n"
        "Es idempotente."
    ),
)
def reset_session(request: Request) -> dict:
    runner = _runner(request)
    realtime = request.app.state.realtime_service

    # El ensayo primero: si se limpiara el buffer con el runner todavía
    # comandando, las muestras siguientes entrarían en un buffer "nuevo"
    # mezcladas con el final del ensayo viejo.
    estado = runner.stop()

    if not realtime.accepts_realtime:
        realtime.restore_realtime()

    request.app.state.reset_runtime_state()

    return estado


@router.post(
    "/stop",
    response_model=TestRunState,
    summary="Detener el ensayo",
    description=(
        "Corta el ensayo en curso y emite `test_stopped`. Es idempotente: sin "
        "ensayo activo devuelve el estado tal cual, sin error.\n\n"
        "Las muestras capturadas hasta el momento **se conservan**. Si alcanzan "
        "para identificar, se puede llamar a `POST /api/identification/run`."
    ),
)
def stop_test(request: Request) -> dict:
    return _runner(request).stop()


# --------------------------------------------------------------------------- #
# Utilidades de conversión
# --------------------------------------------------------------------------- #


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
