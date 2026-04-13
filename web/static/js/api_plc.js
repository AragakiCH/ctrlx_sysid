let ws = null;
let wsReconnectTimer = null;

// ==================== STORE DE MUESTRAS EN TIEMPO REAL ====================
const SampleStore = {
  time: [],
  actuator_ma: [],
  sensor_ma: [],
  setpoint_ma: [],
  actuator_pct: [],
  sensor_pct: [],
  setpoint_pct: [],
  maxPoints: 300,
};

function getWsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

function connectWebSocket() {
  const wsUrl = getWsUrl();
  console.log("Conectando WS a:", wsUrl);

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("WebSocket conectado");
    if (typeof showNotification === "function") {
      showNotification("WebSocket conectado", "success");
    }

    // Dejamos identification_result activo
    sendWsMessage({ type: "ping" });
    sendWsMessage({ type: "get_latest" });
    sendWsMessage({ type: "get_latest_identification" });
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      console.log("mensaje:", msg);
      handleWsMessage(msg);
    } catch (err) {
      console.error("Error parseando mensaje WS:", err, event.data);
      if (typeof showNotification === "function") {
        showNotification("Mensaje WS inválido", "error");
      }
    }
  };

  ws.onerror = (err) => {
    console.error("Error WebSocket:", err);
  };

  ws.onclose = () => {
    console.warn("WebSocket cerrado");
    if (typeof showNotification === "function") {
      showNotification("WebSocket desconectado. Reintentando...", "error");
    }

    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = setTimeout(() => {
      connectWebSocket();
    }, 3000);
  };
}

function sendWsMessage(payload) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn("WS no está abierto:", payload);
    return;
  }
  ws.send(JSON.stringify(payload));
}

function handleWsMessage(msg) {
  const type = msg?.type;

  switch (type) {
    case "pong":
      console.log("pong recibido");
      break;

    case "latest":
      handleLatest(msg.data || msg);
      break;

    case "sample":
      handleSample(msg.data || {});
      break;

    case "identification_result":
      handleIdentificationResult(msg.data || {});
      break;

    case "error":
      console.error("Error WS:", msg);
      if (typeof showNotification === "function") {
        showNotification(
          msg.message || "Error recibido por WebSocket",
          "error",
        );
      }
      break;

    default:
      console.log("Tipo WS no manejado:", type, msg);
      break;
  }
}

function handleLatest(data) {
  console.log("latest:", data);
}

// ==================== SAMPLE -> VISTA SEÑALES ====================
// Espera algo como:
// {
//   actuator: 12,
//   actuator_pct: 50,
//   sensor: 7.21,
//   sensor_pct: 20.06,
//   setpoint: 12,
//   setpoint_pct: 50,
//   time: 5.99,
//   raw: {
//     rActuator: 12,
//     rSensor: 7.21,
//     rSetPoint: 12,
//     rTimeSec: 5.99
//   }
// }
function handleSample(data) {
  const raw = data.raw || {};

  const timeValue = pickNumber(data.time, raw.rTimeSec, raw.arrTimeSec);
  const actuatorMa = pickNumber(data.actuator, raw.rActuator);
  const sensorMa = pickNumber(data.sensor, raw.rSensor);
  const setpointMa = pickNumber(data.setpoint, raw.rSetPoint);

  const actuatorPct = pickNumber(data.actuator_pct);
  const sensorPct = pickNumber(data.sensor_pct);
  const setpointPct = pickNumber(data.setpoint_pct);

  if (timeValue === null) {
    console.warn("Sample sin tiempo válido:", data);
    return;
  }

  pushSample(SampleStore.time, timeValue);
  pushSample(SampleStore.actuator_ma, actuatorMa);
  pushSample(SampleStore.sensor_ma, sensorMa);
  pushSample(SampleStore.setpoint_ma, setpointMa);

  pushSample(SampleStore.actuator_pct, actuatorPct);
  pushSample(SampleStore.sensor_pct, sensorPct);
  pushSample(SampleStore.setpoint_pct, setpointPct);

  refreshSignalsViewFromSampleStore();
}

function refreshSignalsViewFromSampleStore() {
  const signalType = document.getElementById("signalType")?.value || "ma";
  console.log("Redibujando con signalType =", signalType);

  const time = [...SampleStore.time];

  const actuator =
    signalType === "percent"
      ? [...SampleStore.actuator_pct]
      : [...SampleStore.actuator_ma];

  const sensor =
    signalType === "percent"
      ? [...SampleStore.sensor_pct]
      : [...SampleStore.sensor_ma];

  const setpoint =
    signalType === "percent"
      ? [...SampleStore.setpoint_pct]
      : [...SampleStore.setpoint_ma];

  console.log("actuator[último] =", actuator[actuator.length - 1]);
  console.log("sensor[último] =", sensor[sensor.length - 1]);
  console.log("setpoint[último] =", setpoint[setpoint.length - 1]);

  setTextareaValues("dataTime", time);
  setTextareaValues("dataActuator", actuator);
  setTextareaValues("dataSensor", sensor);
  setTextareaValues("dataSetpoint", setpoint);

  if (typeof plotRawData === "function") {
    plotRawData(time, actuator, sensor, setpoint);
  }
}

