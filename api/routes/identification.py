from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

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


@router.post(
    "/run",
    response_model=IdentificationResult,
    responses={
        400: {
            "model": ErrorResponse,
            "description": "El buffer actual no permite identificar",
        }
    },
    summary="Re-identificar con el buffer actual",
    description=(
        "Vuelve a correr la identificación sobre las muestras que ya están en el\n"
        "buffer, usando las **condiciones de ensayo vigentes**\n"
        "(`GET /api/test/config`).\n\n"
        "La identificación automática solo se dispara una vez por escalón, y espera\n"
        "a tener la ventana completa que pide `duration_s`. Este endpoint es el\n"
        "disparador manual: sirve para recalcular tras cambiar el `order` o la\n"
        "escala de una señal, sin repetir el ensayo en el PLC.\n\n"
        "Con `allow_partial=true` (por defecto) identifica con las muestras que\n"
        "haya, aunque sean menos que las que pide la duración configurada, y marca\n"
        "el resultado con `truncated: true`. Un proceso de primer orden queda bien\n"
        "definido a partir de unas 3-4 constantes de tiempo, así que una ventana\n"
        "más corta que el ensayo suele bastar; el flag está para que se sepa que el\n"
        "ajuste se hizo sobre menos datos de los previstos.\n\n"
        "Con `allow_partial=false` exige la ventana completa y devuelve 400 si\n"
        "todavía no está.\n\n"
        "Devuelve 400 si no hay escalón en el buffer o si ningún modelo converge."
    ),
)
def run_identification(
    request: Request,
    allow_partial: bool = Query(
        True,
        description=(
            "Identificar aunque la ventana sea más corta que la duración configurada."
        ),
    ),
) -> dict:
    state = request.app.state

    realtime_service = state.realtime_service
    pipeline_service = state.pipeline_service
    step_detector_service = state.step_detector_service
    test_config_service = state.test_config_service

    series = realtime_service.get_signal_series(use_percent=True)

    nominal = test_config_service.describe_step_config()["sample_period_s"]
    muestreo = realtime_service.sampling_report(nominal_period_s=nominal)
    periodo = muestreo["measured_period_s"] or nominal

    if len(series.time) < 40:
        pista = ""
        if muestreo["ratio"] and muestreo["ratio"] > 1.5:
            pista = (
                f" El muestreo real está en {muestreo['measured_period_s']} s "
                f"({muestreo['effective_rate_hz']} Hz), {muestreo['ratio']}× más lento "
                f"que los {nominal} s configurados: la lectura del PLC no da para "
                "más. Alarga la duración del ensayo o sube el 'Tiempo de muestreo' "
                "del paso 1 a un valor alcanzable."
            )

        raise HTTPException(
            status_code=400,
            detail=(
                f"Solo hay {len(series.time)} muestras en el buffer. "
                f"Se necesitan al menos 40.{pista}"
            ),
        )

    threshold = test_config_service.step_threshold_pct()

    # La ventana se dimensiona con el periodo MEDIDO, no con el configurado.
    post_needed = test_config_service.post_samples(period_s=periodo)

    step_detector_service.min_step_delta = threshold

    # Se diagnostica antes de llamar al pipeline: si devuelve None no se sabe
    # cuál de las dos condiciones falló, y ese silencio es lo que hace que la
    # identificación "no pase nada" sin explicación.
    step_index = step_detector_service.find_latest_rising_step_index(series.actuator)

    if step_index is None:
        # Sin esto el usuario solo sabe que "no hubo escalón" y tiene que
        # adivinar si el problema es la escala, el mapeo o el propio ensayo.
        # El backend ya tiene los valores: se los devolvemos.
        scales = test_config_service.get_scales()
        step = test_config_service.describe_step_config()
        unidad = step["actuator_scale"]["unit"]

        observado = (
            f"El actuador se movió entre {min(series.actuator):.1f} % y "
            f"{max(series.actuator):.1f} % (escala declarada: "
            f"{step['actuator_scale']['label']})"
        )
        esperado = (
            f"el ensayo espera un salto de {step['step_from']} → {step['step_to']} "
            f"{unidad}, o sea {threshold * 2:.1f} %"
        )

        raise HTTPException(
            status_code=400,
            detail=(
                f"No se detectó ningún escalón de al menos {threshold:.1f} % en las "
                f"{len(series.time)} muestras del buffer. {observado}, pero {esperado}. "
                f"Revisa que el 'Tipo de señal' del actuador ({scales['actuator']}) sea "
                "el correcto y que los valores del paso 2 estén en esa misma escala."
            ),
        )

    post_available = len(series.time) - step_index
    segundos = post_available * periodo

    # Piso duro: por debajo de esto el ajuste no es confiable ni con buena
    # voluntad — la curva no alcanzó a definir ni la ganancia ni la constante.
    MINIMO_ABSOLUTO = 30

    if post_available < MINIMO_ABSOLUTO:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Se detectó el escalón, pero solo hay {post_available} muestras "
                f"después de él ({segundos:.1f} s). Se necesitan al menos "
                f"{MINIMO_ABSOLUTO} para que el ajuste tenga sentido. Espera unos "
                "segundos y vuelve a intentar."
            ),
        )

    truncated = post_available < post_needed

    if truncated and not allow_partial:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Se detectó el escalón, pero solo hay {post_available} muestras "
                f"después de él ({segundos:.1f} s) y la duración configurada pide "
                f"{post_needed}. Espera a que termine el ensayo, o baja la "
                "'Duración total' en el paso 2."
            ),
        )

    try:
        result = pipeline_service.process_series(
            series,
            pre_samples=test_config_service.pre_samples(period_s=periodo),
            post_samples=min(post_needed, post_available) if truncated else post_needed,
            order=test_config_service.get_order(),
        )
    except ValueError as exc:
        # El validador de la ventana rechaza los casos degenerados (sin cambio en
        # el actuador, sin respuesta en el sensor). Es un problema con los datos,
        # no del servidor: 400, no 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Se detectó el escalón y hay muestras suficientes, pero ningún "
                "modelo convergió. Suele pasar si el sensor no reaccionó al salto "
                "o si la señal está saturada."
            ),
        )

    result["truncated"] = truncated
    result["requested_post_samples"] = post_needed
    result["sampling"] = muestreo
    result["warnings"] = _build_warnings(result, muestreo, periodo, nominal)

    state.last_identification_result = result
    state.last_step_index = result.get("step_index")

    return result


