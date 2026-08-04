/* =========================================================
   state.js
   Estado global de la aplicación.
   Se carga primero — el resto de módulos leen/escriben aquí.
   ========================================================= */

window.State = {
  // ---------- Endpoints ----------
  APP_PREFIX: window.APP_PREFIX || "",
  API_BASE:   window.API_BASE   || window.location.origin,
  WS_BASE:    window.WS_BASE    || window.location.origin.replace(/^http/, "ws"),

  // ---------- UI ----------
  ui: {
    step: 1,              // paso activo del wizard (1..5)
    pidFormat: "standard" // "standard" (Kp,Ki,Kd) | "parallel" (Kp,Ti,Td)
  },

  // ---------- Chart.js instances ----------
  charts: {
    cAct:       null,   // paso 3 — actuador en tiempo real
    cSen:       null,   // paso 3 — sensor en tiempo real
    cCmp:       null,   // paso 4 — medido vs modelo
    cBodeMag:   null,   // paso 4 — Bode magnitud
    cBodePhase: null    // paso 4 — Bode fase
  },

  // ---------- Mapeo rol -> variable del PLC ----------
  // Lo envía el backend en cada sample (`sample.mapping`) y lo puede
  // cambiar el usuario desde los <select> del paso 1.
  mapping: {
    time:        null,
    actuator:    null,
    sensor:      null,
    setpoint:    null,
    signal_type: null
  },

  // ---------- Ensayo y escalas (GET/POST /api/test) ----------
  // El backend es la fuente de verdad de las conversiones mA/%/V.
  test: {
    scales:    { actuator: "ma", sensor: "ma", setpoint: "ma" },
    step:      null,   // última respuesta de /api/test/config
    available: []      // catálogo de escalas soportadas
  },

  // ---------- Buffer de muestras en tiempo real ----------
  // Alimentado por websocket.js cuando llegan mensajes {type:"sample"}
  sampleStore: {
    time:         [],
    actuator_ma:  [],
    sensor_ma:    [],
    setpoint_ma:  [],
    actuator_pct: [],
    sensor_pct:   [],
    setpoint_pct: [],
    maxPoints:    300
  },

  // ---------- Resultados de identificación ----------
  identification: {
    models: [],     // arreglo normalizado de modelos (FOPDT/SOPDT/Integrating)
    winner: null,   // model_type ("fopdt" | "sopdt" | "integrating")
    active: 0       // índice del modelo seleccionado en el paso 5
  },

  // ---------- WebSocket ----------
  ws: {
    connection:     null,
    started:        false,
    reconnectTimer: null
  },

  // ---------- Ensayo activo (paso 3) ----------
  // Se activa con el botón "Inicio" y controla:
  //   - Timer visible (contador de segundos).
  //   - Aceptación de muestras en el sampleStore (fuera de ensayo se ignoran).
  //   - Auto-parada al alcanzar durationS.
  // `startedAt` es un timestamp (Date.now()); el tiempo relativo es
  // (Date.now() - startedAt) / 1000, así el gráfico empieza en 0 s
  // independientemente de lo que reporte el reloj interno del PLC.
  ensayo: {
    running:   false,
    startedAt: null,
    durationS: null,
    timerId:   null
  },

  // ---------- Última muestra recibida del PLC ----------
  // handleSample actualiza esto en cada mensaje `sample` (siempre, haya
  // o no ensayo). tickEnsayo lo lee para llenar el chart del sensor con
  // el valor real más reciente en cada tick — así se "muestrea" a la
  // misma frecuencia que el actuador sintético.
  latestSample: {
    actuatorMa:  null,
    actuatorPct: null,
    sensorMa:    null,
    sensorPct:   null,
    setpointMa:  null,
    setpointPct: null
  }
};

/** Limpia el buffer de muestras. */
function resetSampleStore() {
  const s = State.sampleStore;
  s.time = [];
  s.actuator_ma = [];
  s.sensor_ma = [];
  s.setpoint_ma = [];
  s.actuator_pct = [];
  s.sensor_pct = [];
  s.setpoint_pct = [];
}
