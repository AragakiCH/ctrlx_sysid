from __future__ import annotations

from typing import Callable, Sequence


def nelder_mead(
    objective: Callable[[list[float]], float],
    x0: Sequence[float],
    step: Sequence[float] | None = None,
    max_iter: int = 400,
    tol: float = 1e-9,
) -> tuple[list[float], float]:
    """
    Nelder-Mead (simplex) en Python puro.

    Se usa para refinar los parámetros de los modelos cuando scipy no está
    disponible en el runtime (por ejemplo, dentro del snap del ctrlX).
    Devuelve (mejor_x, mejor_valor).
    """
    n = len(x0)
    if n == 0:
        return list(x0), float("inf")

    if step is None:
        step = [max(abs(v) * 0.1, 1e-3) for v in x0]

    alpha, gamma, rho, sigma = 1.0, 2.0, 0.5, 0.5

    simplex: list[list[float]] = [list(x0)]
    for i in range(n):
        point = list(x0)
        point[i] = point[i] + step[i]
        simplex.append(point)

    scores = [objective(p) for p in simplex]

    for _ in range(max_iter):
        order = sorted(range(len(simplex)), key=lambda i: scores[i])
        simplex = [simplex[i] for i in order]
        scores = [scores[i] for i in order]

        if abs(scores[-1] - scores[0]) < tol:
            break

        centroid = [
            sum(simplex[i][j] for i in range(n)) / n for j in range(n)
        ]

        # reflexión
        reflected = [centroid[j] + alpha * (centroid[j] - simplex[-1][j]) for j in range(n)]
        f_reflected = objective(reflected)

        if scores[0] <= f_reflected < scores[-2]:
            simplex[-1], scores[-1] = reflected, f_reflected
            continue

        # expansión
        if f_reflected < scores[0]:
            expanded = [centroid[j] + gamma * (reflected[j] - centroid[j]) for j in range(n)]
            f_expanded = objective(expanded)

            if f_expanded < f_reflected:
                simplex[-1], scores[-1] = expanded, f_expanded
            else:
                simplex[-1], scores[-1] = reflected, f_reflected
            continue

        # contracción
        contracted = [centroid[j] + rho * (simplex[-1][j] - centroid[j]) for j in range(n)]
        f_contracted = objective(contracted)

        if f_contracted < scores[-1]:
            simplex[-1], scores[-1] = contracted, f_contracted
            continue

        # reducción
        best = simplex[0]
        for i in range(1, len(simplex)):
            simplex[i] = [best[j] + sigma * (simplex[i][j] - best[j]) for j in range(n)]
            scores[i] = objective(simplex[i])

    best_index = min(range(len(simplex)), key=lambda i: scores[i])
    return simplex[best_index], scores[best_index]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