def _build_warnings(
    result: dict, muestreo: dict, periodo: float, nominal: float
) -> list[str]:
    """
    Avisos sobre la CALIDAD del ajuste, no sobre errores.

    Un modelo puede converger y aun así no describir nada: si la planta responde
    más rápido de lo que se muestrea, el transitorio cae entre dos muestras, la
    constante de tiempo se colapsa al mínimo y la función de transferencia queda
    en una ganancia pura con `tau = 0.0000s`. La ganancia sale bien —solo
    necesita los extremos— y eso hace que el resultado parezca válido.
    """
    avisos: list[str] = []

    # La planta tiene que estar quieta cuando empieza la ventana: el ajuste
    # toma `sensor[0]` como el régimen permanente de `actuador[0]`.
    base = result.get("baseline") or {}
    if base.get("samples") and not base.get("settled", True):
        avisos.append(
            f"El sensor todavía se estaba moviendo antes del escalón: derivó "
            f"{base['drift']:.2f} % durante la línea base, un {base['ratio'] * 100:.0f} % "
            f"de la respuesta total. El modelo supone que la planta parte en "
            "reposo, así que la ganancia absorbe esa deriva. Deja asentar el "
            "proceso antes del escalón o alarga el retardo del paso 2."
        )

    if muestreo.get("ratio") and muestreo["ratio"] > 1.5:
        avisos.append(
            f"El muestreo real es {muestreo['measured_period_s']} s "
            f"({muestreo['ratio']}× el configurado de {nominal} s). La lectura "
            "del PLC no alcanza el ritmo pedido."
        )

    for modelo in result.get("models", []):
        tau = modelo.get("tau") or modelo.get("tau1")
        if tau is None:
            continue

        if tau <= 1e-5:
            avisos.append(
                f"{modelo['model_type'].upper()}: la constante de tiempo salió "
                "prácticamente nula. La respuesta se completó entre dos muestras, "
                f"así que con un periodo de {periodo:.2f} s no hay forma de medir "
                "la dinámica. Solo la ganancia es confiable."
            )
        elif tau < 3 * periodo:
            avisos.append(
                f"{modelo['model_type'].upper()}: tau ({tau:.3f} s) es menor que "
                f"3 periodos de muestreo ({3 * periodo:.2f} s). Hacen falta más "
                "puntos durante el transitorio para que sea confiable."
            )

        break  # solo el modelo ganador

    ganador = (result.get("models") or [{}])[0]
    if ganador.get("fit_quality") is not None and ganador["fit_quality"] < 0:
        avisos.append(
            f"R² negativo ({ganador['fit_quality'] * 100:.1f} %): el modelo ajusta "
            "peor que una línea horizontal. No lo uses para sintonizar."
        )

    return avisos
