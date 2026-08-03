from __future__ import annotations

import math


def r_squared(measured: list[float], simulated: list[float]) -> float:
    """
    Coeficiente de determinación R².

    Puede ser negativo si el modelo ajusta peor que la media; eso es
    información útil para el ranking, así que NO se recorta a 0.
    """
    if not measured or not simulated or len(measured) != len(simulated):
        return 0.0

    n = len(measured)
    y_mean = sum(measured) / n

    ss_tot = sum((y - y_mean) ** 2 for y in measured)
    ss_res = sum((measured[i] - simulated[i]) ** 2 for i in range(n))

    if ss_tot <= 1e-12:
        return 0.0

    return 1.0 - (ss_res / ss_tot)


def rmse(measured: list[float], simulated: list[float]) -> float:
    """Error cuadrático medio."""
    if not measured or not simulated or len(measured) != len(simulated):
        return 0.0

    n = len(measured)
    ss_res = sum((measured[i] - simulated[i]) ** 2 for i in range(n))
    return math.sqrt(ss_res / n)


def linear_fit(x: list[float], y: list[float]) -> tuple[float, float]:
    """
    Recta y = a*x + b por mínimos cuadrados. Devuelve (pendiente, intercepto).
    Mucho menos sensible al ruido que la secante entre dos puntos.
    """
    n = len(x)
    if n < 2 or n != len(y):
        return 0.0, 0.0

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    den = sum((x[i] - mean_x) ** 2 for i in range(n))

    if abs(den) < 1e-12:
        return 0.0, mean_y

    slope = num / den
    return slope, mean_y - slope * mean_x


def linear_slope(x: list[float], y: list[float]) -> float:
    """Solo la pendiente. Atajo sobre `linear_fit`."""
    return linear_fit(x, y)[0]