function pushSample(arr, value) {
  arr.push(value);
  if (arr.length > SampleStore.maxPoints) {
    arr.shift();
  }
}

// ==================== IDENTIFICATION RESULT ====================
function handleIdentificationResult(data) {
  const models = Array.isArray(data.models) ? data.models : [];
  const winnerType = data.winner || null;

  if (!models.length) {
    console.warn("No llegaron modelos de identificación");
    return;
  }

  const normalized = models.map(normalizeModelResult);
  const winner =
    normalized.find((m) => m.model_type === winnerType) || normalized[0];

  renderMetricsBar(winner);
  renderResultsGrid(normalized, winnerType);
  renderPidSection(winner);
  plotPIDTunings(winner.pid_tunings);
  plotBode(winner);

  // Solo si el backend manda curvas simuladas
  const measuredTime = parseTextareaNumbers("dataTime");
  const measuredSensor = parseTextareaNumbers("dataSensor");
  const plottable = normalized.filter(
    (x) => Array.isArray(x.simulated) && x.simulated.length,
  );

  if (
    measuredTime.length &&
    measuredSensor.length &&
    plottable.length &&
    typeof plotComparison === "function"
  ) {
    const comparisonResults = plottable.map((m) => ({
      method: modelTypeLabel(m.model_type),
      simulated: m.simulated,
    }));

    plotComparison(measuredTime, measuredSensor, comparisonResults);
    showComparisonChart(true);
  } else {
    showComparisonChart(false);
  }

  showIdentificationViews();
}

function normalizeModelResult(model) {
  return {
    model_type: model.model_type || "unknown",
    fit_quality: asNumber(model.fit_quality),
    gain: asNumber(model.gain),
    dead_time: asNumber(model.dead_time),
    tau: asNumber(model.tau),
    tau1: asNumber(model.tau1),
    tau2: asNumber(model.tau2),
    tf_string: model.tf_string || "—",
    pid_tunings: Array.isArray(model.pid_tunings) ? model.pid_tunings : [],
    simulated: Array.isArray(model.simulated) ? model.simulated : null,
  };
}

function renderMetricsBar(winner) {
  const metricsBar = document.getElementById("metricsBar");
  const metBestModel = document.getElementById("metBestModel");
  const metFitQuality = document.getElementById("metFitQuality");
  const metK = document.getElementById("metK");
  const metL = document.getElementById("metL");

  if (!metricsBar || !winner) return;

  metBestModel.textContent = modelTypeLabel(winner.model_type);
  metFitQuality.textContent = formatNumber(winner.fit_quality, 4);
  metK.textContent = formatNumber(winner.gain, 4);
  metL.textContent = formatNumber(winner.dead_time, 4);

  metricsBar.style.display = "flex";
}

function renderResultsGrid(models, winnerType) {
  const grid = document.getElementById("resultsGrid");
  if (!grid) return;

  grid.innerHTML = models
    .map((model) => {
      const isWinner = model.model_type === winnerType;
      const paramsHtml = buildModelParamsHtml(model);
      const pidCount = model.pid_tunings?.length || 0;

      return `
      <div class="result-card ${isWinner ? "best" : ""}">
        <div class="result-header">
          <div class="result-method">${modelTypeLabel(model.model_type)}</div>
          <div class="result-badge ${isWinner ? "badge-best" : "badge-quality"}">
            ${isWinner ? "MEJOR AJUSTE" : "MODELO"}
          </div>
        </div>

        <div class="tf-display">
          <div class="tf-label">Función de Transferencia</div>
          <div style="font-family:'IBM Plex Mono', monospace; font-size:11px; color:#1a2332; line-height:1.6;">
            ${escapeHtml(model.tf_string)}
          </div>
        </div>

        <div class="params-grid">
          ${paramsHtml}
          <div class="param-cell">
            <div class="param-name">Fit R²</div>
            <div class="param-value">${formatNumber(model.fit_quality, 4)}</div>
          </div>
          <div class="param-cell">
            <div class="param-name">PID Tunings</div>
            <div class="param-value">${pidCount}</div>
          </div>
        </div>
      </div>
    `;
    })
    .join("");

  grid.style.display = "grid";
}

