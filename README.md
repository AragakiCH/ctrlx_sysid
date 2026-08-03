# ctrlX System Identification API

Identificación de sistemas y sintonía PID sobre un **ctrlX Core X3**.

Lee señales de un programa PLC por OPC UA, detecta escalones en el actuador y ajusta
tres modelos candidatos (FOPDT, SOPDT e integrador), entregando la función de
transferencia y las constantes PID por varios métodos.

---

## Índice

- [Arranque](#arranque)
- [Documentación interactiva (Swagger)](#documentación-interactiva-swagger)
- [Flujo de conexión](#flujo-de-conexión)
- [Mapeo de señales](#mapeo-de-señales)
- [Unidades](#unidades)
- [Referencia REST](#referencia-rest)
- [Protocolo WebSocket](#protocolo-websocket)
- [Payload de identificación](#payload-de-identificación)
- [Notas de integración](#notas-de-integración)
- [Tests](#tests)

---

## Arranque

```bash
pip install -r requirements.txt
python main.py
```

Levanta en `http://0.0.0.0:8000`.

| Variable de entorno | Por defecto | Para qué sirve |
|---|---|---|
| `APP_PREFIX` | `/api-sysid` | Prefijo cuando corre como app del ctrlX |
| `OPCUA_DISCOVERY_URLS` | *(vacío)* | Candidatos extra para `/discover`, separados por comas |
| `OPCUA_TIMEOUT_CONNECT` | `5.0` | Timeout de conexión OPC UA, en segundos |
| `OPCUA_CLIENT_CERT` | *(vacío)* | Certificado cliente, si el endpoint exige seguridad |
| `OPCUA_CLIENT_KEY` | *(vacío)* | Clave privada del certificado |

---

## Documentación interactiva (Swagger)

Con el servicio arriba:

| URL | Qué es |
|---|---|
| `http://localhost:8000/docs` | **Swagger UI** — probar los endpoints desde el navegador |
| `http://localhost:8000/redoc` | ReDoc, más cómodo para solo leer |
| `http://localhost:8000/openapi.json` | Especificación OpenAPI cruda |

También hay una copia versionada en [`docs/openapi.json`](docs/openapi.json) para
**importar en Postman o Insomnia** sin levantar el servicio.

> Swagger **no documenta WebSockets**. El protocolo de `/ws` está más abajo, en
> [Protocolo WebSocket](#protocolo-websocket).

---

## Flujo de conexión

Los endpoints están pensados para usarse en este orden. Cada paso alimenta al siguiente:

```
1. GET  /api/opcua/discover           -> lista de dispositivos      -> elige una url
2. POST /api/opcua/discover-programs  -> lista de programas         -> elige program_name
3. POST /api/opcua/discover-variables -> TODAS las variables        -> arma el mapping
4. POST /api/opcua/login              -> arranca la lectura cíclica
5. WS   /ws                           -> muestras + identificación en vivo
```

Los pasos 1-3 **no abren sesión**: cada uno conecta, lee y desconecta. La sesión
persistente solo la crea el `login`.

---

## Mapeo de señales

El backend necesita saber qué variable del PLC cumple cada rol. Hay cinco:

| Rol | Obligatorio | Qué es |
|---|---|---|
| `time` | Sí | Tiempo del proceso, en segundos |
| `actuator` | Sí | Salida del controlador (MV) |
| `sensor` | Sí | Medición del proceso (PV) |
| `setpoint` | No | Consigna |
| `signal_type` | No | Escala de la señal. Si vale `1`, se convierte de 4-20 mA a % |

**El mapeo lo decide la vista, no el backend.** `POST /discover-variables` devuelve
todas las variables del programa sin filtrar, y el usuario elige en desplegables.

Existe un `suggested_mapping` con los nombres clásicos (`rActuator`, `rSensor`,
`rTimeSec`, `rSetPoint`, `uiSignalType`) que solo sirve para **pre-seleccionar** los
desplegables. Si el programa usa otros nombres, esos roles vienen en `null` y el
usuario los asigna a mano.

Un rol que se envíe como `null` en el `login` se resuelve con esos mismos alias como
último recurso. El campo `mapping` de la respuesta es el **mapeo efectivo**, ya
completado — úsalo, no el que enviaste.

---

## Unidades

Cada muestra viaja en **dos escalas a la vez**:

| Campo | Escala |
|---|---|
| `actuator`, `sensor`, `setpoint` | Valor crudo, tal como está en el PLC |
| `actuator_pct`, `sensor_pct`, `setpoint_pct` | Convertido a 0-100 % |

La conversión mA → % es `(v - 4) / 16 * 100` y **solo se aplica si `signal_type == 1`**.
En cualquier otro caso los campos `_pct` repiten el valor crudo tal cual.

La identificación se hace sobre la escala en porcentaje cuando `signal_type == 1`; si
no, sobre la cruda. Eso significa que **la ganancia `K` viene en las unidades de esa
escala**, y las constantes PID también.

---

## Referencia REST

Todos los errores usan el formato de FastAPI: `{"detail": "mensaje"}`.

| Código | Cuándo |
|---|---|
| `400` | Faltan campos, están vacíos o no hay sesión activa |
| `404` | Solo en `/api/identification/latest`: aún no hay resultado |
| `422` | El JSON no cumple el esquema (lo genera FastAPI) |
| `502` | Falló la comunicación con el PLC |

---

### `GET /api/opcua/discover`

Busca servidores OPC UA. **No requiere credenciales.**

Los candidatos salen del host del request, de `OPCUA_DISCOVERY_URLS` y de unos
fallbacks fijos. Los que respondieron (`tcp_ok: true`) van primero.

**Response `200`**

```json
[
  {
    "url": "opc.tcp://192.168.1.1:4840",
    "host": "192.168.1.1",
    "port": 4840,
    "tcp_ok": true,
    "source": "candidate"
  },
  {
    "url": "opc.tcp://127.0.0.1:4840",
    "host": "127.0.0.1",
    "port": 4840,
    "tcp_ok": false,
    "source": "candidate"
  }
]
```

> `tcp_ok` solo dice que el puerto está abierto. No valida que sea un OPC UA real ni
> que las credenciales sirvan.

---

### `POST /api/opcua/discover-programs`

Lista los programas colgados de `Objects/Datalayer/plc/app/Application/sym`.

**Request**

```json
{
  "url": "opc.tcp://192.168.1.1:4840",
  "user": "boschrexroth",
  "password": "********"
}
```

**Response `200`**

```json
{
  "ok": true,
  "url": "opc.tcp://192.168.1.1:4840",
  "user": "boschrexroth",
  "programs": ["PLC_PRG", "PRG_Horno"]
}
```

**Errores**

```json
{ "detail": "Conectó al OPC UA, pero no se encontró el nodo 'sym'." }
{ "detail": "Se encontró el nodo 'sym', pero no se pudieron identificar programas válidos." }
```

---

### `POST /api/opcua/discover-variables`

Devuelve **todas** las variables del programa, con su tipo y su valor en el momento
de la lectura.

**Request**

```json
{
  "url": "opc.tcp://192.168.1.1:4840",
  "user": "boschrexroth",
  "password": "********",
  "program_name": "PLC_PRG"
}
```

**Response `200`**

```json
{
  "ok": true,
  "url": "opc.tcp://192.168.1.1:4840",
  "user": "boschrexroth",
  "program_name": "PLC_PRG",
  "roles": ["time", "actuator", "sensor", "setpoint", "signal_type"],
  "variables": [
    {
      "name": "rActuator",
      "node_id": "ns=2;s=plc/app/Application/sym/PLC_PRG/rActuator",
      "data_type": "Float",
      "value": 12.0,
      "numeric": true,
      "readable": true,
      "error": null
    },
    {
      "name": "rSensor",
      "node_id": "ns=2;s=plc/app/Application/sym/PLC_PRG/rSensor",
      "data_type": "Float",
      "value": 7.21,
      "numeric": true,
      "readable": true,
      "error": null
    }
  ],
  "suggested_mapping": {
    "time": "rTimeSec",
    "actuator": "rActuator",
    "sensor": "rSensor",
    "setpoint": "rSetPoint",
    "signal_type": null
  }
}
```

**Campos de cada variable**

| Campo | Notas |
|---|---|
| `name` | Browse name. **Es lo que se manda en el `mapping`.** |
| `node_id` | NodeId OPC UA. Informativo, no se usa en el mapeo. |
| `data_type` | `Float`, `Double`, `Int16`... o `UNKNOWN` si no se pudo determinar |
| `value` | `null` si no es un escalar simple o si la lectura falló |
| `numeric` | `true` si sirve como señal continua. Útil para atenuar el resto en la UI |
| `readable` | `false` si la lectura dio error |
| `error` | El motivo del fallo, cuando `readable` es `false` |

Las variables vienen **ordenadas alfabéticamente**, sin distinguir mayúsculas.

---

### `POST /api/opcua/login`

Valida la conexión, guarda el mapeo y arranca el hilo que lee el PLC cada 200 ms.
A partir de aquí el WebSocket empieza a emitir `sample`.

Limpia el buffer y descarta cualquier identificación previa.

**Request** — `mapping` es opcional; los roles omitidos caen a los alias.

```json
{
  "url": "opc.tcp://192.168.1.1:4840",
  "user": "boschrexroth",
  "password": "********",
  "program_name": "PLC_PRG",
  "mapping": {
    "time": "Tiempo_Proceso",
    "actuator": "Valvula_Salida",
    "sensor": "Temp_Horno",
    "setpoint": "Consigna",
    "signal_type": null
  }
}
```

**Response `200`**

```json
{
  "ok": true,
  "url": "opc.tcp://192.168.1.1:4840",
  "user": "boschrexroth",
  "program_name": "PLC_PRG",
  "mapping": {
    "time": "Tiempo_Proceso",
    "actuator": "Valvula_Salida",
    "sensor": "Temp_Horno",
    "setpoint": "Consigna",
    "signal_type": null
  },
  "started": true
}
```

**Errores**

```json
{ "detail": "Falta la URL OPC UA." }
{ "detail": "Falta seleccionar el programa OPC UA." }
{ "detail": "Conectó al OPC UA, pero no se encontró el programa 'PLC_PRG' dentro de 'sym'." }
```

---

### `POST /api/opcua/mapping`

Reasigna qué variable cumple cada rol **sin cerrar la sesión ni reconectar**.

Limpia el buffer y descarta la identificación en curso: las muestras viejas fueron
tomadas con otro mapeo y mezclarlas daría un modelo sin sentido.

**Request**

```json
{
  "mapping": {
    "time": "rTimeSec",
    "actuator": "OtraValvula",
    "sensor": "rSensor",
    "setpoint": null,
    "signal_type": null
  }
}
```

**Response `200`**

```json
{
  "ok": true,
  "mapping": {
    "time": "rTimeSec",
    "actuator": "OtraValvula",
    "sensor": "rSensor",
    "setpoint": null,
    "signal_type": null
  }
}
```

**Error `400`** — `{ "detail": "No hay sesión OPC UA activa." }`

---

### `POST /api/opcua/logout`

Para el hilo de lectura, borra credenciales y mapeo, y limpia el buffer.
Es idempotente: sin sesión activa igual responde `ok: true`.

**Response `200`**

```json
{ "ok": true, "logged_out": true }
```

---

### `GET /api/opcua/status`

**Response `200`**

```json
{
  "authenticated": true,
  "connected": true,
  "url": "opc.tcp://192.168.1.1:4840",
  "user": "boschrexroth",
  "buffer_size": 1250,
  "has_latest": true,
  "has_identification": true,
  "last_error": null,
  "last_login_ts": 1754212800.42,
  "program_name": "PLC_PRG",
  "mapping": {
    "time": "rTimeSec",
    "actuator": "rActuator",
    "sensor": "rSensor",
    "setpoint": "rSetPoint",
    "signal_type": "uiSignalType"
  }
}
```

> **`authenticated` y `connected` no son lo mismo.** `authenticated` significa que hay
> credenciales cargadas; `connected` que el hilo de lectura está vivo *ahora mismo*.
> Si se cae la red, `authenticated` sigue en `true` mientras el lector reintenta con
> backoff exponencial (1 s, 2 s, 4 s... hasta 30 s).

---

### `GET /api/identification/latest`

Último resultado de identificación. **Es el mismo payload** que el WebSocket empuja
en `{"type": "identification_result"}` — existe para poder inspeccionarlo desde
Swagger o Postman sin abrir un WebSocket.

Ver [Payload de identificación](#payload-de-identificación).

**Error `404`**

```json
{
  "detail": "Aún no hay identificación. Se genera automáticamente cuando el actuador da un escalón y hay suficientes muestras posteriores."
}
```

---

### `GET /health`

**Response `200`** — responde 200 aunque no haya sesión: el servicio está arriba,
simplemente sin datos.

```json
{
  "status": "ok",
  "buffer_size": 0,
  "has_latest": false,
  "has_identification": false,
  "opcua_authenticated": false,
  "opcua_url": null,
  "opcua_user": null,
  "use_percent": false
}
```

---

## Protocolo WebSocket

**Endpoint:** `ws://<host>:8000/ws`

Sin autenticación propia: la sesión es la que abrió `POST /api/opcua/login`. Si no hay
sesión, el WebSocket conecta igual pero no llega ningún `sample`.

Todos los mensajes son JSON con un campo `type`.

### Al conectar

El servidor envía **sin que se lo pidan**, si existen:

1. `latest` — la última muestra en buffer
2. `identification_result` — el último resultado de identificación

Si el buffer está vacío y no hubo identificación, no envía nada y se queda esperando.

---

### Cliente → servidor

| `type` | Respuesta | Para qué |
|---|---|---|
| `ping` | `pong` | Keepalive |
| `get_latest` | `latest` | Pedir la última muestra |
| `get_series` | `series` | Pedir el buffer completo |
| `get_latest_identification` | `identification_result` | Pedir el último resultado |
| `clear_buffer` | `buffer_cleared` | Vaciar el buffer |

Un `type` desconocido devuelve `{"type": "error", "message": "Mensaje no soportado: ..."}`.

```json
{ "type": "ping" }
```

---

### Servidor → cliente

#### `sample` — muestra en tiempo real

Se emite en **broadcast** a cada lectura del PLC (~5 Hz). Es el mensaje que más llega.

```json
{
  "type": "sample",
  "data": {
    "timestamp": 1754212800.123,
    "mapping": {
      "time": "rTimeSec",
      "actuator": "rActuator",
      "sensor": "rSensor",
      "setpoint": "rSetPoint",
      "signal_type": "uiSignalType"
    },
    "time": 5.99,
    "actuator": 12.0,
    "sensor": 7.21,
    "setpoint": 12.0,
    "signal_type": 1,
    "raw": {
      "rTimeSec": 5.99,
      "rActuator": 12.0,
      "rSensor": 7.21,
      "rSetPoint": 12.0,
      "uiSignalType": 1
    },
    "actuator_pct": 50.0,
    "sensor_pct": 20.0625,
    "setpoint_pct": 50.0
  }
}
```

| Campo | Notas |
|---|---|
| `timestamp` | Epoch UNIX del **servidor**, no del PLC |
| `mapping` | Mapeo efectivo usado para esta muestra. Cambia si se llama a `/api/opcua/mapping` |
| `time` | Tiempo del **proceso**, el que expone el PLC |
| `actuator` / `sensor` / `setpoint` | Valores crudos. `null` si la variable no se pudo resolver |
| `signal_type` | Si vale `1`, los `_pct` están convertidos de mA |
| `raw` | **Todas** las variables del programa, con sus nombres reales |
| `*_pct` | Escala 0-100 % |

> `raw` trae el programa entero, no solo las cinco mapeadas. Sirve para mostrar
> cualquier variable en la UI sin pedirla aparte.

---

#### `latest` — última muestra a petición

Respuesta a `get_latest`. Mismo objeto que `sample.data`, o `null` si el buffer
está vacío.

```json
{ "type": "latest", "data": { "...": "igual que sample.data" } }
```

---

#### `series` — buffer completo

Respuesta a `get_series`. Arrays paralelos, todos de longitud `count`.

```json
{
  "type": "series",
  "data": {
    "time": [0.0, 0.2, 0.4],
    "actuator": [4.0, 4.0, 12.0],
    "sensor": [8.0, 8.0, 8.05],
    "setpoint": [12.0, 12.0, 12.0],
    "signal_type": 1,
    "count": 3,
    "unit": "raw"
  }
}
```

| Campo | Notas |
|---|---|
| `unit` | `"raw"` o `"%"`. Por WebSocket siempre llega `"raw"` |
| `signal_type` | El de la **última** muestra |
| Elementos `null` | Aparecen si esa muestra no pudo resolver la variable |

> El buffer es circular, de **5000 muestras**. A 5 Hz son unos 16 minutos; pasado eso
> las viejas se descartan.

---

#### `identification_result` — modelos y PID

Se emite en **broadcast** automáticamente al detectar un escalón, y también como
respuesta a `get_latest_identification` (en ese caso `data` puede ser `null`).

Ver [Payload de identificación](#payload-de-identificación).

---

#### `pong`, `buffer_cleared`, `error`

```json
{ "type": "pong" }
{ "type": "buffer_cleared" }
{ "type": "error", "message": "Mensaje no soportado: foo" }
```

---

## Payload de identificación

Idéntico en `GET /api/identification/latest` y en el mensaje WebSocket
`identification_result`. Ejemplo real, recortado en los arrays largos:

```json
{
  "step_index": 15,
  "winner": "sopdt",
  "window": {
    "time": [0.0, 0.5, 1.0, 1.5],
    "actuator": [4.0, 4.0, 4.0, 4.0],
    "sensor": [8.06, 8.02, 7.98, 8.04],
    "setpoint": [12.0, 12.0, 12.0, 12.0],
    "count": 65
  },
  "models": [
    {
      "model_type": "sopdt",
      "gain": 2.4107,
      "dead_time": 0.5,
      "fit_quality": 0.998,
      "tf_string": "2.4107 * exp(-0.5000s) / ((7.8608s + 1)(3.4047s + 1))",
      "numerator": [2.4107],
      "denominator": [26.7638, 11.2655, 1.0],
      "simulated": [8.056, 8.056, 8.056, 8.056],
      "tau1": 7.8608,
      "tau2": 3.4047,
      "pid_tunings": [
        {
          "method": "IMC (SOPDT eq.)",
          "kp": 0.8637,
          "ki": 0.0903,
          "kd": 0.0,
          "ti": 9.5631,
          "td": 0.0,
          "lambda": 2.3908,
          "description": "IMC / Lambda — Robusto, recomendado para procesos lentos"
        },
        {
          "method": "Ziegler-Nichols (SOPDT eq.)",
          "kp": 2.1615,
          "ki": 0.4907,
          "kd": 2.3802,
          "ti": 4.4047,
          "td": 1.1012,
          "lambda": null,
          "description": "ZN lazo abierto — Agresivo, buena velocidad de respuesta"
        }
      ]
    }
  ]
}
```

### Nivel raíz

| Campo | Notas |
|---|---|
| `step_index` | Índice del escalón dentro de la **serie completa**, no de la ventana |
| `winner` | `model_type` del de mejor `fit_quality`. Siempre es `models[0]` |
| `window` | Tramo usado para identificar |
| `models` | Ordenados de mejor a peor R². Puede traer 1, 2 o 3 elementos |

### `window`

Es un **recorte alrededor del escalón**, no el buffer completo: 10 muestras antes y
40 después, por defecto.

> **Para graficar medido contra simulado hay que usar `window.sensor`, no el buffer.**
> `simulated` tiene exactamente `window.count` elementos; el buffer tiene otra
> longitud y no cuadraría.

El `window.time` viene **re-basado a 0** en el inicio de la ventana. Los tiempos no
coinciden con los de `sample.time`.

### Cada modelo

| Campo | Presente en | Notas |
|---|---|---|
| `model_type` | todos | `fopdt`, `sopdt` o `integrating` |
| `gain` | todos | Ganancia K, en las unidades de la escala activa |
| `dead_time` | todos | Tiempo muerto L, en segundos |
| `fit_quality` | todos | R² en escala **0-1**. Ver aviso abajo |
| `tf_string` | todos | Función de transferencia legible |
| `numerator` / `denominator` | todos | Coeficientes de mayor a menor grado |
| `simulated` | todos | Respuesta del modelo sobre la ventana |
| `pid_tunings` | todos | 4 métodos para FOPDT/SOPDT, 2 para integrador |
| `tau` | solo `fopdt` | Constante de tiempo |
| `tau1`, `tau2` | solo `sopdt` | `tau1` es siempre la dominante (`tau1 >= tau2`) |

> **`fit_quality` puede ser negativo.** Un R² por debajo de 0 significa que el modelo
> ajusta peor que una línea recta en la media — típico del modelo integrador aplicado
> a un proceso autorregulado. No se recorta a 0 a propósito: el signo es información
> útil para el ranking. Si lo muestras en la UI, no asumas que está entre 0 y 1.

### Cada sintonía PID

Se entregan **las dos formas** del PID:

- **Estándar** (la del bloque PID de ctrlX): `kp`, `ti`, `td`
- **Paralela**: `kp`, `ki`, `kd`

con `ki = kp/ti` y `kd = kp*td`. Son el mismo controlador escrito distinto.

| Método | Modelos | Carácter |
|---|---|---|
| `IMC` | FOPDT, SOPDT | Robusto, para procesos lentos |
| `Ziegler-Nichols` | FOPDT, SOPDT | Agresivo, respuesta rápida |
| `Cohen-Coon` | FOPDT, SOPDT | Balance velocidad/estabilidad |
| `SIMC` | FOPDT, SOPDT | Buen rechazo de perturbaciones |
| `SIMC-Integrating` | integrador | Por defecto para procesos de nivel |
| `IMC-Integrating` | integrador | Más conservador, menos sobreimpulso |

En un modelo SOPDT los métodos llevan el sufijo **`(SOPDT eq.)`**: se calculan sobre
un equivalente de primer orden usando la media-regla de Skogestad.

`lambda` solo viene en IMC y SIMC; en los demás es `null`.

---

## Notas de integración

**La identificación es automática.** No hay endpoint para dispararla. El backend la
lanza solo cuando se cumplen todas estas condiciones:

1. Hay al menos 40 muestras en el buffer.
2. Se detecta un escalón de subida en el actuador de al menos **1.0** unidad.
3. Hay al menos 30 muestras después del escalón.
4. El escalón está a 20 o más muestras del último ya procesado.

Si el proceso no da escalones, nunca llega un `identification_result`. Para provocar
uno hay que mover el actuador desde el PLC.

**El umbral de 1.0 está en unidades de la señal.** Si mapeas una variable en una
escala distinta a 4-20 mA (por ejemplo 0-1), un escalón real puede quedar por debajo
del umbral y no dispararse nunca. Está en `main.py`, en `StepDetectorService(min_step_delta=1.0)`.

**`web/static/js/api.js` es código muerto.** Llama a `/generate_sample`, `/identify` e
`/identify_order`, que **no existen** en el backend. Son de una versión anterior con
identificación manual. No lo tomes como referencia.

**Ojo con el orden de los scripts en `index.html`.** `api_plc.js` define las funciones
que usan `charts.js` y `pid.js`.

---

## Tests

```bash
python -m pytest tests/ -q
```

| Archivo | Qué cubre |
|---|---|
| `tests/unit/test_signal_processor.py` | Conversiones mA/%, detección de escalón, validaciones |
| `tests/unit/test_fopdt_identifier.py` | Método de dos puntos, calidad de ajuste |
| `tests/unit/test_sopdt_identifier.py` | Refinamiento numérico, polos repetidos |
| `tests/unit/test_integrating_identifier.py` | Pendiente y tiempo muerto por tangente |
| `tests/unit/test_controller_tuner.py` | Fórmulas de los 4 métodos, coherencia Ki/Kd |
| `tests/unit/test_node_repository.py` | Sugerencia y resolución del mapeo |
| `tests/unit/test_plc_reader_mapping.py` | Lectura con nombres personalizados |
| `tests/integration/test_api_identification.py` | Pipeline completo y serialización |

Los fixtures de `tests/conftest.py` generan procesos sintéticos con parámetros
conocidos, así que los tests verifican que se recuperan los valores reales, no solo
que el código no reviente.
