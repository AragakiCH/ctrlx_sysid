/* =========================================================
   ui.js
   Navegación entre pasos, badges, conversiones de unidad,
   status bar y helpers de estado de conexión.
   ========================================================= */


/* ==================== NAVEGACIÓN DE PASOS ==================== */

/** Cambia al paso `n` (1..5) y actualiza indicadores del wizard. */
function goStep(n) {
  State.ui.step = n;

  [1, 2, 3, 4, 5].forEach((i) => {
    const panel = document.getElementById("p" + i);
    const btn   = document.getElementById("sb" + i);

    if (panel) panel.classList.toggle("active", i === n);
    if (btn) {
      btn.className = "step-btn" + (i === n ? " active" : i < n ? " done" : "");
    }
  });

  // Redraws diferidos (los canvas no miden bien cuando están display:none)
  if (n === 2) setTimeout(drawStepPreview, 60);
  if (n === 3 || n === 4) {
    setTimeout(() => {
      Object.values(State.charts).forEach((c) => c && c.resize && c.resize());
    }, 60);
  }

  // Al entrar al paso 3 hay que reflejar si la escritura está armada: el
  // estado vive en el backend y pudo cambiar (logout, cambio de mapeo).
  if (n === 3 && typeof refreshWriterState === "function") {
    refreshWriterState();
  }
}


/* ==================== SUB-TABS DEL PASO 4 ==================== */

/** Alterna entre "model" y "bode" dentro del paso 4. */
function switchIdentTab(tab) {
  ["model", "bode"].forEach((t) => {
    const el   = document.getElementById("ident-" + t);
    const item = document.getElementById("itab-" + t);
    if (el) el.style.display = t === tab ? (t === "bode" ? "flex" : "block") : "none";
    if (item) item.classList.toggle("active", t === tab);
  });

  if (tab === "bode") {
    setTimeout(() => {
      [State.charts.cBodeMag, State.charts.cBodePhase].forEach(
        (c) => c && c.resize && c.resize()
      );
    }, 60);
  }
}


/* ==================== BADGES DE TIPO DE SEÑAL ==================== */

const BADGE_CLS = { ma: "badge-ma",  pct: "badge-pct", v: "badge-v" };
const BADGE_LBL = { ma: "4-20 mA",   pct: "0-100 %",   v: "0-10 V" };

/** Refresca el badge de la variable indicada ("act" | "sen" | "sp"). */
// Id del <select> de cada rol. Antes se derivaba del nombre corto capitalizando
// la primera letra ("sp" -> "typeSp"), pero el elemento se llama "typeSP":
// getElementById devolvía null, la función salía sin hacer nada y el badge del
// Set Point se quedaba congelado en el valor que traía el HTML. En pantalla el
// combo decía "0-100 %" y la etiqueta al lado "4-20 mA".
const SELECT_POR_ROL = { act: "typeAct", sen: "typeSen", sp: "typeSP" };

function updateBadge(which) {
  const sel = document.getElementById(SELECT_POR_ROL[which]);
  const b   = document.getElementById("badge-" + which);
  if (!sel || !b) return;

  b.className   = "badge " + BADGE_CLS[sel.value];
  b.textContent = BADGE_LBL[sel.value];
}


/* ==================== CONVERSIONES DE SEÑAL ==================== */

/** Convierte un valor a porcentaje (0..100) según su tipo. */
function toRange(v, t) {
  if (t === "ma") return ((v - 4) / 16) * 100;
  if (t === "v")  return (v / 10) * 100;
  return v;
}

/** Convierte un porcentaje a la escala de su tipo. */
function fromRange(p, t) {
  if (t === "ma") return 4 + (p / 100) * 16;
  if (t === "v")  return (p / 100) * 10;
  return p;
}

/**
 * Devuelve el tipo de señal usado como "referencia global" para la UI.
 * Se toma el del actuador porque es la señal de entrada del proceso.
 * "ma" | "pct" | "v"
 */
function getSignalType() {
  return document.getElementById("typeAct")?.value || "ma";
}


/* ==================== FORMATO PID ==================== */

/** Alterna la tabla PID entre formato standard (Kp,Ki,Kd) y parallel (Kp,Ti,Td). */
function setFormat(f) {
  State.ui.pidFormat = f;

  document.getElementById("fmt1")?.classList.toggle("active", f === "standard");
  document.getElementById("fmt2")?.classList.toggle("active", f === "parallel");

  if (State.identification.models.length) {
    renderPID(State.identification.models, State.identification.active);
  }
}


