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
async function startCapture(forzarSinEscritura = false) {
  // El WS tiene que estar abierto ANTES del start: si no, se pierden los
  // primeros ticks (y el test_started con el plan).
  ensureWebSocket();

  // Chequeo previo: un ensayo con la escritura desarmada corre entero sin
  // mover el actuador. La curva "Comandado" sube y la "Leído del PLC" se
  // queda plana, y no hay nada que identificar — pero eso solo se descubre
  // dos minutos después, cuando el ensayo ya terminó.
  if (!forzarSinEscritura) {
    const bloqueo = await avisarSiNoVaAEscribir();
    if (bloqueo) return null;
  }

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
 * ¿Este ensayo va a mover el actuador de verdad?
 *
 * Devuelve `true` si conviene NO arrancar todavía. Se avisa solo en el caso
 * que importa: la variable es escribible pero el armado está apagado. Si no
 * hay sesión OPC UA o la variable es de solo lectura, el ensayo en modo dibujo
 * es lo único posible y no hay nada que advertir.
 */
async function avisarSiNoVaAEscribir() {
  let estado;

  try {
    const response = await fetch(`${State.API_BASE}/api/test/writer`);
    if (!response.ok) return false;
    estado = await response.json();
  } catch (_) {
    return false;  // sin diagnóstico, mejor no estorbar
  }

  if (estado.enabled) return false;   // armado: adelante
  if (!estado.writable) return false; // no se puede escribir de todas formas

  const config = State.test?.step || {};
  const unidad = config.actuator_scale?.unit || "";

  mostrarAvisoEscritura({
    variable: estado.variable,
    desde: config.step_from,
    hasta: config.step_to,
    unidad,
    duracion: config.duration_s,
  });

  return true;
}


/** Aviso bloqueante con las dos salidas: armar y arrancar, o solo dibujar. */
function mostrarAvisoEscritura({ variable, desde, hasta, unidad, duracion }) {
  document.getElementById("avisoEscritura")?.remove();

  const overlay = document.createElement("div");
  overlay.id = "avisoEscritura";
  overlay.className = "import-overlay";

  overlay.innerHTML = `
    <div class="import-modal" style="max-width:520px">
      <h3>El ensayo no va a mover el actuador</h3>
      <p style="font-size:13px;line-height:1.6;color:var(--text2);margin:10px 0 4px">
        La escritura en el PLC está <strong>desarmada</strong>, así que estos
        ${duracion ?? "?"} s van a dibujar el escalón pero
        <strong>${escapeHtml(variable || "la variable")}</strong> se quedará
        en su valor actual. La curva "Leído del PLC" saldrá plana y no habrá
        nada que identificar.
      </p>
      <p style="font-size:12.5px;color:var(--text3);margin:0 0 16px">
        El escalón configurado es
        ${desde ?? "?"} → ${hasta ?? "?"} ${escapeHtml(unidad)}.
      </p>
      <div class="imp-actions">
        <button class="btn btn-secondary" onclick="cerrarAvisoEscritura()">
          Cancelar
        </button>
        <button class="btn btn-secondary" onclick="cerrarAvisoEscritura(); startCapture(true)">
          Solo dibujar
        </button>
        <button class="btn btn-primary" onclick="armarYArrancar()">
          <i class="fa-solid fa-play"></i> Armar y arrancar
        </button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);
}


function cerrarAvisoEscritura() {
  document.getElementById("avisoEscritura")?.remove();
}


/** Arma la escritura y arranca el ensayo en un solo gesto. */
async function armarYArrancar() {
  cerrarAvisoEscritura();

  const chk = document.getElementById("chkWriter");
  if (chk) chk.checked = true;

  await toggleWriter();

  // `toggleWriter` desmarca la casilla si el backend rechazó el armado.
  if (!document.getElementById("chkWriter")?.checked) {
    setStatus(
      "No se pudo armar la escritura, así que el ensayo no arrancó.",
      "error"
    );
    return;
  }

  await startCapture(true);
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


/**
 * `test_presetting`: la app está llevando la planta al valor inicial.
 *
 * Todavía NO se está grabando. Es importante que se vea, porque si no el
 * usuario pulsa Inicio y durante unos segundos no pasa nada visible: parece
 * que la app se colgó.
 */
function onTestPresetting(data) {
  const sensor = data?.sensor;
  const leido = typeof sensor === "number" ? `${sensor.toFixed(2)} %` : "sin lectura";

  setStatus(
    `Llevando la planta a ${formatNumber(data?.target, 2)} antes de grabar — ` +
      `sensor ${leido} · ${formatNumber(data?.elapsed_s, 1)} s de ` +
      `${formatNumber(data?.timeout_s, 0)} s`,
    "running"
  );
}


/**
 * `writer_released`: el ensayo terminó y la app soltó la variable del actuador.
 *
 * La casilla NO se desmarca: refleja la intención del usuario, y el backend
 * rearma solo en el siguiente ensayo. Desmarcarla obligaría a rearmar a mano
 * cada vez, que es justo lo que se sentía como "el reinicio no genera la señal
 * del actuador".
 */
function onWriterReleased(data) {
  const badge = document.getElementById("writerBadge");
  const card  = document.getElementById("writerCard");
  const hint  = document.getElementById("writerHint");

  if (card) card.classList.remove("armed");
  if (badge) badge.textContent = data?.intent ? "Armado · en espera" : "Solo dibujo";

  if (hint && data?.intent) {
    hint.textContent =
      "El actuador quedó en su valor inicial y la variable volvió a la HMI. " +
      "El próximo ensayo la toma de nuevo automáticamente.";
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

  showEnsayoTimer("running", 0);

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

  // LEÍDO del PLC — la variable que el usuario mapeó a cada rol.
  // Es lo que se grafica y lo que la identificación consume de verdad.
  s.actuator_ma.push(latest.actuatorMa);
  s.actuator_pct.push(latest.actuatorPct);
  s.sensor_ma.push(latest.sensorMa);
  s.sensor_pct.push(latest.sensorPct);
  s.setpoint_ma.push(latest.setpointMa);
  s.setpoint_pct.push(latest.setpointPct);

  // COMANDADO por el backend. Va aparte: si se guardara en `actuator_ma` el
  // gráfico mostraría siempre el escalón ideal y cambiar la variable mapeada
  // no tendría ningún efecto visible — que es justo lo que pasaba antes.
  s.actuator_cmd_ma.push(pickNumber(data.actuator_cmd));
  s.actuator_cmd_pct.push(pickNumber(data.actuator_cmd_pct));

  showEnsayoTimer("running", State.ensayo.elapsedS);

  plotCapture();
  fillManualTextareas();
}


/* ==================== CONTADOR FLOTANTE ==================== */

/**
 * Pinta la notificación "Tiempo de ensayo": transcurrido y duración total.
 *
 * La visibilidad se maneja SOLO con clases (`running` / `done`). El CSS la
 * resuelve con opacity + transform; tocar `display` desde aquí cortaría la
 * transición de entrada y salida.
 *
 * @param {"running"|"done"|"hidden"} estado
 * @param {number} elapsed segundos transcurridos
 */
function showEnsayoTimer(estado, elapsed) {
  const box     = document.getElementById("ensayoTimerBox");
  const counter = document.getElementById("ensayoCounter");
  const total   = document.getElementById("ensayoTotal");

  if (box) {
    box.className =
      estado === "hidden" ? "ensayo-timer" : `ensayo-timer ${estado}`;
  }

  if (counter) {
    counter.textContent =
      Number.isFinite(elapsed) ? `${Number(elapsed).toFixed(1)} s` : "—";
  }

  // La duración es la configurada en el paso 2 (o la del plan que mandó el
  // backend al arrancar, que es la misma). Sin ella no hay contra qué leer
  // el transcurrido.
  if (total) {
    const duracion =
      Number(State.ensayo?.durationS) || Number(State.test?.step?.duration_s) || null;
    total.textContent = duracion ? `/ ${Number(duracion).toFixed(0)} s` : "";
  }
}


/** `test_finished`: el ensayo completó su duración. */
function onTestFinished(data) {
  State.ensayo.running = false;

  // Se oculta al terminar, como pide el diseño.
  showEnsayoTimer("hidden", State.ensayo.elapsedS);

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

  showEnsayoTimer("hidden", Number(data?.elapsed_s) || 0);

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

  showEnsayoTimer("hidden", Number(data?.elapsed_s) || 0);

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
    case "test_presetting":
      onTestPresetting(msg.data || {});
      break;

    case "test_settled":
      setStatus(
        `Planta estabilizada en ${formatNumber(msg.data?.sensor, 2)} % ` +
          `tras ${formatNumber(msg.data?.elapsed_s, 1)} s. Empieza la captura.`,
        "ok"
      );
      break;

    case "test_settle_timeout":
      setStatus(msg.data?.detail || "La planta no llegó a estabilizarse.", "warn");
      break;

    case "writer_released":
      onWriterReleased(msg.data || {});
      break;

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

  // 2. Cachear la última muestra completa — la usa onTestTick para
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
  updateVariablePreview(data);
}


/**
 * Muestra bajo cada <select> del paso 1 qué está leyendo esa variable ahora.
 *
 * Sin esto no hay forma de notar que se eligió una variable que vale siempre
 * lo mismo —una declarada pero que el programa PLC nunca asigna, por ejemplo—
 * y el síntoma se confunde con "el mapeo no funciona".
 *
 * El valor sale de `raw`, que trae TODAS las variables del programa con su
 * nombre real. Así el número corresponde exactamente a la variable elegida.
 */
function updateVariablePreview(data) {
  const raw = data?.raw || {};
  const mapping = data?.mapping || State.mapping || {};

  const targets = [
    { id: "liveAct", role: "actuator" },
    { id: "liveSen", role: "sensor" },
    { id: "liveSP",  role: "setpoint" }
  ];

  targets.forEach(({ id, role }) => {
    const el = document.getElementById(id);
    if (!el) return;

    const nombre = mapping[role];

    if (!nombre) {
      el.textContent = "— sin asignar —";
      el.classList.remove("estatica");
      return;
    }

    const valor = pickNumber(raw[nombre]);
    el.textContent =
      valor === null ? `${nombre}: sin lectura` : `${nombre} = ${valor.toFixed(3)}`;

    // Se marca la que lleva un rato sin moverse: casi siempre es señal de que
    // se eligió la variable equivocada.
    el.classList.toggle("estatica", registrarYDetectarEstatica(id, valor));
  });
}


/**
 * ¿Lleva esta variable muchas lecturas seguidas con el mismo valor?
 *
 * No basta con comparar contra la lectura anterior: una señal lenta cambia
 * poco entre muestras consecutivas. Se exige una racha larga para no marcar
 * como sospechosa una señal que simplemente está en régimen permanente.
 */
const RACHA_ESTATICA = 50;   // ~10 s a 0.2 s por muestra
const _historial = {};

function registrarYDetectarEstatica(id, valor) {
  const h = (_historial[id] ||= { ultimo: null, repeticiones: 0 });

  if (valor === null) {
    h.repeticiones = 0;
    return false;
  }

  if (h.ultimo !== null && Math.abs(valor - h.ultimo) < 1e-9) {
    h.repeticiones += 1;
  } else {
    h.repeticiones = 0;
  }

  h.ultimo = valor;
  return h.repeticiones >= RACHA_ESTATICA;
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
 * Mantiene los <select> del paso 1 alineados con la realidad del backend.
 *
 * Cada muestra trae `mapping`: el mapeo EFECTIVO con el que el PLCReader está
 * leyendo en ese instante. Este mapeo es la fuente de verdad y el desplegable
 * tiene que reflejarlo siempre.
 *
 * Antes esto corría una sola vez, con la idea de "no pisar lo que eligió el
 * usuario". El efecto era el contrario: el select se congelaba en la primera
 * muestra y mentía en cuanto el mapeo cambiaba desde otro sitio —otra pestaña,
 * Swagger, un re-login, o el propio backend resolviendo un alias distinto—.
 * Se veía `rActuator` en pantalla mientras el backend leía `rSensor`.
 *
 * Para no pisar un cambio del usuario mientras viaja al backend se usa
 * `State.mappingPending`, que `applyMappingChange` levanta durante el POST.
 */
function populateVariableDropdowns(sample) {
  // El catálogo COMPLETO del programa viaja en `variables`, no en `raw`.
  //
  // `raw` solo trae las variables que tienen un rol asignado: se dejó de
  // muestrear el programa entero porque cada lectura es un viaje de red y eso
  // hundía el periodo de muestreo. Poblar el desplegable desde `raw` deja al
  // usuario eligiendo únicamente entre las que YA están elegidas, sin forma de
  // llegar al resto. El síntoma engaña: si el programa tiene tantas variables
  // como roles, la lista se ve completa y todo parece normal.
  const keys = Array.isArray(sample?.variables) && sample.variables.length
    ? sample.variables
    : Object.keys(sample?.raw || {});   // backends viejos, sin `variables`

  if (!keys.length) return;

  // El desplegable del ciclo de tarea se alimenta del mismo catálogo.
  if (typeof poblarVariablesDeCiclo === "function") poblarVariablesDeCiclo(keys);

  const mapping = sample.mapping || {};

  const targets = [
    { id: "varAct", role: "actuator", opcional: false },
    { id: "varSen", role: "sensor",   opcional: false },
    { id: "varSP",  role: "setpoint", opcional: true  }
  ];

  targets.forEach(({ id, role, opcional }) => {
    const sel = document.getElementById(id);
    if (!sel) return;

    // Las opciones se reconstruyen SOLO si cambió la lista de variables (por
    // ejemplo al cambiar de programa). Rehacerlas en cada muestra cerraría el
    // desplegable justo cuando el usuario lo tiene abierto.
    const actuales = Array.from(sel.options)
      .map((o) => o.value)
      .filter((v) => v !== "");

    const mismasOpciones =
      actuales.length === keys.length && actuales.every((v, i) => v === keys[i]);

    if (!mismasOpciones) {
      const previo = sel.value;

      const opts = opcional ? [`<option value="">— sin asignar —</option>`] : [];
      keys.forEach((k) => {
        opts.push(`<option value="${escapeHtml(k)}">${escapeHtml(k)}</option>`);
      });
      sel.innerHTML = opts.join("");

      // innerHTML resetea la selección al primer <option>.
      if (previo && keys.includes(previo)) sel.value = previo;
    }

    // Hay un cambio del usuario viajando al backend: no se toca hasta saber
    // si lo aceptó, o el select parpadearía al valor viejo.
    if (State.mappingPending) return;

    const real = mapping[role] || "";
    const asignable = real === "" ? opcional : keys.includes(real);

    if (asignable && sel.value !== real) sel.value = real;
  });
}


/* ==================== CAMBIO DE MAPEO EN CALIENTE ==================== */
/* `applyMappingChange` vive en main.js. Aquí había una segunda definición que
   quedaba SIEMPRE anulada, porque main.js se carga después y las declaraciones
   de función de nivel superior se pisan entre sí. Dos versiones del mismo
   nombre con comportamientos distintos: la que se leía al depurar no era la
   que corría. Se eliminó y lo bueno que tenía se movió a main.js. */


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

  // Render.
  //
  // El medido tiene que salir de `data.window`, NO del sampleStore completo.
  // `simulated` cubre solo la ventana de identificación —un recorte alrededor
  // del escalón, con el tiempo re-basado a 0—, así que emparejarlo con el
  // tiempo del buffer entero lo dibuja desplazado al principio y cortado a los
  // pocos segundos: parece que el modelo "no considera" el resto del ensayo
  // cuando en realidad se está pintando contra el eje equivocado.
  const s = State.sampleStore;
  const type = getSignalType();
  const ventana = data.window || {};

  const measured = Array.isArray(ventana.sensor) && ventana.sensor.length
    ? ventana.sensor
    : (type === "pct" ? s.sensor_pct : s.sensor_ma);

  const tiempo = Array.isArray(ventana.time) && ventana.time.length
    ? ventana.time
    : s.time;

  // Se conserva para que `selectAlt` dibuje sobre el MISMO eje. Antes solo
  // existía aquí, y al cambiar de modelo alternativo se caía al sampleStore
  // completo: el gráfico se descuadraba en cuanto se tocaba una card.
  State.identification.window =
    Array.isArray(ventana.time) && ventana.time.length
      ? { time: tiempo, sensor: measured, count: tiempo.length }
      : null;

  renderIdent(models, measured, tiempo, winner);
  renderBode(models[State.identification.active]);
  renderPID(models, State.identification.active);

  // UI: habilitar botones que llevan al paso 4/5
  // (btnIdent ya está siempre visible: es el disparador manual)
  document.getElementById("btnToIdent").style.display = "";

  renderIdentWarnings(data);

  const r2 = models[State.identification.active]?.fit_quality;
  const avisos = Array.isArray(data.warnings) ? data.warnings : [];

  // Un R² bonito con avisos graves detrás es peor que un error: invita a
  // sintonizar un PID con una dinámica que nadie midió.
  setStatus(
    avisos.length
      ? `Identificación lista con reservas (R² ${formatNumber(r2, 1)}%) — ${avisos[0]}`
      : `Identificación lista — mejor ajuste R²: ${formatNumber(r2, 1)}%`,
    avisos.length ? "running" : "ok"
  );
}


/**
 * Pinta los avisos de calidad sobre el panel del paso 4.
 *
 * El backend los manda en `warnings`. Son casos en los que el ajuste
 * *converge* pero no describe la planta —el más común es que el proceso sea
 * más rápido que el muestreo, con lo que tau se colapsa a cero y queda una
 * ganancia pura—. Sin mostrarlos, el resultado se ve idéntico a uno bueno.
 */
function renderIdentWarnings(data) {
  const cont = document.getElementById("identWarnings");
  if (!cont) return;

  const avisos = Array.isArray(data?.warnings) ? data.warnings : [];

  if (!avisos.length) {
    cont.innerHTML = "";
    cont.style.display = "none";
    return;
  }

  cont.innerHTML = avisos
    .map(
      (w) =>
        `<div class="ident-warning">
           <i class="fa-solid fa-triangle-exclamation"></i>
           <span>${escapeHtml(w)}</span>
         </div>`
    )
    .join("");
  cont.style.display = "block";
}
