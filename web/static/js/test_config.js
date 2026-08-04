/* =========================================================
   test_config.js
   Cliente de /api/test — escalas por rol y condiciones de ensayo.

   El backend es la fuente de verdad: aquí no se calcula ninguna
   conversión mA/%/V. Los combos "Tipo de señal" (paso 1) y los
   campos del escalón (paso 2) se sincronizan contra la API y los
   textos "= 25 %" vienen de `derived` en la respuesta.

   El backend NO escribe en el PLC: el escalón lo aplica el operador
   desde el ctrlX. Estos números solo dimensionan la ventana de
   identificación, el umbral de detección y el modelo a ajustar.
   ========================================================= */


/* ==================== HELPERS ==================== */

function testApiUrl(path) {
  return `${State.API_BASE}/api/test${path}`;
}

async function testApiCall(path, options = {}) {
  const response = await fetch(testApiUrl(path), options);
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    // Los .js y .html se leen del disco en cada request, pero las rutas de
    // Python se registran al arrancar. Si el servidor viene de antes de que
    // existiera /api/test, este archivo ya corre pero la ruta todavía no.
    if (response.status === 404) {
      throw new Error(
        "El backend no expone /api/test todavía. Reinicia el servidor (python main.py)."
      );
    }

    // 422 = el schema rechazó el body antes de llegar al servicio.
    if (response.status === 422 && Array.isArray(data.detail)) {
      const campos = data.detail
        .map((e) => `${(e.loc || []).slice(-1)[0]}: ${e.msg}`)
        .join(" · ");
      throw new Error(campos || "Datos inválidos");
    }

    throw new Error(data.detail || `HTTP ${response.status}`);
  }

  return data;
}

/** Evita disparar un POST por cada tecla mientras se escribe un número. */
function debounce(fn, ms = 350) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function setValueIfPresent(id, value) {
  const el = document.getElementById(id);
  if (el && value !== null && value !== undefined) el.value = value;
}


/* ==================== PINTAR LA RESPUESTA ==================== */

/** Refleja las escalas devueltas por el backend en los combos y badges. */
function applyScalesToView(payload) {
  const scales = payload?.scales || {};
  State.test.scales = { ...State.test.scales, ...scales };

  const targets = [
    { id: "typeAct", role: "actuator", badge: "act" },
    { id: "typeSen", role: "sensor",   badge: "sen" },
    { id: "typeSP",  role: "setpoint", badge: "sp"  }
  ];

  targets.forEach(({ id, role, badge }) => {
    const sel = document.getElementById(id);
    if (sel && scales[role]) sel.value = scales[role];
    updateBadge(badge);
  });
}


/** Refleja la config de ensayo y sus derivados. */
function applyStepConfigToView(step) {
  if (!step) return;

  State.test.step = step;

  setValueIfPresent("stepFrom",   step.step_from);
  setValueIfPresent("stepTo",     step.step_to);
  setValueIfPresent("duration",   step.duration_s);
  setValueIfPresent("stepDelay",  step.delay_s);
  setValueIfPresent("sampleTime", step.sample_period_s);

  const orderSel = document.getElementById("orderSel");
  if (orderSel) {
    // El combo usa números; el backend devuelve el nombre del modelo.
    const toOption = { auto: "auto", fopdt: "1", sopdt: "2", integrating: "0" };
    orderSel.value = toOption[step.order] ?? "auto";
  }

  // Los "= 25 %" los calcula el backend, no el JavaScript.
  const d = step.derived || {};
  const unit = step.actuator_scale?.unit || "";

  const fromHint = document.getElementById("stepFromPct");
  const toHint   = document.getElementById("stepToPct");

  if (fromHint) fromHint.textContent = `= ${formatNumber(d.step_from_pct, 1)} %`;
  if (toHint)   toHint.textContent   = `= ${formatNumber(d.step_to_pct, 1)} %`;

  console.log(
    `Ensayo: ${step.step_from} → ${step.step_to} ${unit} ` +
      `(Δ ${formatNumber(d.delta_pct, 1)} %) · ventana ${d.pre_samples}+${d.post_samples} muestras · ` +
      `umbral ${formatNumber(d.step_threshold_pct, 1)} % · modelo ${step.order}`
  );

  if (typeof drawStepPreview === "function") drawStepPreview();
}


/** Resume en la barra de estado qué va a hacer el backend con esta config. */
function describeStepConfig(step) {
  const d = step?.derived || {};
  const unit = step?.actuator_scale?.unit || "";

  const modelo =
    step.order === "auto"
      ? "compara FOPDT, SOPDT e integrante y elige el de mejor R²"
      : `ajusta solo ${modelTypeLabel(step.order)}`;

  return (
    `Escalón ${step.step_from} → ${step.step_to} ${unit} ` +
    `(Δ ${formatNumber(d.delta_pct, 1)} %) · ` +
    `ventana ${d.pre_samples} + ${d.post_samples} muestras · ` +
    `umbral ${formatNumber(d.step_threshold_pct, 1)} % · ${modelo}`
  );
}


