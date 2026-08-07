"""Esquemas del resultado de identificación.

Este es EXACTAMENTE el mismo payload que:
  - devuelve GET /api/identification/latest
  - empuja el WebSocket en el mensaje {"type": "identification_result"}

Si cambias algo aquí, cambia en los dos lados.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PIDTuning(BaseModel):
    """
    Una sintonía PID.

    Se entregan las dos formas:
      - estándar (la del bloque PID de ctrlX):  Kp, Ti, Td
      - paralela:                               Kp, Ki, Kd

    con Ki = Kp/Ti y Kd = Kp*Td.
    """

    method: str = Field(..., description="IMC | Ziegler-Nichols | Cohen-Coon | SIMC | ...")
    kp: float = Field(..., description="Ganancia proporcional")
    ki: float = Field(..., description="Ganancia integral = Kp/Ti")
    kd: float = Field(..., description="Ganancia derivativa = Kp*Td")
    ti: Optional[float] = Field(None, description="Tiempo integral, en segundos")
    td: Optional[float] = Field(None, description="Tiempo derivativo, en segundos")
    lambda_: Optional[float] = Field(
        None,
        alias="lambda",
        description="Constante de tiempo de lazo cerrado deseada (solo IMC y SIMC)",
    )
    description: str = Field("", description="Texto para mostrar en la tabla de la UI")

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "method": "SIMC",
                "kp": 2.4981,
                "ki": 0.3122,
                "kd": 0.0,
                "ti": 8.0,
                "td": 0.0,
                "lambda": 2.0,
                "description": "SIMC (Skogestad) — Excelente rechazo de perturbaciones",
            }
        },
    }


class ModelResult(BaseModel):
    """Un modelo candidato con su ajuste y sus sintonías."""

    model_type: str = Field(..., description="fopdt | sopdt | integrating")
    gain: float = Field(..., description="Ganancia K del proceso")
    dead_time: float = Field(..., description="Tiempo muerto L, en segundos")
    fit_quality: float = Field(
        ...,
        description="R² en escala 0-1. Puede ser NEGATIVO si el modelo ajusta peor que la media.",
    )
    tf_string: str = Field(..., description="Función de transferencia legible")
    numerator: list[float]
    denominator: list[float]
    simulated: list[float] = Field(
        ...,
        description="Respuesta del modelo sobre la ventana. Misma longitud que window.time.",
    )
    pid_tunings: list[PIDTuning]
    tau: Optional[float] = Field(None, description="Solo en fopdt")
    tau1: Optional[float] = Field(None, description="Solo en sopdt: constante dominante")
    tau2: Optional[float] = Field(None, description="Solo en sopdt: constante rápida")

    model_config = {
        "json_schema_extra": {
            "example": {
                "model_type": "fopdt",
                "gain": 2.4891,
                "dead_time": 2.11,
                "fit_quality": 0.9991,
                "tf_string": "2.4891 * exp(-2.1100s) / (9.9974s + 1)",
                "numerator": [2.4891],
                "denominator": [9.9974, 1.0],
                "simulated": [8.0, 8.0, 8.31, 9.02],
                "pid_tunings": [
                    {
                        "method": "IMC",
                        "kp": 1.3374,
                        "ki": 0.1338,
                        "kd": 0.0,
                        "ti": 9.9974,
                        "td": 0.0,
                        "lambda": 2.4994,
                        "description": "IMC / Lambda — Robusto, recomendado para procesos lentos",
                    }
                ],
                "tau": 9.9974,
            }
        }
    }


class IdentificationWindow(BaseModel):
    """
    Tramo de datos usado para identificar.

    Es un recorte alrededor del escalón, NO el buffer completo. La UI lo
    necesita para graficar medido contra simulado: las longitudes tienen
    que coincidir. El tiempo viene re-basado a 0 en el inicio de la ventana.
    """

    time: list[float]
    actuator: list[float]
    sensor: list[float]
    setpoint: list[float]
    count: int


class IdentificationResult(BaseModel):
    step_index: int = Field(..., description="Índice del escalón dentro de la serie completa")
    order: str = Field(
        "auto",
        description=(
            "Orden pedido en las condiciones de ensayo. Con `auto` vienen los tres "
            "modelos rankeados; con `fopdt`/`sopdt`/`integrating`, `models` trae uno solo."
        ),
    )
    winner: str = Field(..., description="model_type del modelo con mejor R²")
    truncated: bool = Field(
        False,
        description=(
            "True si la ventana usada fue más corta que la que pide `duration_s`. "
            "El ajuste sigue siendo válido, pero se hizo sobre menos respuesta de "
            "la prevista: conviene revisar que la curva haya llegado al nuevo estable."
        ),
    )
    requested_post_samples: int = Field(
        0, description="Muestras de respuesta que pedía la duración configurada"
    )
    baseline: Optional[dict] = Field(
        None,
        description=(
            "Estado de la planta ANTES del escalón: `drift` (cuánto se movió el "
            "sensor durante la línea base), `response` (recorrido total) y "
            "`settled`. El ajuste toma el primer valor de la ventana como el "
            "régimen permanente de la entrada inicial; si el proceso venía "
            "moviéndose, ese supuesto es falso y la ganancia absorbe la deriva."
        ),
    )
    sampling: Optional[dict] = Field(
        None,
        description=(
            "Muestreo real medido sobre el buffer: `measured_period_s`, "
            "`effective_rate_hz` y `ratio` contra el periodo configurado."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Avisos sobre la calidad del ajuste. Un modelo puede converger y aun "
            "así no describir la planta: si el proceso responde más rápido que el "
            "muestreo, tau se colapsa a cero y queda una ganancia pura que parece "
            "válida. Conviene mostrarlos junto al resultado."
        ),
    )
    window: IdentificationWindow
    models: list[ModelResult] = Field(..., description="Ordenados de mejor a peor R²")

    model_config = {
        "json_schema_extra": {
            "example": {
                "step_index": 30,
                "order": "auto",
                "winner": "fopdt",
                "window": {
                    "time": [0.0, 0.5, 1.0],
                    "actuator": [4.0, 4.0, 12.0],
                    "sensor": [8.0, 8.0, 8.31],
                    "setpoint": [12.0, 12.0, 12.0],
                    "count": 3,
                },
                "models": [],
            }
        }
    }
