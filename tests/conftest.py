import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_step_series(
    n: int = 200,
    dt: float = 0.5,
    step_index: int = 20,
    u0: float = 4.0,
    u1: float = 12.0,
):
    """Tiempo y actuador con un escalón en `step_index`."""
    time_data = [i * dt for i in range(n)]
    actuator = [u0 if i < step_index else u1 for i in range(n)]
    return time_data, actuator


@pytest.fixture
def fopdt_series():
    """Proceso FOPDT conocido: K=2.5, tau=10, L=3, y0=8."""
    k, tau, dead_time, y0 = 2.5, 10.0, 3.0, 8.0
    time_data, actuator = make_step_series()
    step_time = time_data[20]
    du = actuator[-1] - actuator[0]

    sensor = []
    for t in time_data:
        te = t - step_time - dead_time
        if te <= 0:
            sensor.append(y0)
        else:
            sensor.append(y0 + k * du * (1.0 - math.exp(-te / tau)))

    return {
        "time": time_data,
        "actuator": actuator,
        "sensor": sensor,
        "k": k,
        "tau": tau,
        "dead_time": dead_time,
    }


@pytest.fixture
def sopdt_series():
    """Proceso SOPDT conocido: K=1.8, tau1=12, tau2=4, L=2, y0=5."""
    k, tau1, tau2, dead_time, y0 = 1.8, 12.0, 4.0, 2.0, 5.0
    time_data, actuator = make_step_series(n=300)
    step_time = time_data[20]
    du = actuator[-1] - actuator[0]

    sensor = []
    for t in time_data:
        te = t - step_time - dead_time
        if te <= 0:
            sensor.append(y0)
        else:
            factor = 1.0 - (
                (tau1 * math.exp(-te / tau1) - tau2 * math.exp(-te / tau2))
                / (tau1 - tau2)
            )
            sensor.append(y0 + k * du * factor)

    return {
        "time": time_data,
        "actuator": actuator,
        "sensor": sensor,
        "k": k,
        "tau1": tau1,
        "tau2": tau2,
        "dead_time": dead_time,
    }


@pytest.fixture
def integrating_series():
    """Proceso integrador conocido: K=0.05, L=4, y0=10."""
    k, dead_time, y0 = 0.05, 4.0, 10.0
    time_data, actuator = make_step_series(n=200)
    step_time = time_data[20]
    du = actuator[-1] - actuator[0]

    sensor = []
    for t in time_data:
        te = t - step_time - dead_time
        sensor.append(y0 if te <= 0 else y0 + k * du * te)

    return {
        "time": time_data,
        "actuator": actuator,
        "sensor": sensor,
        "k": k,
        "dead_time": dead_time,
    }
