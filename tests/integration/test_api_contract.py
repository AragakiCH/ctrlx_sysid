"""Verifica que el contrato documentado en README.md y en Swagger sea real."""

import pytest
from fastapi.testclient import TestClient

import main
from application.services.identification_pipeline_service import (
    IdentificationPipelineService,
)
from application.services.identification_service import IdentificationService
from application.services.step_detector_service import StepDetectorService
from domain.models.signals import SignalSeries


@pytest.fixture
def client():
    main.app.state.last_identification_result = None
    return TestClient(main.app)


@pytest.fixture
def identified(fopdt_series):
    pipeline = IdentificationPipelineService(
        identification_service=IdentificationService(),
        step_detector_service=StepDetectorService(min_step_delta=1.0),
    )
    return pipeline.process_series(
        SignalSeries(
            time=fopdt_series["time"],
            actuator=fopdt_series["actuator"],
            sensor=fopdt_series["sensor"],
            setpoint=[12.0] * len(fopdt_series["time"]),
            signal_type=1,
        ),
        pre_samples=10,
        post_samples=120,
    )


# --------------------------------------------------------------------------- #
# Swagger
# --------------------------------------------------------------------------- #


def test_openapi_se_genera(client):
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_todos_los_endpoints_documentados_existen(client):
    paths = client.get("/openapi.json").json()["paths"]

    esperados = {
        "/health",
        "/api/opcua/discover",
        "/api/opcua/discover-programs",
        "/api/opcua/discover-variables",
        "/api/opcua/login",
        "/api/opcua/mapping",
        "/api/opcua/logout",
        "/api/opcua/status",
        "/api/identification/latest",
    }

    assert esperados <= set(paths)


def test_cada_endpoint_tiene_summary(client):
    paths = client.get("/openapi.json").json()["paths"]

    for path, operations in paths.items():
        for method, operation in operations.items():
            assert operation.get("summary"), f"{method.upper()} {path} sin summary"


# --------------------------------------------------------------------------- #
# Responses de los endpoints
# --------------------------------------------------------------------------- #


def test_health_trae_las_claves_documentadas(client):
    body = client.get("/health").json()

    assert set(body) == {
        "status",
        "buffer_size",
        "has_latest",
        "has_identification",
        "opcua_authenticated",
        "opcua_url",
        "opcua_user",
        "use_percent",
    }


def test_status_trae_las_claves_documentadas(client):
    body = client.get("/api/opcua/status").json()

    assert set(body) == {
        "authenticated",
        "connected",
        "url",
        "user",
        "buffer_size",
        "has_latest",
        "has_identification",
        "last_error",
        "last_login_ts",
        "program_name",
        "mapping",
    }


def test_discover_devuelve_lista_con_tcp_ok(client):
    body = client.get("/api/opcua/discover").json()

    assert isinstance(body, list)
    for item in body:
        assert set(item) == {"url", "host", "port", "tcp_ok", "source"}


def test_errores_usan_el_campo_detail(client):
    r = client.post(
        "/api/opcua/discover-programs",
        json={"url": "", "user": "u", "password": "p"},
    )

    assert r.status_code == 400
    assert "detail" in r.json()


def test_identificacion_sin_datos_da_404(client):
    assert client.get("/api/identification/latest").status_code == 404


def test_mapping_sin_sesion_da_400(client):
    r = client.post("/api/opcua/mapping", json={"mapping": {"actuator": "A"}})

    assert r.status_code == 400
    assert "sesión" in r.json()["detail"]


def test_logout_es_idempotente(client):
    assert client.post("/api/opcua/logout").json() == {"ok": True, "logged_out": True}
    assert client.post("/api/opcua/logout").json() == {"ok": True, "logged_out": True}


def test_login_acepta_mapping_opcional(client):
    """El esquema debe validar aunque `mapping` no venga."""
    sin_mapping = client.post(
        "/api/opcua/login",
        json={"url": "", "user": "u", "password": "p", "program_name": "X"},
    )
    con_mapping = client.post(
        "/api/opcua/login",
        json={
            "url": "",
            "user": "u",
            "password": "p",
            "program_name": "X",
            "mapping": {"actuator": "MiValvula"},
        },
    )

    # 400 por la URL vacía, no 422 por el esquema
    assert sin_mapping.status_code == 400
    assert con_mapping.status_code == 400


# --------------------------------------------------------------------------- #
# Payload de identificación (idéntico al del WebSocket)
# --------------------------------------------------------------------------- #


def test_latest_devuelve_el_payload_completo(client, identified):
    main.app.state.last_identification_result = identified

    body = client.get("/api/identification/latest").json()

    assert set(body) == {
        "step_index",
        "order",
        "winner",
        "truncated",
        "requested_post_samples",
        "sampling",
        "warnings",
        "window",
        "models",
    }
    assert body["winner"] == body["models"][0]["model_type"]


def test_simulated_cuadra_con_la_ventana(client, identified):
    """Es lo que permite graficar medido contra simulado."""
    main.app.state.last_identification_result = identified

    body = client.get("/api/identification/latest").json()
    count = body["window"]["count"]

    assert len(body["window"]["time"]) == count
    assert len(body["window"]["sensor"]) == count
    for model in body["models"]:
        assert len(model["simulated"]) == count


def test_modelos_ordenados_de_mejor_a_peor(client, identified):
    main.app.state.last_identification_result = identified

    models = client.get("/api/identification/latest").json()["models"]
    calidades = [m["fit_quality"] for m in models]

    assert calidades == sorted(calidades, reverse=True)


def test_pid_trae_las_dos_formas_y_descripcion(client, identified):
    main.app.state.last_identification_result = identified

    models = client.get("/api/identification/latest").json()["models"]
    tunings = [t for m in models for t in m["pid_tunings"]]

    assert tunings
    for t in tunings:
        assert set(t) >= {"method", "kp", "ki", "kd", "ti", "td", "lambda", "description"}
        assert t["description"]


def test_la_ventana_arranca_en_cero(client, identified):
    """window.time viene re-basado, no comparte escala con sample.time."""
    main.app.state.last_identification_result = identified

    body = client.get("/api/identification/latest").json()
    assert body["window"]["time"][0] == 0.0