function buildModelParamsHtml(model) {
  let html = `
    <div class="param-cell">
      <div class="param-name">Ganancia K</div>
      <div class="param-value">${formatNumber(model.gain, 4)}</div>
    </div>
    <div class="param-cell">
      <div class="param-name">Dead Time L</div>
      <div class="param-value">${formatNumber(model.dead_time, 4)}</div>
    </div>
  `;

  if (model.model_type === "fopdt") {
    html += `
      <div class="param-cell">
        <div class="param-name">Tau</div>
        <div class="param-value">${formatNumber(model.tau, 4)}</div>
      </div>
    `;
  }

  if (model.model_type === "sopdt") {
    html += `
      <div class="param-cell">
        <div class="param-name">Tau1</div>
        <div class="param-value">${formatNumber(model.tau1, 4)}</div>
      </div>
      <div class="param-cell">
        <div class="param-name">Tau2</div>
        <div class="param-value">${formatNumber(model.tau2, 4)}</div>
      </div>
    `;
  }

  if (model.model_type === "integrating") {
    html += `
      <div class="param-cell">
        <div class="param-name">Tipo</div>
        <div class="param-value">INT</div>
      </div>
    `;
  }

  return html;
}

function renderPidSection(winner) {
  const noPidMsg = document.getElementById("noPidMsg");
  const pidContent = document.getElementById("pidContent");
  const pidTablesContainer = document.getElementById("pidTablesContainer");
  const pidInfoBox = document.getElementById("pidInfoBox");

  if (!pidTablesContainer || !winner) return;

  const tunings = Array.isArray(winner.pid_tunings) ? winner.pid_tunings : [];

  if (!tunings.length) {
    if (pidTablesContainer) pidTablesContainer.innerHTML = "";
    if (pidInfoBox)
      pidInfoBox.textContent = "El modelo ganador no incluye sintonías PID.";
    return;
  }

  const rows = tunings
    .map((t) => {
      const method = t.method || t.name || "Método";
      const kp = formatNumber(t.kp ?? t.Kp, 4);
      const ki = formatNumber(t.ki ?? t.Ki, 4);
      const kd = formatNumber(t.kd ?? t.Kd, 4);
      const desc = t.description || t.desc || "—";

      return `
      <tr>
        <td class="td-method">${escapeHtml(method)}</td>
        <td class="td-value">${kp}</td>
        <td class="td-value">${ki}</td>
        <td class="td-value">${kd}</td>
        <td class="td-desc">${escapeHtml(desc)}</td>
      </tr>
    `;
    })
    .join("");

  pidTablesContainer.innerHTML = `
    <div class="pid-table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Método</th>
            <th>Kp</th>
            <th>Ki</th>
            <th>Kd</th>
            <th>Descripción</th>
          </tr>
        </thead>
        <tbody>
          ${rows}
        </tbody>
      </table>
    </div>
  `;

  if (pidInfoBox) {
    pidInfoBox.textContent = `Sintonías PID generadas a partir del modelo ganador: ${modelTypeLabel(winner.model_type)}.`;
  }

  if (pidContent) pidContent.style.display = "block";
}

function showIdentificationViews() {
  const noResultsMsg = document.getElementById("noResultsMsg");
  const resultsGrid = document.getElementById("resultsGrid");
  const noBodeMsg = document.getElementById("noBodeMsg");
  const bodeContent = document.getElementById("bodeContent");

  if (noResultsMsg) noResultsMsg.style.display = "none";
  if (resultsGrid) resultsGrid.style.display = "grid";
  if (noBodeMsg) noBodeMsg.style.display = "none";
  if (bodeContent) bodeContent.style.display = "grid";
}

function showComparisonChart(visible) {
  const idChartCard = document.getElementById("idChartCard");
  if (!idChartCard) return;
  idChartCard.style.display = visible ? "flex" : "none";
}

// ==================== HELPERS ====================
function setTextareaValues(id, values) {
  const el = document.getElementById(id);
  if (!el || !Array.isArray(values)) return;
  el.value = values
    .map((v) =>
      typeof v === "number" && Number.isFinite(v) ? Number(v).toFixed(4) : "",
    )
    .join(", ");
}

function parseTextareaNumbers(id) {
  const el = document.getElementById(id);
  if (!el) return [];
  return el.value
    .split(",")
    .map((v) => Number(v.trim()))
    .filter((v) => !Number.isNaN(v));
}

function ensureArray(value) {
  return Array.isArray(value) ? value : [];
}

function asNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function pickNumber(...values) {
  for (const v of values) {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

function formatNumber(value, decimals = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value)))
    return "—";
  return Number(value).toFixed(decimals);
}

function modelTypeLabel(type) {
  switch ((type || "").toLowerCase()) {
    case "fopdt":
      return "1er Orden (FOPDT)";
    case "sopdt":
      return "2do Orden (SOPDT)";
    case "integrating":
      return "Integrante";
    default:
      return type || "Modelo";
  }
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

document.addEventListener("DOMContentLoaded", () => {
  connectWebSocket();

  const signalTypeSelect = document.getElementById("signalType");
  if (signalTypeSelect) {
    signalTypeSelect.addEventListener("change", () => {
      if (typeof updateSignalLabels === "function") {
        updateSignalLabels();
      }
      refreshSignalsViewFromSampleStore();
    });
  }
});
