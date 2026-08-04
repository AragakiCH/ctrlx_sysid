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
  document.getElementById("btnToIdent").style.display = "none";

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
 * Botón "Identificar": fuerza el recálculo sobre el buffer actual con las
 * condiciones de ensayo vigentes.
 *
 * La identificación automática corre una sola vez por escalón. Si después se
 * cambia el orden del modelo o la escala de alguna señal, hay que pedirle al
 * backend que recalcule; no hace falta repetir el ensayo en el PLC.
 */
async function runIdentification(goToStep = true) {
  setStatus("Identificando con el buffer actual...", "running");

  try {
    const data = await fetch(`${State.API_BASE}/api/identification/run`, {
      method: "POST"
    }).then(async (r) => {
      const body = await r.json().catch(() => ({}));

      if (r.status === 404) {
        // Este archivo se lee del disco en cada request, pero las rutas de
        // Python se registran al arrancar: el servidor viene de antes.
        throw new Error(
          "El backend no expone /api/identification/run todavía. Reinicia el servidor."
        );
      }

      if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
      return body;
    });

    handleIdentificationResult(data);

    // El backend avisa si tuvo que ajustar con menos respuesta de la prevista.
    if (data.truncated) {
      setStatus(
        `Identificado con ${data.window.count} muestras — la duración configurada ` +
          `pedía ${data.requested_post_samples} después del escalón. Revisa que la ` +
          `curva haya llegado al nuevo estable.`,
        "running"
      );
    }

    if (goToStep) goStep(4);
    return data;
  } catch (err) {
    console.warn("No se pudo identificar:", err.message);

    // Si ya había un resultado previo, al menos dejamos ver ese.
    if (State.identification.models.length && goToStep) {
      setStatus(`${err.message} — se muestra el último resultado.`, "error");
      goStep(4);
    } else {
      setStatus(err.message, "error");
    }
    return null;
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
  // El mapeo efectivo lo manda el backend en cada sample.
  if (data.mapping && typeof data.mapping === "object") {
    State.mapping = { ...State.mapping, ...data.mapping };
  }

  // Los dropdowns de variables (paso 1) se llenan con las llaves de `raw`.
  populateVariableDropdowns(data);

  const timeValue = valueForRole(data, "time");
  if (timeValue === null) {
    console.warn("Sample sin tiempo válido:", data);
    return;
  }

  const actuatorMa = valueForRole(data, "actuator");
  const sensorMa   = valueForRole(data, "sensor");
  const setpointMa = valueForRole(data, "setpoint");

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


/**
 * Resuelve el valor de un rol sin nombres hardcodeados:
 *   1. El campo ya normalizado por el backend (`data.actuator`, ...).
 *   2. La variable cruda que el mapeo vigente asigna a ese rol.
 * Así, si el usuario cambia el <select> a otra variable, se lee esa.
 */
function valueForRole(data, role) {
  const direct = pickNumber(data[role]);
  if (direct !== null) return direct;

  const mapping = data.mapping || State.mapping || {};
  const variableName = mapping[role];
  const raw = data.raw || {};

  if (variableName && variableName in raw) {
    return pickNumber(raw[variableName]);
  }

  return null;
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

  // (Barra de progreso del buffer eliminada — el CSS .prog-row/.prog-wrap/.prog-bar
  //  sigue en app.css por si queremos traerla de vuelta.)
}


/* ==================== POBLACIÓN DE DROPDOWNS DE VARIABLES ==================== */

/**
 * Al recibir el primer sample con `raw`, llena los <select> de variables
 * del paso 1 con todas las llaves del programa. Pre-selecciona cada
 * dropdown con lo que el backend ya tiene mapeado (`sample.mapping`).
 *
 * Se ejecuta una sola vez: en cuanto los selects tienen opciones reales,
 * ya no se toca (así no pisa cambios manuales del usuario).
 */
function populateVariableDropdowns(sample) {
  const raw = sample?.raw;
  if (!raw) return;

  const keys = Object.keys(raw);
  if (!keys.length) return;

  // ¿Ya se llenó? El placeholder inicial tiene value="", los reales no.
  const first = document.getElementById("varAct");
  if (!first || (first.options.length > 0 && first.options[0].value !== "")) return;

  const mapping = sample.mapping || {};

  const targets = [
    { id: "varAct", role: "actuator" },
    { id: "varSen", role: "sensor" },
    { id: "varSP",  role: "setpoint" }
  ];

  targets.forEach(({ id, role }) => {
    const sel = document.getElementById(id);
    if (!sel) return;

    // Para setpoint dejamos una opción "sin asignar" (es opcional)
    const opts = role === "setpoint" ? [`<option value="">— sin asignar —</option>`] : [];
    keys.forEach((k) => {
      opts.push(`<option value="${escapeHtml(k)}">${escapeHtml(k)}</option>`);
    });
    sel.innerHTML = opts.join("");

    const current = mapping[role];
    if (current && keys.includes(current)) sel.value = current;
  });
}


/* ==================== CAMBIO DE MAPEO EN CALIENTE ==================== */

/**
 * Lee los <select> del paso 1 y manda el nuevo mapeo al backend
 * (POST /api/opcua/mapping). El backend reasigna qué variable lee para
 * cada rol y limpia su buffer, así que aquí también limpiamos el nuestro:
 * las muestras viejas se tomaron con otro mapeo.
 */
async function applyMappingChange() {
  const mapping = {
    // time y signal_type no son editables en la UI: se conserva
    // lo que el backend ya resolvió (alias por defecto o login).
    time:        State.mapping.time        || null,
    signal_type: State.mapping.signal_type || null,
    actuator:    document.getElementById("varAct")?.value || null,
    sensor:      document.getElementById("varSen")?.value || null,
    setpoint:    document.getElementById("varSP")?.value  || null
  };

  setStatus("Aplicando nuevo mapeo de variables...", "running");

  try {
    const response = await fetch(`${State.API_BASE}/api/opcua/mapping`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ mapping })
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(data.detail || `HTTP ${response.status}`);
    }

    State.mapping = { ...State.mapping, ...(data.mapping || mapping) };

    try {
      localStorage.setItem("plcMapping", JSON.stringify(State.mapping));
    } catch (_) {}

    // El buffer viejo ya no es válido: se capturó con otras variables.
    resetSampleStore();
    clearLiveValues();
    setTextareaValues("manualTime", [], 3);
    setTextareaValues("manualAct",  [], 3);
    setTextareaValues("manualSen",  [], 3);
    refreshLiveViews();

    setStatus(
      `Mapeo actualizado — u: ${mapping.actuator || "—"} · ` +
        `y: ${mapping.sensor || "—"} · SP: ${mapping.setpoint || "—"}`,
      "ok"
    );
  } catch (err) {
    console.error("Error actualizando mapeo:", err);
    setStatus(`No se pudo actualizar el mapeo: ${err.message}`, "error");
  }
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
  // (btnIdent ya está siempre visible: es el disparador manual)
  document.getElementById("btnToIdent").style.display = "";
  document.getElementById("btnExport").style.display  = "";

  setStatus(
    "Identificación lista — mejor ajuste R²: " +
      formatNumber(models[State.identification.active]?.fit_quality, 1) + "%",
    "ok"
  );
}
