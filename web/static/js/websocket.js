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

/**
 * Se llama al cargar la app (desde main.js).
 * Asegura que el WebSocket está abierto SIN arrancar un ensayo.
 * Sirve para que los dropdowns del paso 1 se llenen con las variables
 * que reporta el backend.
 */
function ensureWebSocket() {
  if (State.ws.connection && State.ws.connection.readyState === WebSocket.OPEN) return;
  State.ws.started = true;
  connectWebSocket();
}


/**
 * Botón "Inicio" (paso 3): arranca un nuevo ensayo.
 *
 * MODO ACTUAL (temporal, mientras validamos el flujo del ensayo):
 * El chart se llena con el ESCALÓN IDEAL del paso 2 (no con los valores
 * reales del PLC). Cada tick se calcula el valor teórico:
 *   - t <  delay_s → step_from  (línea plana inicial)
 *   - t >= delay_s → step_to    (línea plana final)
 * y se anexa al sampleStore. Así el paso 3 muestra visualmente cómo
 * quedaría la señal si el PLC ejecutara exactamente el escalón configurado.
 *
 * Cuando cableemos los datos reales del PLC, aquí cambiaremos el tick
 * para que empuje `valueForRole(data, "actuator")` desde handleSample
 * en vez de los valores sintéticos.
 */
function startCapture() {
  const step = State.test?.step || {};

  const duration = Number(step.duration_s) || 120;
  const delay    = Number(step.delay_s)    || 10;
  const stepFrom = Number(step.step_from);
  const stepTo   = Number(step.step_to);

  if (!Number.isFinite(stepFrom) || !Number.isFinite(stepTo)) {
    setStatus(
      "Falta configurar el escalón (paso 2): valor inicial y final del actuador.",
      "error"
    );
    return;
  }

  // Estado del ensayo — guardo también los parámetros del escalón
  // para que el tick los tenga a mano sin volver a leer del DOM.
  State.ensayo.running   = true;
  State.ensayo.startedAt = Date.now();
  State.ensayo.durationS = duration;
  State.ensayo.delayS    = delay;
  State.ensayo.stepFrom  = stepFrom;
  State.ensayo.stepTo    = stepTo;

  // Buffer limpio y timer arrancando
  resetSampleStore();
  setTextareaValues("manualTime", [], 2);
  setTextareaValues("manualAct",  [], 3);
  setTextareaValues("manualSen",  [], 3);

  if (State.ensayo.timerId) clearInterval(State.ensayo.timerId);
  State.ensayo.timerId = setInterval(tickEnsayo, 200);
  tickEnsayo();

  // UI
  document.getElementById("btnStart").style.display   = "none";
  document.getElementById("btnStop").style.display    = "";
  document.getElementById("btnToIdent").style.display = "none";

  const box = document.getElementById("ensayoTimerBox");
  if (box) box.className = "ensayo-timer running";

  plotCapture();
  ensureWebSocket();  // WS sigue abierto para live values y dropdowns

  // El timer flotante ya comunica "ensayo en curso" — no duplicamos con toast.
}


/**
 * Cada 200 ms mientras dure el ensayo:
 *   1. Calcula el valor IDEAL del actuador según el reloj interno
 *      (el chart del actuador es sintético, viene del paso 2).
 *   2. Toma el ÚLTIMO valor REAL del sensor y del setpoint (cacheado
 *      por handleSample en State.latestSample). Esto muestrea la señal
 *      del PLC a 200 ms — sample-and-hold del valor más reciente.
 *   3. Empuja actuador ideal + sensor real + setpoint real al buffer.
 *   4. Actualiza el contador y redibuja el chart.
 *   5. Si el tiempo alcanza durationS, dispara finishEnsayo.
 */
function tickEnsayo() {
  if (!State.ensayo.running) return;

  const elapsed = (Date.now() - State.ensayo.startedAt) / 1000;

  // Valor IDEAL del actuador (sintético, para el chart)
  const ideal = elapsed < State.ensayo.delayS
    ? State.ensayo.stepFrom
    : State.ensayo.stepTo;

  // Últimos valores REALES del PLC (cacheados por handleSample vía mapping)
  const latest = State.latestSample || {};

  const s = State.sampleStore;
  s.time.push(elapsed);
  s.actuator_ideal.push(ideal);                // ← SINTÉTICO — chart cAct
  s.actuator_ma.push(latest.actuatorMa);       // ← REAL del PLC — textarea + identificación
  s.actuator_pct.push(latest.actuatorPct);     // ← REAL del PLC
  s.sensor_ma.push(latest.sensorMa);           // ← REAL del PLC
  s.sensor_pct.push(latest.sensorPct);         // ← REAL del PLC
  s.setpoint_ma.push(latest.setpointMa);       // ← REAL del PLC
  s.setpoint_pct.push(latest.setpointPct);     // ← REAL del PLC

  // Contador
  const counter = document.getElementById("ensayoCounter");
  if (counter) counter.textContent = `${elapsed.toFixed(1)} s`;

  plotCapture();
  fillManualTextareas();

  if (elapsed >= State.ensayo.durationS) finishEnsayo();
}


