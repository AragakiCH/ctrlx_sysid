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
- [Ejecución del ensayo](#ejecución-del-ensayo)
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
  },
  "sampling": {
    "mode": "subscription",
    "requested_period_s": 0.02,
    "revised_period_s": 0.02,
    "honored": true,
    "reason": null
  }
}
```

> **`authenticated` y `connected` no son lo mismo.** `authenticated` significa que hay
> credenciales cargadas; `connected` que el hilo de lectura está vivo *ahora mismo*.
> Si se cae la red, `authenticated` sigue en `true` mientras el lector reintenta con
> backoff exponencial (1 s, 2 s, 4 s... hasta 30 s).

#### El bloque `sampling`

El "Tiempo de muestreo" de la vista es una **petición**, no un hecho. Este bloque
dice qué está pasando de verdad.

| Campo | Significado |
|---|---|
| `mode` | `subscription` o `polling`. `null` si no hay sesión. |
| `requested_period_s` | Lo que se pidió desde la vista. |
| `revised_period_s` | Lo real. Por suscripción, lo que **concedió** el servidor; por polling, el intervalo **medido** entre las últimas muestras. |
| `honored` | `true` si lo real está dentro del 25 % de lo pedido. |
| `reason` | Por qué se cayó a polling, si aplica. |

**Por qué existen dos modos.** Con *polling* cada muestra cuesta un viaje completo
de ida y vuelta, así que el periodo nunca puede bajar de la latencia de red: pedir
20 ms sobre un enlace de 64 ms da 64 ms, sin que nada falle ni avise. Con una
*suscripción* el reparto se invierte — el ctrlX muestrea cada `SamplingInterval`
(puede bajar al ciclo de tarea del PLC), acumula en una cola y envía el lote entero
cada `PublishingInterval` — así que el ritmo lo marca el PLC y no la red. Ahí sí se
llega a 10-20-30 ms.

El lector **siempre intenta la suscripción primero** y cae a polling si el servidor
no la acepta, sin interrumpir el trabajo. Cambiar el tiempo de muestreo con una
suscripción abierta la **reabre** con el nuevo intervalo: a diferencia del polling,
el intervalo se negocia al abrirla y no se puede cambiar en caliente.

El servidor OPC UA **no está obligado** a conceder el intervalo pedido. Cuando no
lo hace, la app lo dice en la vista y deja la decisión al usuario en vez de
corregirlo por su cuenta: subir el tiempo de muestreo a lo concedido, o seguir
sabiendo que la curva tendrá menos puntos de los previstos.

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

#### `test_started` — arranca el ensayo

Se emite al llamar a `POST /api/test/start`. Trae el **perfil completo** del
actuador, para que la vista pinte la línea objetivo de una sola vez en lugar de
irla dibujando punto a punto.

```json
{
  "type": "test_started",
  "data": {
    "status": "running",
    "running": true,
    "elapsed_s": 0.0,
    "duration_s": 120.0,
    "index": 0,
    "total": 600,
    "progress": 0.0017,
    "actuator_cmd": 8.0,
    "actuator_cmd_pct": 25.0,
    "phase": "baseline",
    "started_at": 1754212800.42,
    "writes_enabled": false,
    "write_errors": 0,
    "last_write_error": null,
    "plan": {
      "time": [0.0, 0.2, 0.4],
      "actuator": [8.0, 8.0, 8.0],
      "actuator_pct": [25.0, 25.0, 25.0],
      "unit": "mA",
      "sample_period_s": 0.2,
      "duration_s": 120.0,
      "step_at_s": 10.0,
      "from_value": 8.0,
      "to_value": 12.0,
      "from_value_pct": 25.0,
      "to_value_pct": 50.0,
      "samples": 600
    }
  }
}
```

> `plan` tiene **una entrada por muestra** del ensayo, al mismo periodo con el
> que se ejecuta. No confundir con `GET /api/test/preview`, que reparte N puntos
> repartidos solo para dibujar.

---

#### `test_tick` — avance del ensayo

Uno por muestra, al periodo configurado. Mismos campos que `test_started` pero
sin `plan`, más `written`.

```json
{
  "type": "test_tick",
  "data": {
    "status": "running",
    "running": true,
    "elapsed_s": 12.4,
    "index": 62,
    "total": 600,
    "progress": 0.105,
    "actuator_cmd": 12.0,
    "actuator_cmd_pct": 50.0,
    "phase": "step",
    "written": false,
    "write_errors": 0
  }
}
```

| Campo | Notas |
|---|---|
| `phase` | `baseline` antes del salto, `step` después |
| `actuator_cmd` | Valor comandado, en la escala del actuador |
| `written` | `true` si se escribió en el PLC. Hoy siempre `false` |
| `elapsed_s` | Reloj **monótono** del backend, inmune a cambios de hora |

---

#### `test_finished` / `test_stopped`

`test_finished` al completar la duración; `test_stopped` si se corta con
`POST /api/test/stop`. Mismos campos, con `status` en `finished` o `stopped` y
`running: false`.

---

#### `test_state` — respuesta a `get_test_state`

Se envía también **al conectar**, si hay un ensayo en curso, para que una pestaña
recién abierta se enganche sin esperar al siguiente tick. Incluye `plan` solo
cuando el ensayo está corriendo.

---

#### `pong`, `buffer_cleared`, `error`

```json
{ "type": "pong" }
{ "type": "buffer_cleared" }
{ "type": "error", "message": "Mensaje no soportado: foo" }
```

---

## Ejecución del ensayo

El escalón lo genera el **backend**, no el navegador.

### Por qué el reloj vive en el backend

Antes el perfil se calculaba en el navegador con un `setInterval` de 200 ms. Eso
sirve para dibujar, pero no para gobernar un proceso:

- Los navegadores **estrangulan los timers** de las pestañas en segundo plano
  (Chrome los baja a 1 Hz o menos). El escalón se aplicaría tarde, o nunca.
- Si el operador cierra la pestaña, el ensayo queda a medias y el actuador se
  queda en el último valor comandado.
- Con varias pestañas abiertas habría varios relojes compitiendo.

Teniendo el reloj en el backend el ensayo es uno solo, sobrevive a que se cierre
el navegador, y es el mismo hilo que va a escribir en el PLC.

### Ciclo

```
POST /api/test/config     -> guarda el escalón (paso 2 de la vista)
POST /api/test/start      -> arranca; limpia el buffer por defecto
   WS: test_started       -> plan completo
   WS: test_tick  x N     -> uno por muestra
   WS: test_finished      -> al completar duration_s