/* ==================== TOAST DE NOTIFICACIONES ==================== */

let _toastTimer = null;
const _TOAST_AUTO_DISMISS_MS = 3500;

/** Icono según tipo de estado. */
function _toastIconClass(cls) {
  if (cls === "ok")      return "fa-solid fa-circle-check";
  if (cls === "error")   return "fa-solid fa-circle-exclamation";
  if (cls === "warn")    return "fa-solid fa-triangle-exclamation";
  if (cls === "running") return "fa-solid fa-circle-notch fa-spin";
  return "fa-solid fa-circle-info";
}

/**
 * Muestra una notificación tipo toast centrada bajo la topbar.
 * @param {string} msg  Texto a mostrar.
 * @param {string} cls  "ok" | "running" | "error" | (nada = info)
 *
 * Comportamiento:
 *   - "ok"        → auto-dismiss a los 3.5 s.
 *   - "error"     → persistente hasta que el usuario cierre o llegue otro estado.
 *   - "running"   → persistente hasta que llegue otro estado.
 *   - Cualquier llamada nueva REEMPLAZA la anterior (así un "ok" tapa un error
 *     previo automáticamente cuando la operación se corrige).
 */
function setStatus(msg, cls) {
  const bar  = document.getElementById("statusBar");
  const span = document.getElementById("statusMsg");
  const icon = document.getElementById("statusIcon");
  if (!bar || !span) return;

  span.textContent = msg;
  bar.className    = "toast visible" + (cls ? " " + cls : "");
  if (icon) icon.className = "toast-icon " + _toastIconClass(cls);

  clearTimeout(_toastTimer);
  // "ok" y "warn" se auto-cierran. "error" y "running" persisten hasta que
  // el usuario los cierre o llegue otro estado.
  if (cls === "ok") {
    _toastTimer = setTimeout(dismissStatus, _TOAST_AUTO_DISMISS_MS);
  } else if (cls === "warn") {
    _toastTimer = setTimeout(dismissStatus, 8000);  // más largo, es info útil
  }
}

/** Oculta el toast manualmente (botón X). */
function dismissStatus() {
  const bar = document.getElementById("statusBar");
  if (bar) bar.className = "toast";
  clearTimeout(_toastTimer);
}


/* ==================== ESTADO DE CONEXIÓN (topbar) ==================== */

/** Actualiza el punto y etiqueta de conexión en la topbar. */
function setConnectionStatus(isOnline) {
  const dot = document.getElementById("apiDot");
  const lbl = document.getElementById("apiLabel");
  if (!dot || !lbl) return;

  if (isOnline) {
    dot.className    = "api-dot ok";
    lbl.textContent  = "Servidor conectado";
  } else {
    dot.className    = "api-dot error";
    lbl.textContent  = "Sin conexión";
  }
}


/* ==================== PARSE / FORMAT NUMÉRICO ==================== */

/** Parsea un CSV (separado por coma, punto y coma, espacio o salto de línea) a array numérico. */
function parseCSV(str) {
  if (!str || !str.trim()) return [];
  return str
    .split(/[\n,;\s]+/)
    .map((v) => parseFloat(v))
    .filter((v) => !isNaN(v));
}

/** Vuelca un array numérico en un textarea con formato fijo. */
function setTextareaValues(id, values, decimals = 4) {
  const el = document.getElementById(id);
  if (!el || !Array.isArray(values)) return;

  el.value = values
    .map((v) =>
      typeof v === "number" && Number.isFinite(v) ? Number(v).toFixed(decimals) : ""
    )
    .join(", ");
}

/** Lee un textarea y devuelve el arreglo de números. */
function parseTextareaNumbers(id) {
  const el = document.getElementById(id);
  if (!el) return [];
  return el.value
    .split(",")
    .map((v) => Number(v.trim()))
    .filter((v) => !Number.isNaN(v));
}

/** Devuelve el primer valor numérico finito de la lista. */
function pickNumber(...values) {
  for (const v of values) {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

/** Formatea un número con `decimals` decimales, o "—" si no es válido. */
function formatNumber(value, decimals = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(decimals);
}

/** Convierte cualquier valor a Number o null. */
function asNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Devuelve la etiqueta amigable del tipo de modelo. */
function modelTypeLabel(type) {
  switch ((type || "").toLowerCase()) {
    case "fopdt":       return "FOPDT (1er orden)";
    case "sopdt":       return "SOPDT (2do orden)";
    case "integrating": return "Integrante";
    default:            return type || "Modelo";
  }
}

/** Escapa HTML mínimo para insertar texto en innerHTML. */
function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