/* ==================== CARGA INICIAL ==================== */

/** Trae escalas + config + catálogo en una sola llamada. */
async function loadTestConfig() {
  try {
    const data = await testApiCall("");
    State.test.available = data.available || [];
    applyScalesToView(data);
    applyStepConfigToView(data.step);
  } catch (err) {
    console.error("No se pudo cargar la configuración de ensayo:", err);
    setStatus(`No se pudo leer la configuración de ensayo: ${err.message}`, "error");
  }
}


/* ==================== ENVÍOS ==================== */

/** Manda la escala de cada rol. Las muestras nuevas ya vienen convertidas. */
async function pushScales() {
  const scales = {
    actuator: document.getElementById("typeAct")?.value,
    sensor:   document.getElementById("typeSen")?.value,
    setpoint: document.getElementById("typeSP")?.value
  };

  try {
    const data = await testApiCall("/scales", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ scales })
    });

    applyScalesToView(data);

    // Cambiar de escala mueve los límites del actuador: hay que revalidar
    // el escalón contra la nueva (8 mA es válido, 8 V no).
    await pushStepConfig();

    setStatus(
      `Escalas actualizadas — u: ${data.detail?.actuator?.label} · ` +
        `y: ${data.detail?.sensor?.label} · SP: ${data.detail?.setpoint?.label}`,
      "ok"
    );
  } catch (err) {
    console.error("Error fijando escalas:", err);
    setStatus(`No se pudieron fijar las escalas: ${err.message}`, "error");
  }
}


/** Manda las condiciones del ensayo y repinta los derivados. */
async function pushStepConfig() {
  const body = {
    step_from:       asNumber(document.getElementById("stepFrom")?.value),
    step_to:         asNumber(document.getElementById("stepTo")?.value),
    duration_s:      asNumber(document.getElementById("duration")?.value),
    delay_s:         asNumber(document.getElementById("stepDelay")?.value),
    sample_period_s: asNumber(document.getElementById("sampleTime")?.value),
    order:           document.getElementById("orderSel")?.value || "auto"
  };

  // No mandar nulls: el backend interpreta ausente como "no cambiar".
  Object.keys(body).forEach((k) => {
    if (body[k] === null || body[k] === undefined) delete body[k];
  });

  try {
    const step = await testApiCall("/config", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body)
    });

    applyStepConfigToView(step);
    markStepFieldsValid(true);
    setStatus(describeStepConfig(step), "ok");
    return step;
  } catch (err) {
    console.warn("Config de ensayo rechazada:", err.message);
    markStepFieldsValid(false);

    // Importante que se entienda que NO quedó guardada: el backend sigue
    // usando la configuración anterior para detectar e identificar.
    const vigente = State.test.step;
    const sufijo = vigente
      ? ` — sigue vigente: ${vigente.step_from} → ${vigente.step_to} ` +
        `${vigente.actuator_scale?.unit || ""}, ${vigente.duration_s} s, ` +
        `retardo ${vigente.delay_s} s.`
      : "";

    setStatus(`No se guardó: ${err.message}${sufijo}`, "error");
    return null;
  }
}


/** Marca visualmente los campos del paso 2 cuando el backend rechaza la config. */
function markStepFieldsValid(isValid) {
  ["stepFrom", "stepTo", "duration", "stepDelay"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("input-error", !isValid);
  });
}


/* ==================== CABLEADO ==================== */

const pushStepConfigDebounced = debounce(pushStepConfig, 400);

document.addEventListener("DOMContentLoaded", () => {
  // Escalas (paso 1)
  ["typeAct", "typeSen", "typeSP"].forEach((id) => {
    document.getElementById(id)?.addEventListener("change", () => pushScales());
  });

  // Condiciones de ensayo (paso 2) + tiempo de muestreo (paso 1)
  ["stepFrom", "stepTo", "duration", "stepDelay", "sampleTime"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", pushStepConfigDebounced);
  });

  // Cambiar el orden solo afecta a la PRÓXIMA identificación. Si ya hay un
  // resultado en pantalla, se recalcula al vuelo para que el cambio se vea.
  document.getElementById("orderSel")?.addEventListener("change", async () => {
    const step = await pushStepConfig();
    if (!step) return;

    if (State.identification.models.length) {
      await runIdentification(false);
    }
  });

  loadTestConfig();
});