POST /api/identification/run  -> ajusta los modelos sobre lo capturado
```

`POST /api/test/stop` corta antes de tiempo y emite `test_stopped`. Las muestras
capturadas **se conservan**: si alcanzan, se puede identificar igual.

### El comando en las muestras

Mientras hay un ensayo corriendo, cada `sample` se etiqueta con lo que el
backend estaba comandando en ese instante:

| Campo | Qué es |
|---|---|
| `actuator` | Lo que el PLC **reporta**. No se toca |
| `actuator_cmd` | Lo que el backend **pidió** |
| `actuator_cmd_pct` | Lo mismo en % de span |
| `test_phase` | `baseline` o `step` |
| `test_elapsed_s` | Segundos desde el arranque del ensayo |

Van en campos aparte a propósito: comparar `actuator` contra `actuator_cmd` es lo
que permite detectar que el actuador saturó, llegó tarde o directamente no
obedeció. Fuera de un ensayo estos campos **no aparecen** en la muestra.

### Escritura al PLC

**Todavía no se escribe nada.** El operador sigue aplicando el escalón desde el
ctrlX; el backend solo calcula y publica el valor.

El punto de extensión es `TestRunnerService.set_writer()`: recibe una función que
se llama en cada tick con el valor a aplicar, en la escala del actuador. Cuando
esté conectada, `writes_enabled` pasa a `true` y cada `test_tick` reportará
`written`. Un fallo de escritura no aborta el ensayo: se cuenta en
`write_errors` y se sigue, para que un error puntual de red no deje el actuador
a medio camino.

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
