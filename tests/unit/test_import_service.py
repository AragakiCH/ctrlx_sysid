"""Parser de importación: trace de CODESYS, CSV tabular y xlsx."""

import io
from pathlib import Path

import pytest

from application.services.import_service import parse_file

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures_data_sinto.trace.csv"


# --------------------------------------------------------------------------- #
# Trace de CODESYS (con el archivo real del usuario)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def trace_real():
    return parse_file("data_sinto_05_08.trace.csv", FIXTURE.read_bytes())


def test_detecta_el_formato_trace(trace_real):
    assert trace_real.format == "codesys-trace"


def test_extrae_los_tres_canales(trace_real):
    nombres = [v.name for v in trace_real.variables]

    assert nombres == [
        "DB_HMI.HMI_SP_Local_Automatico",
        "Main_Control.Velocidad_Scaled",
        "Main_Control.PID_OUT",
    ]


def test_todas_las_series_alineadas(trace_real):
    n = len(trace_real.time_s)
    assert n == 1517
    for v in trace_real.variables:
        assert len(v.values) == n


def test_convierte_ms_a_segundos(trace_real):
    assert trace_real.time_s[0] == pytest.approx(0.002)
    assert trace_real.time_s[-1] == pytest.approx(30.322)
    assert trace_real.sample_period_s == pytest.approx(0.020)


def test_resumen_por_variable(trace_real):
    resumen = trace_real.describe()["variables"]
    pid = next(v for v in resumen if v["name"] == "Main_Control.PID_OUT")

    assert pid["min"] == pytest.approx(25.0)
    assert pid["max"] == pytest.approx(50.0)
    assert pid["constant"] is False


def test_busca_variable_sin_distinguir_mayusculas(trace_real):
    assert trace_real.variable("main_control.pid_out") is not None
    assert trace_real.variable("NoExiste") is None


def test_trace_sin_datos_lanza():
    texto = "[key]; [value]\n0.Variable; X\n0.Data;\n"
    with pytest.raises(ValueError, match="ningún canal con datos"):
        parse_file("vacio.trace.csv", texto.encode())


# --------------------------------------------------------------------------- #
# CSV tabular
# --------------------------------------------------------------------------- #


def make_csv(sep=",", time_header="tiempo_s"):
    filas = [f"{time_header}{sep}valvula{sep}temperatura"]
    for i in range(60):
        filas.append(f"{i * 0.5}{sep}{4.0 if i < 20 else 12.0}{sep}{8.0 + i * 0.1}")
    return "\n".join(filas).encode()


def test_csv_con_comas():
    ds = parse_file("datos.csv", make_csv(","))

    assert ds.format == "csv"
    assert [v.name for v in ds.variables] == ["valvula", "temperatura"]
    assert len(ds.time_s) == 60
    assert ds.sample_period_s == pytest.approx(0.5)


def test_csv_con_punto_y_coma():
    ds = parse_file("datos.csv", make_csv(";"))
    assert len(ds.variables) == 2


def test_csv_columna_de_tiempo_por_nombre():
    """La columna de tiempo no tiene que ser la primera."""
    filas = ["valvula,time_s,temperatura"]
    for i in range(50):
        filas.append(f"4.0,{i * 0.2},8.0")

    ds = parse_file("d.csv", "\n".join(filas).encode())

    assert [v.name for v in ds.variables] == ["valvula", "temperatura"]
    assert ds.time_s[1] == pytest.approx(0.2)


def test_csv_tiempo_en_ms_se_convierte():
    filas = ["t_ms,senal"]
    for i in range(50):
        filas.append(f"{i * 20},{float(i)}")

    ds = parse_file("d.csv", "\n".join(filas).encode())

    assert ds.sample_period_s == pytest.approx(0.020)


def test_csv_decimales_con_coma():
    """Excel en español exporta 3,14 en vez de 3.14."""
    contenido = "t;x\n0;1,5\n1;2,5\n2;3,5\n" + "".join(
        f"{i};{i}\n" for i in range(3, 50)
    )
    ds = parse_file("d.csv", contenido.encode())

    assert ds.variables[0].values[0] == pytest.approx(1.5)


def test_csv_filas_no_numericas_se_descartan():
    # 51 filas de datos: 50 numéricas y una de basura en medio.
    contenido = "t,x\n0,1\nbasura,texto\n1,2\n" + "".join(
        f"{i},{i}\n" for i in range(2, 50)
    )
    ds = parse_file("d.csv", contenido.encode())

    # Solo entran las numéricas, y las series siguen alineadas entre sí.
    assert len(ds.time_s) == 50
    assert len(ds.variables[0].values) == 50


def test_csv_sin_datos_lanza():
    with pytest.raises(ValueError):
        parse_file("d.csv", b"a,b\nx,y\n")


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #


def make_xlsx():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["tiempo", "actuador_plc", "sensor_plc"])
    for i in range(60):
        ws.append([i * 0.5, 4.0 if i < 20 else 12.0, 8.0 + i * 0.1])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_xlsx_se_parsea():
    ds = parse_file("datos.xlsx", make_xlsx())

    assert ds.format == "xlsx"
    assert [v.name for v in ds.variables] == ["actuador_plc", "sensor_plc"]
    assert len(ds.time_s) == 60


def test_xlsx_se_detecta_por_contenido_aunque_no_tenga_extension():
    """Los .xlsx son zips: empiezan con PK. La extensión puede mentir."""
    ds = parse_file("datos_sin_extension", make_xlsx())
    assert ds.format == "xlsx"
