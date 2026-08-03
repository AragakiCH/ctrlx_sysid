/* =========================================================
   websocket.js
   Cliente WebSocket contra el backend FastAPI (main.py).
   - Se conecta a /ws cuando el usuario pulsa "Inicio" (paso 3).
   - Recibe muestras en tiempo real y las mete en State.sampleStore.
   - Recibe el resultado de identificación cuando el backend
     detecta un escalón y decide identificar.
   - Reconecta cada 3s si se cae la conexión.
   ========================================================= */


/* ==================== ENTRY POINTS (botones del paso 3) ==================== */

/** Botón "Inicio": inicia la sesión y abre el WebSocket. */
function startCapture() {
  if (State.ws.started) return;

  State.ws.started = true;

  // UI feedback
  document.getElementById("btnStart").style.display = "none";
  document.getElementById("btnStop").style.display  = "";
  document.getElementById("btnIdent").style.display = "none";
  document.getElementById("btnToIdent").style.display = "none";
  document.getElementById("progBar").style.width = "0%";

  setStatus("Conectando al PLC vía WebSocket...", "running");

  resetSampleStore();
  clearLiveValues();
  connectWebSocket();
}


/** Botón "Paro": cierra el WebSocket y detiene la captura. */
function stopCapture() {
  State.ws.started = false;
  clearTimeout(State.ws.reconnectTimer);

  if (State.ws.connection) {
    try { State.ws.connection.close(); } catch (_) {}
    State.ws.connection = null;
  }

  document.getElementById("btnStop").style.display  = "none";
  document.getElementById("btnStart").style.display = "";

  setStatus("Captura detenida por el usuario", "error");
  setConnectionStatus(false);
}


/**
 * Botón "Identificar": en este backend la identificación se dispara
 * automáticamente cuando se detecta un escalón. Si ya llegó un
 * resultado, avanzamos al paso 4; si no, mostramos aviso.
 */
function runIdentification() {
  if (State.identification.models.length) {
    goStep(4);
  } else {
    setStatus("Aún no hay resultados. Espera a que el backend detecte un escalón.", "running");
  }
}


/* ==================== CONEXIÓN ==================== */

/** Devuelve la URL absoluta del endpoint WS. */
function getWsUrl() {
  if (window.State?.WS_BASE) return `${window.State.WS_BASE}/ws`;

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const parts    = window.location.pathname.split("/").filter(Boolean);
  const prefix   = parts.length ? `/${parts[0]}` : "";
  return `${protocol}//${window.location.host}${prefix}/ws`;
}


function connectWebSocket() {
  const wsUrl = getWsUrl();
  console.log("Conectando WS a:", wsUrl);

  const ws = new WebSocket(wsUrl);
  State.ws.connection = ws;

  ws.onopen = () => {
    console.log("WebSocket conectado");
    setConnectionStatus(true);
    setStatus("Conectado — recibiendo muestras del PLC", "ok");

    sendWsMessage({ type: "ping" });
    sendWsMessage({ type: "get_latest" });
    sendWsMessage({ type: "get_latest_identification" });
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleWsMessage(msg);
    } catch (err) {
      console.error("Error parseando mensaje WS:", err, event.data);
    }
  };

  ws.onerror = (err) => {
    console.error("Error WebSocket:", err);
    setConnectionStatus(false);
  };

  ws.onclose = () => {
    console.warn("WebSocket cerrado");
    setConnectionStatus(false);
    State.ws.connection = null;

    if (State.ws.started) {
      setStatus("WS desconectado. Reintentando en 3s...", "error");
      clearTimeout(State.ws.reconnectTimer);
      State.ws.reconnectTimer = setTimeout(connectWebSocket, 3000);
    }
  };
}


function sendWsMessage(payload) {
  const ws = State.ws.connection;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn("WS no está abierto:", payload);
    return;
  }
  ws.send(JSON.stringify(payload));
}


/* ==================== DISPATCHER ==================== */

function handleWsMessage(msg) {
  switch (msg?.type) {
    case "pong":
      break;

    case "latest":
      // Snapshot inicial al conectar — lo tratamos como sample suelto
      if (msg.data) handleSample(msg.data);
      break;

    case "sample":
      handleSample(msg.data || {});
      break;

    case "identification_result":
      handleIdentificationResult(msg.data || {});
      break;

    case "error":
      console.error("Error WS:", msg);
      setStatus(msg.message || "Error recibido por WebSocket", "error");
      break;

    default:
      console.log("Tipo WS no manejado:", msg?.type, msg);
  }
}


/* ==================== HANDLERS ==================== */