/**
 * Rellena los textareas de "Ingreso manual de datos" (paso 1) con la data
 * capturada del ensayo. Se actualiza en cada tick.
 * NOTA: estos textareas son solo informativos — el backend NO los usa para
 * identificar. La identificación siempre corre sobre el buffer interno del
 * backend (los samples que llegaron por OPC UA).
 */
function fillManualTextareas() {
  const s    = State.sampleStore;
  const type = getSignalType();
  const actuator = type === "pct" ? s.actuator_pct : s.actuator_ma;
  const sensor   = type === "pct" ? s.sensor_pct   : s.sensor_ma;

  setTextareaValues("manualTime", s.time,    2);
  setTextareaValues("manualAct",  actuator,  3);
  setTextareaValues("manualSen",  sensor,    3);
}


/**
 * Se dispara automáticamente cuando el contador alcanza durationS.
 * Congela el chart, esconde el contador y habilita "Identificar".
 */
function finishEnsayo() {
  State.ensayo.running = false;
  if (State.ensayo.timerId) {
    clearInterval(State.ensayo.timerId);
    State.ensayo.timerId = null;
  }

  // El contador desaparece al finalizar (fade + slide out por CSS).
  const box = document.getElementById("ensayoTimerBox");
  if (box) box.className = "ensayo-timer";

  document.getElementById("btnStop").style.display  = "none";
  document.getElementById("btnStart").style.display = "";
  document.getElementById("btnIdent").style.display = "";

  setStatus(
    `Ensayo completado (${State.ensayo.durationS} s). Presiona "Identificar" para procesar.`,
    "ok"
  );
}


/**
 * Botón "Paro" (paso 3): aborta el ensayo en curso.
 * NO cierra el WebSocket — sigue abierto para los live values y
 * los dropdowns del paso 1.
 */
function stopCapture() {
  State.ensayo.running = false;
  if (State.ensayo.timerId) {
    clearInterval(State.ensayo.timerId);
    State.ensayo.timerId = null;
  }

  document.getElementById("btnStop").style.display  = "none";
  document.getElementById("btnStart").style.display = "";

  const box = document.getElementById("ensayoTimerBox");
  if (box) box.className = "ensayo-timer";

  setStatus("Ensayo detenido por el usuario", "error");
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
    // NO es un "running" — la identificación ya terminó. Es una ADVERTENCIA.
    if (data.truncated) {
      setStatus(
        `Identificado con ${data.window.count} muestras — la duración configurada ` +
          `pedía ${data.requested_post_samples} después del escalón. Revisa que la ` +
          `curva haya llegado al nuevo estable.`,
        "warn"
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
 * Cada muestra que llega hace DOS cosas independientes:
 *   1. Sincronizar mapping y poblar dropdowns del paso 1 (siempre).
 *   2. Actualizar los live values del paso 3 (siempre).
 *
 * NO toca el sampleStore ni redibuja el chart. En esta versión el chart
 * del paso 3 se alimenta 100% del escalón IDEAL configurado en paso 2,
 * generado punto a punto por tickEnsayo(). Los datos reales del PLC solo
 * se usan para los indicadores en vivo (valAct/valSP/valSensor).
 *
 * Cuando cableemos la señal real al chart, aquí se agregará el push al
 * sampleStore condicionado a State.ensayo.running.
 */
function handleSample(data) {
  // 1. Mapping y dropdowns (siempre)
  if (data.mapping && typeof data.mapping === "object") {
    State.mapping = { ...State.mapping, ...data.mapping };
  }
  populateVariableDropdowns(data);

  // 2. Cachear la última muestra completa — la usa tickEnsayo para
  //    llenar el chart del sensor con el valor real más reciente.
  const latest = State.latestSample;
  latest.actuatorMa  = valueForRole(data, "actuator");
  latest.sensorMa    = valueForRole(data, "sensor");
  latest.setpointMa  = valueForRole(data, "setpoint");
  latest.actuatorPct = pickNumber(data.actuator_pct);
  latest.sensorPct   = pickNumber(data.sensor_pct);
  latest.setpointPct = pickNumber(data.setpoint_pct);

  // 3. Live values (siempre)
  updateLiveValues(latest);
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


/**
 * Actualiza SOLO los indicadores en vivo del paso 3 (valAct, valSP, valSensor)
 * usando la muestra recién llegada. No toca el sampleStore ni los charts.
 * Se ejecuta en cada sample, haya o no ensayo en curso.
 */
function updateLiveValues(vals) {
  const type     = getSignalType();
  const actuator = type === "pct" ? vals.actuatorPct : vals.actuatorMa;
  const sensor   = type === "pct" ? vals.sensorPct   : vals.sensorMa;
  const setpoint = type === "pct" ? vals.setpointPct : vals.setpointMa;

  writeLive("valAct",    actuator);
  writeLive("valSP",     setpoint);
  writeLive("valSensor", sensor);
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
    // El chart del paso 3 se redibuja en el próximo Inicio; aquí no forzamos.

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

  setStatus(
    "Identificación lista — mejor ajuste R²: " +
      formatNumber(models[State.identification.active]?.fit_quality, 1) + "%",
    "ok"
  );
}
