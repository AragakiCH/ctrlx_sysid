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
 * El reloj lo lleva el BACKEND. Aquí solo se dispara y se espera:
 *
 *   POST /api/test/start
 *      -> WS test_started   : perfil completo del actuador (línea objetivo)
 *      -> WS test_tick × N  : una por muestra, al periodo configurado
 *      -> WS test_finished  : al completar duration_s
 *
 * Antes esto lo generaba un `setInterval` de 200 ms en el navegador. Se movió
 * porque Chrome estrangula los timers de las pestañas en segundo plano y el
 * escalón se aplicaría tarde o nunca — y porque cuando el backend escriba en
 * el PLC tiene que ser ese mismo reloj el que mande.
 */
async function startCapture() {
  // El WS tiene que estar abierto ANTES del start: si no, se pierden los
  // primeros ticks (y el test_started con el plan).
  ensureWebSocket();

  setStatus("Arrancando ensayo...", "running");

  try {
    const response = await fetch(`${State.API_BASE}/api/test/start`, {
      method: "POST"
    });

    const data = await response.json().catch(() => ({}));

    if (response.status === 404) {
      throw new Error(
        "El backend no expone /api/test/start todavía. Reinicia el servidor."
      );
    }

    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

    // No se toca el estado aquí: lo hace onTestStarted cuando llegue el
    // evento. Así la vista refleja lo que el backend realmente arrancó y no
    // lo que creemos haber pedido.
    return data;
  } catch (err) {
    console.error("No se pudo arrancar el ensayo:", err);
    setStatus(err.message, "error");
    return null;
  }
}


/**
 * Botón "Paro" (paso 3): aborta el ensayo en curso.
 * NO cierra el WebSocket — sigue abierto para los live values del paso 3.
 */
