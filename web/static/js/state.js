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

  // True mientras un cambio de mapeo viaja al backend. Sirve para que la
  // re-sincronización de los <select> no los devuelva al valor anterior
  // durante ese intervalo.
  mappingPending: false,

  // ---------- Ensayo y escalas (GET/POST /api/test) ----------
  // El backend es la fuente de verdad de las conversiones mA/%/V.
  test: {
    scales:    { actuator: "ma", sensor: "ma", setpoint: "ma" },
    step:      null,   // última respuesta de /api/test/config
    available: []      // catálogo de escalas soportadas
  },

  // ---------- Buffer de muestras del ensayo ----------
  // Lo llena `onTestTick` (websocket.js) con los mensajes {type:"test_tick"}
  // que emite el backend, al periodo de muestreo configurado.
  //
  //   actuator_*      → lo que el PLC REPORTA en la variable mapeada al rol.
  //   actuator_cmd_*  → lo que el backend COMANDA según el perfil del ensayo.
  //   sensor_* / setpoint_* → valores REALES del PLC.
  //
  // Comandado y leído son cosas distintas y van separados: compararlos es lo
  // que dice si el actuador obedeció, saturó o ni se enteró. La identificación
  // corre sobre el LEÍDO, así que es ese el que se grafica y el que va a los
  // textareas del paso 1.
  //
  // El sufijo `_ma` es histórico: guarda el valor en la escala que el usuario
  // declaró para ese rol (4-20 mA, 0-100 % o 0-10 V). La unidad real viene en
  // `State.ensayo.plan.unit`.
  //
  // No hay tope de puntos: el ensayo tiene largo acotado
  // (duration_s / sample_period_s), así que el buffer no crece sin fin.
  sampleStore: {
    time:            [],
    actuator_ma:     [],
    sensor_ma:       [],
    setpoint_ma:     [],
    actuator_pct:    [],
    sensor_pct:      [],
    setpoint_pct:    [],
    actuator_cmd_ma:  [],
    actuator_cmd_pct: []
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
  // EL RELOJ VIVE EN EL BACKEND. Aquí solo se refleja lo que llega por
  // WebSocket (test_started / test_tick / test_finished / test_stopped).
  //
  // Antes esto lo generaba un setInterval del navegador. Se movió al backend
  // porque Chrome estrangula los timers de las pestañas en segundo plano
  // (los baja a 1 Hz o menos): el escalón se aplicaría tarde, o nunca. Y
  // cuando el backend escriba en el PLC, tiene que ser el mismo reloj.
  //
  // `plan` es el perfil completo del actuador que manda `test_started`:
  // permite dibujar la línea objetivo entera desde el primer instante, en
  // vez de irla descubriendo punto a punto.
  ensayo: {
    running:   false,
    durationS: null,
    elapsedS:  0,
    plan:      null,
    phase:     null   // "baseline" (antes del salto) | "step" (después)
  },

  // ---------- Última muestra recibida del PLC ----------
  // handleSample actualiza esto en cada mensaje `sample` (siempre, haya
  // o no ensayo). onTestTick lo lee para llenar el chart del sensor con
  // el valor real más reciente en cada tick — sample-and-hold: la señal
  // del PLC se muestrea al ritmo que marca el ensayo del backend.
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
  // Se recorren las claves en vez de listarlas a mano: añadir un arreglo nuevo
  // y olvidar limpiarlo aquí deja datos del ensayo anterior mezclados.
  Object.keys(s).forEach((k) => {
    if (Array.isArray(s[k])) s[k] = [];
  });
}