/**
 * Cada muestra que llega la metemos en el sampleStore y refrescamos la vista.
 * El backend puede mandar la muestra "cruda" (`raw`) o ya derivada.
 */
function handleSample(data) {
  const raw = data.raw || {};

  const timeValue = pickNumber(data.time, raw.rTimeSec, raw.rTiempoSeg, raw.arrTimeSec);
  if (timeValue === null) {
    console.warn("Sample sin tiempo válido:", data);
    return;
  }

  const actuatorMa = pickNumber(data.actuator, raw.rActuator, raw.AO_Actuador_mA, raw.AO_Actuador);
  const sensorMa   = pickNumber(data.sensor,   raw.rSensor,   raw.AI_Sensor_mA,   raw.AI_Sensor);
  const setpointMa = pickNumber(data.setpoint, raw.rSetPoint, raw.SP_mA,          raw.SP);

  const actuatorPct = pickNumber(data.actuator_pct);
  const sensorPct   = pickNumber(data.sensor_pct);
  const setpointPct = pickNumber(data.setpoint_pct);

  const s = State.sampleStore;
  pushSample(s.time,         timeValue);
  pushSample(s.actuator_ma,  actuatorMa);
  pushSample(s.sensor_ma,    sensorMa);
  pushSample(s.setpoint_ma,  setpointMa);
  pushSample(s.actuator_pct, actuatorPct);
  pushSample(s.sensor_pct,   sensorPct);
  pushSample(s.setpoint_pct, setpointPct);

  refreshLiveViews();
}


/** Añade un valor al buffer respetando `maxPoints`. */
function pushSample(arr, value) {
  arr.push(value);
  if (arr.length > State.sampleStore.maxPoints) arr.shift();
}


/** Refresca live values, textareas manuales y charts del paso 3. */
function refreshLiveViews() {
  const s    = State.sampleStore;
  const type = getSignalType();

  const actuator = type === "pct" ? s.actuator_pct : s.actuator_ma;
  const sensor   = type === "pct" ? s.sensor_pct   : s.sensor_ma;
  const setpoint = type === "pct" ? s.setpoint_pct : s.setpoint_ma;

  // Live values (paso 3)
  writeLive("valAct",    actuator[actuator.length - 1]);
  writeLive("valSP",     setpoint[setpoint.length - 1]);
  writeLive("valSensor", sensor[sensor.length - 1]);

  // Textareas manuales (paso 1) — se van llenando en tiempo real
  setTextareaValues("manualTime", s.time,       3);
  setTextareaValues("manualAct",  actuator, 3);
  setTextareaValues("manualSen",  sensor,   3);

  // Charts en tiempo real (paso 3)
  plotCapture(s.time.length);

  // Barra de progreso — se rellena hasta `maxPoints`
  const pct = Math.min(100, (s.time.length / s.maxPoints) * 100).toFixed(0);
  const bar = document.getElementById("progBar");
  const lbl = document.getElementById("progLabel");
  if (bar) bar.style.width  = pct + "%";
  if (lbl) lbl.textContent  = pct + "%  |  " + s.time.length + " muestras";
}


function writeLive(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(2)
    : "--";
}

function clearLiveValues() {
  ["valAct", "valSP", "valSensor"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.textContent = "--";
  });
}


/**
 * El backend detectó un escalón y corrió la identificación.
 * Rellena los paneles 4 y 5.
 */
function handleIdentificationResult(data) {
  const rawModels = Array.isArray(data.models) ? data.models : [];
  if (!rawModels.length) {
    console.warn("No llegaron modelos de identificación");
    return;
  }

  const models   = rawModels.map(normalizeModelResult);
  const winner   = data.winner || models[0]?.model_type || null;

  // Guardar en estado
  State.identification.models = models;
  State.identification.winner = winner;
  State.identification.active = models.findIndex((m) => m.model_type === winner);
  if (State.identification.active < 0) State.identification.active = 0;

  // Render
  const s = State.sampleStore;
  const type = getSignalType();
  const measured = type === "pct" ? s.sensor_pct : s.sensor_ma;

  renderIdent(models, measured, s.time, winner);
  renderBode(models[State.identification.active]);
  renderPID(models, State.identification.active);

  // UI: habilitar botones que llevan al paso 4/5
  document.getElementById("btnIdent").style.display   = "";
  document.getElementById("btnToIdent").style.display = "";
  document.getElementById("btnExport").style.display  = "";

  setStatus(
    "Identificación lista — mejor ajuste R²: " +
      formatNumber(models[State.identification.active]?.fit_quality, 1) + "%",
    "ok"
  );
}