async function stopCapture() {
  try {
    const response = await fetch(`${State.API_BASE}/api/test/stop`, {
      method: "POST"
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${response.status}`);
    }
    // La UI la actualiza onTestStopped al recibir el evento.
  } catch (err) {
    console.error("No se pudo detener el ensayo:", err);
    setStatus(`No se pudo detener el ensayo: ${err.message}`, "error");
  }
}


/* ==================== ESCRITURA AL PLC ==================== */

/**
 * Interruptor "Escribir en el PLC".
 *
 * Armar es un paso deliberado: mientras no se arme, Inicio solo dibuja. Se
 * escribe siempre sobre la variable que esté mapeada al rol `actuator` en el
 * paso 1 — la que el usuario haya elegido.
 */
async function toggleWriter() {
  const chk = document.getElementById("chkWriter");
  const enabled = !!chk?.checked;

  try {
    const response = await fetch(`${State.API_BASE}/api/test/writer`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ enabled })
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

    renderWriterState(data);
    setStatus(data.detail, enabled ? "warn" : "ok");
  } catch (err) {
    // El backend rechazó el armado: la casilla no puede quedar marcada
    // sugiriendo que se va a escribir cuando no es así.
    if (chk) chk.checked = false;
    renderWriterState({ enabled: false, writable: false, detail: err.message });
    setStatus(`No se pudo armar la escritura: ${err.message}`, "error");
  }
}


/** Pinta el estado del interruptor a partir de la respuesta del backend. */
function renderWriterState(data) {
  const chk   = document.getElementById("chkWriter");
  const badge = document.getElementById("writerBadge");
  const hint  = document.getElementById("writerHint");
  const card  = document.getElementById("writerCard");

  const enabled = !!data?.enabled;

  if (chk) chk.checked = enabled;
  if (card) card.classList.toggle("armed", enabled);

  if (badge) {
    badge.textContent = enabled
      ? `Escribiendo en ${data.variable || "?"}`
      : "Solo dibujo";
  }

  if (hint) {
    hint.textContent = enabled
      ? "El ensayo va a mover el actuador. Al terminar o al pulsar Paro se " +
        "devuelve a su valor inicial."
      : data?.detail ||
        "Sin activar, el ensayo dibuja el escalón pero no toca el actuador.";
  }
}


/** Consulta el estado al cargar la vista, para reflejar lo que ya haya armado. */
async function refreshWriterState() {
  try {
    const response = await fetch(`${State.API_BASE}/api/test/writer`);
    if (!response.ok) return;
    renderWriterState(await response.json());
  } catch (_) {
    // Sin backend disponible se deja el estado por defecto (desarmado).
  }
}


/* ==================== EVENTOS DEL ENSAYO ==================== */

/**
 * `test_started`: el backend arrancó y manda el perfil completo.
 *
 * El plan permite dibujar la línea objetivo entera de una vez, en lugar de
 * irla descubriendo punto a punto: se ve a dónde va el ensayo desde el
 * primer segundo.
 */
function onTestStarted(data) {
  const plan = data?.plan || null;

  State.ensayo.running   = true;
  State.ensayo.plan      = plan;
  State.ensayo.durationS = Number(plan?.duration_s) || Number(data?.duration_s) || 120;
  State.ensayo.elapsedS  = 0;
  State.ensayo.phase     = data?.phase || null;

  resetSampleStore();
  setTextareaValues("manualTime", [], 2);
  setTextareaValues("manualAct",  [], 3);
  setTextareaValues("manualSen",  [], 3);

  document.getElementById("btnStart").style.display   = "none";
  document.getElementById("btnStop").style.display    = "";
  document.getElementById("btnToIdent").style.display = "none";

  const box = document.getElementById("ensayoTimerBox");
  if (box) box.className = "ensayo-timer running";

  plotCapture();

  const unidad = plan?.unit || "";
  setStatus(
    `Ensayo en curso — ${State.ensayo.durationS} s · ` +
      `${plan?.from_value ?? "?"} → ${plan?.to_value ?? "?"} ${unidad} ` +
      `en t=${plan?.step_at_s ?? "?"} s`,
    "running"
  );
}


/**
 * `test_tick`: una por muestra.
 *
 * El actuador es lo que el backend COMANDA (`actuator_cmd`); el sensor y el
 * setpoint son el último valor REAL del PLC, cacheado por handleSample.
 * Es un sample-and-hold: se muestrea la señal del PLC al ritmo del ensayo.
 */
function onTestTick(data) {
  if (!data) return;

  State.ensayo.running  = true;
  State.ensayo.elapsedS = Number(data.elapsed_s) || 0;
  State.ensayo.phase    = data.phase || null;

  const latest = State.latestSample || {};
  const s = State.sampleStore;

  s.time.push(State.ensayo.elapsedS);

  // Comandado por el backend. `actuator_pct` ya no va en null: antes el
  // actuador desaparecía del chart al cambiar la vista a porcentaje.
  s.actuator_ma.push(pickNumber(data.actuator_cmd));
  s.actuator_pct.push(pickNumber(data.actuator_cmd_pct));

  // Leído del PLC
  s.sensor_ma.push(latest.sensorMa);
  s.sensor_pct.push(latest.sensorPct);
  s.setpoint_ma.push(latest.setpointMa);
  s.setpoint_pct.push(latest.setpointPct);

  const counter = document.getElementById("ensayoCounter");
  if (counter) counter.textContent = `${State.ensayo.elapsedS.toFixed(1)} s`;

  plotCapture();
  fillManualTextareas();
}


/** `test_finished`: el ensayo completó su duración. */
function onTestFinished(data) {
  State.ensayo.running = false;

  const box = document.getElementById("ensayoTimerBox");
  if (box) box.style.display = "none";

  document.getElementById("btnStop").style.display  = "none";
  document.getElementById("btnStart").style.display = "";
  document.getElementById("btnIdent").style.display = "";

  plotCapture();

  setStatus(
    `Ensayo completado (${State.ensayo.durationS} s, ` +
      `${State.sampleStore.time.length} muestras). ` +
      `Presiona "Identificar" para procesar.`,
    "ok"
  );
}


/** `test_stopped`: alguien lo cortó antes de tiempo. */
function onTestStopped(data) {
  State.ensayo.running = false;

  const box = document.getElementById("ensayoTimerBox");
  if (box) box.style.display = "none";

  document.getElementById("btnStop").style.display  = "none";
  document.getElementById("btnStart").style.display = "";

  // Las muestras capturadas se conservan: si alcanzan, se puede identificar.
  if (State.sampleStore.time.length) {
    document.getElementById("btnIdent").style.display = "";
  }

  plotCapture();

  setStatus(
    `Ensayo detenido a los ${(Number(data?.elapsed_s) || 0).toFixed(1)} s ` +
      `(${State.sampleStore.time.length} muestras capturadas)`,
    "error"
  );
}


/**
 * `test_aborted`: se perdió el control del actuador.
 *
 * El backend cortó el ensayo tras varias escrituras fallidas seguidas. Los
 * datos capturados hasta ese punto NO sirven para identificar: a partir del
 * primer fallo la entrada real dejó de seguir al perfil, así que el modelo
 * saldría de una entrada que nunca se aplicó.
 */
function onTestAborted(data) {
  State.ensayo.running = false;

  const box = document.getElementById("ensayoTimerBox");
  if (box) box.style.display = "none";

  document.getElementById("btnStop").style.display  = "none";
  document.getElementById("btnStart").style.display = "";
  // Se deja oculto a propósito: identificar sobre esto daría un modelo falso.
  document.getElementById("btnIdent").style.display = "none";

  plotCapture();
  refreshWriterState();

  setStatus(
    `Ensayo abortado: ${data?.abort_reason || "se perdió el control del actuador"}. ` +
      `Los datos capturados no sirven para identificar. Revisa la conexión con ` +
      `el PLC y repite el ensayo.`,
    "error"
  );
}


/**
 * `test_state`: respuesta a `get_test_state`, y también lo primero que manda
 * el backend al conectar si YA hay un ensayo corriendo.
 *
 * Sirve para engancharse a un ensayo en marcha tras recargar la página: se
 * recupera el plan y la vista vuelve a su sitio sin esperar al próximo tick.
 * Las muestras anteriores a la recarga se perdieron (vivían en el navegador),
 * pero el backend sí las tiene en su buffer y la identificación las usa.
 */
function onTestState(data) {
  if (!data) return;

  if (!data.running) {
    State.ensayo.running = false;
    return;
  }

  onTestStarted(data);
  State.ensayo.elapsedS = Number(data.elapsed_s) || 0;

  setStatus(
    `Reenganchado a un ensayo en curso (${State.ensayo.elapsedS.toFixed(1)} s ` +
      `de ${State.ensayo.durationS} s)`,
    "running"
  );
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
    // Por si se recargó la página con un ensayo en marcha: el backend
    // responde con el estado y el plan, y la vista se reengancha.
    sendWsMessage({ type: "get_test_state" });
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

    // ---- Ensayo (el reloj lo lleva el backend) ----
    case "test_started":
      onTestStarted(msg.data || {});
      break;

    case "test_tick":
      onTestTick(msg.data || {});
      break;

    case "test_finished":
      onTestFinished(msg.data || {});
      break;

    case "test_stopped":
      onTestStopped(msg.data || {});
      break;

    case "test_aborted":
      onTestAborted(msg.data || {});
      break;

    case "test_state":
      onTestState(msg.data);
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
 * NO toca el sampleStore ni redibuja el chart: de eso se encarga onTestTick,
 * al ritmo que marca el backend. Aquí solo se cachea la última muestra, que
 * es lo que ese tick lee para el sensor y el setpoint (sample-and-hold).
 *
 * Los samples llegan siempre, haya o no ensayo, porque los live values del
 * paso 3 y los dropdowns del paso 1 tienen que funcionar igual.
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
