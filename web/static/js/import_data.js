/* =========================================================
   import_data.js
   Importación de ensayos desde archivo (botón "Importar").

   Flujo:
     1. El usuario elige un archivo (.trace.csv de CODESYS, CSV o xlsx).
     2. POST /api/import/parse -> variables encontradas.
     3. Modal: asignar qué variable del ARCHIVO es actuador/sensor/setpoint.
     4. POST /api/import/load -> el buffer del backend pasa a ser el archivo
        (modo importado: las muestras del PLC se ignoran).
     5. Identificar / gráficos / PID funcionan igual que en vivo.

   El banner de modo importado y el botón "Volver a tiempo real" viven aquí.
   ========================================================= */


let _importParse = null;  // respuesta de /parse, viva mientras el modal está abierto


/** Entrada: el <input type="file"> oculto de la topbar dispara esto. */
async function handleImportFile(input) {
  const file = input.files && input.files[0];
  input.value = "";  // permite volver a elegir el mismo archivo
  if (!file) return;

  setStatus(`Analizando ${file.name}...`, "running");

  const form = new FormData();
  form.append("file", file, file.name);

  try {
    const response = await fetch(`${State.API_BASE}/api/import/parse`, {
      method: "POST",
      body: form
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

    _importParse = data;
    renderImportModal(data);
    setStatus(
      `${file.name}: ${data.samples} muestras · ${data.variables.length} variables · ` +
        `${(data.sample_period_s * 1000).toFixed(0)} ms`,
      "ok"
    );
  } catch (err) {
    console.error("Error al analizar el archivo:", err);
    setStatus(`No se pudo leer el archivo: ${err.message}`, "error");
  }
}


/* ==================== MODAL DE MAPEO ==================== */

function renderImportModal(data) {
  closeImportModal();

  const overlay = document.createElement("div");
  overlay.id = "importModal";
  overlay.className = "import-overlay";

  const options = (rol, opcional) => {
    const sugerido = data.suggested_mapping?.[rol] || "";
    const opts = opcional ? ['<option value="">— sin asignar —</option>'] : [];
    data.variables.forEach((v) => {
      const sel = v.name === sugerido ? " selected" : "";
      const aviso = v.constant ? " (constante)" : "";
      opts.push(
        `<option value="${escapeHtml(v.name)}"${sel}>` +
          `${escapeHtml(v.name)}${aviso}</option>`
      );
    });
    return opts.join("");
  };

  const filas = data.variables
    .map(
      (v) => `
      <tr>
        <td class="imp-name">${escapeHtml(v.name)}</td>
        <td>${v.samples}</td>
        <td>${formatNumber(v.min, 2)} … ${formatNumber(v.max, 2)}</td>
        <td>${v.constant ? '<span class="imp-warn">constante</span>' : "ok"}</td>
      </tr>`
    )
    .join("");

  overlay.innerHTML = `
    <div class="import-modal">
      <h3>Importar ensayo — ${escapeHtml(data.source_name)}</h3>
      <p class="imp-meta">
        ${data.format} · ${data.samples} muestras ·
        ${(data.duration_s).toFixed(1)} s · periodo ${(data.sample_period_s * 1000).toFixed(0)} ms
      </p>

      <table class="imp-table">
        <thead><tr><th>Variable del archivo</th><th>Muestras</th><th>Rango</th><th></th></tr></thead>
        <tbody>${filas}</tbody>
      </table>

      <div class="imp-grid">
        <div>
          <label>Entrada (u) — Actuador*</label>
          <select id="impAct">${options("actuator", false)}</select>
        </div>
        <div>
          <label>Salida (y) — Sensor*</label>
          <select id="impSen">${options("sensor", false)}</select>
        </div>
        <div>
          <label>Set Point — opcional</label>
          <select id="impSP">${options("setpoint", true)}</select>
        </div>
        <div>
          <label>Escala de las señales</label>
          <select id="impScale">
            <option value="pct" selected>0-100 % (unidades de ingeniería)</option>
            <option value="ma">4-20 mA</option>
            <option value="v">0-10 V</option>
          </select>
        </div>
      </div>

      <div class="imp-actions">
        <button class="btn btn-secondary" onclick="closeImportModal()">Cancelar</button>
        <button class="btn btn-primary" onclick="confirmImport()">
          <i class="fa-solid fa-file-import"></i> Cargar datos
        </button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);
}


function closeImportModal() {
  document.getElementById("importModal")?.remove();
}


async function confirmImport() {
  if (!_importParse) return;

  const actuator = document.getElementById("impAct")?.value || "";
  const sensor   = document.getElementById("impSen")?.value || "";
  const setpoint = document.getElementById("impSP")?.value || null;
  const scale    = document.getElementById("impScale")?.value || "pct";

  if (!actuator || !sensor) {
    setStatus("Asigna al menos actuador y sensor.", "error");
    return;
  }
  if (actuator === sensor) {
    setStatus("Actuador y sensor no pueden ser la misma variable.", "error");
    return;
  }

  setStatus("Cargando datos importados...", "running");

  try {
    const response = await fetch(`${State.API_BASE}/api/import/load`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: _importParse.token,
        mapping: { actuator, sensor, setpoint },
        scale
      })
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

    closeImportModal();
    _importParse = null;

    enterImportedMode(data);

    // El buffer del backend ya es el archivo: pedir la serie y volcarla al
    // sampleStore para que los gráficos del paso 3 la muestren tal cual.
    await loadImportedSeriesIntoStore();

    setStatus(
      `Datos importados: ${data.source_name} (${data.samples} muestras). ` +
        `Ve al paso 3 para ver las señales, o Identificar directamente.`,
      "ok"
    );
  } catch (err) {
    console.error("Error al cargar importado:", err);
    setStatus(`No se pudo cargar: ${err.message}`, "error");
  }
}


/**
 * Trae la serie completa del backend y la vuelca al sampleStore local.
 * Así plotCapture y los textareas muestran el archivo sin tocar su código.
 */
async function loadImportedSeriesIntoStore() {
  try {
    const response = await fetch(`${State.API_BASE}/api/test/series?percent=false`);
    if (!response.ok) return;
    const serie = await response.json();
    fillStoreFromSeries(serie);
  } catch (_) {
    // Fallback por WebSocket si el endpoint REST no está.
    sendWsMessage({ type: "get_series" });
  }
}


function fillStoreFromSeries(serie) {
  if (!serie || !Array.isArray(serie.time)) return;

  resetSampleStore();
  const s = State.sampleStore;

  serie.time.forEach((t, i) => {
    s.time.push(t);
    s.actuator_ma.push(serie.actuator?.[i] ?? null);
    s.sensor_ma.push(serie.sensor?.[i] ?? null);
    s.setpoint_ma.push(serie.setpoint?.[i] ?? null);
    // En importado la escala cruda YA es la de trabajo; los _pct los calcula
    // el backend en cada sample, aquí se replican para el toggle de la vista.
    s.actuator_pct.push(serie.actuator?.[i] ?? null);
    s.sensor_pct.push(serie.sensor?.[i] ?? null);
    s.setpoint_pct.push(serie.setpoint?.[i] ?? null);
    s.actuator_cmd_ma.push(null);
    s.actuator_cmd_pct.push(null);
  });

  // Eje X del gráfico = duración del archivo.
  State.ensayo.durationS = serie.time.length ? serie.time[serie.time.length - 1] : null;
  State.ensayo.plan = null;  // no hay línea objetivo: esto no es un ensayo del runner

  if (typeof plotCapture === "function") plotCapture();
  if (typeof fillManualTextareas === "function") fillManualTextareas();
}


/* ==================== BANNER DE MODO IMPORTADO ==================== */

function enterImportedMode(info) {
  removeImportBanner();

  const banner = document.createElement("div");
  banner.id = "importBanner";
  banner.className = "import-banner";
  banner.innerHTML = `
    <i class="fa-solid fa-file-import"></i>
    <span>
      <strong>Datos importados:</strong> ${escapeHtml(info.source_name || "archivo")}
      · ${info.samples} muestras · ${((info.sample_period_s || 0) * 1000).toFixed(0)} ms
      — las señales del PLC en vivo están pausadas
    </span>
    <button class="btn btn-secondary btn-sm" onclick="exitImportedMode()">
      Volver a tiempo real
    </button>
  `;

  const topbar = document.querySelector(".topbar");
  (topbar?.parentNode || document.body).insertBefore(banner, topbar?.nextSibling || null);

  // Durante el modo importado no tiene sentido arrancar un ensayo del runner.
  const btnStart = document.getElementById("btnStart");
  if (btnStart) btnStart.style.display = "none";
  const btnIdent = document.getElementById("btnIdent");
  if (btnIdent) btnIdent.style.display = "";
}


function removeImportBanner() {
  document.getElementById("importBanner")?.remove();
}


async function exitImportedMode() {
  try {
    await fetch(`${State.API_BASE}/api/import/clear`, { method: "POST" });
  } catch (_) {}

  removeImportBanner();
  resetSampleStore();
  if (typeof plotCapture === "function") plotCapture();

  const btnStart = document.getElementById("btnStart");
  if (btnStart) btnStart.style.display = "";

  setStatus("Modo importado cerrado. Se aceptan de nuevo señales del PLC.", "ok");
}


/** Al cargar la vista: si el backend sigue en modo importado, reflejarlo. */
async function refreshImportState() {
  try {
    const response = await fetch(`${State.API_BASE}/api/import/status`);
    if (!response.ok) return;
    const data = await response.json();

    if (data.active) {
      enterImportedMode(data);
      await loadImportedSeriesIntoStore();
    }
  } catch (_) {}
}
