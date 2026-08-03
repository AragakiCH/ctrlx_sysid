/* =========================================================
   pid.js
   Rendering de la sintonía PID (paso 5).
   - Tabs por modelo (FOPDT / SOPDT / Integrante).
   - Tabla con métodos de sintonía (IMC, Ziegler-Nichols, Cohen-Coon, SIMC).
   - Toggle standard (Kp,Ki,Kd) vs parallel (Kp,Ti,Td).
   Cuando el backend no envía sintonías, se calcula localmente
   con `tunePIDLocal` (mismas fórmulas que ctrlx_final).
   ========================================================= */


/* ==================== RENDER PRINCIPAL ==================== */
/**
 * @param {Object[]} models    modelos normalizados
 * @param {number}   modelIdx  índice del modelo activo
 */
function renderPID(models, modelIdx = 0) {
  if (!models?.length) return;

  document.getElementById("emptyPID").style.display = "none";
  const pc = document.getElementById("pidContent");
  pc.style.display = "flex";
  pc.style.flexDirection = "column";

  // Tabs por modelo
  const tabs = document.getElementById("pidModelTabs");
  tabs.innerHTML = models
    .map(
      (r, i) => `
        <div class="pid-mtab${i === modelIdx ? " active" : ""}"
             onclick="switchPIDModel(${i})">
          ${modelTypeLabel(r.model_type)} — R² ${formatNumber(r.fit_quality, 1)}%
        </div>
      `
    )
    .join("");

  const r = models[modelIdx];

  // Convertir arreglo de pid_tunings → objeto por método
  const tuning =
    r.pid_tunings?.length
      ? Object.fromEntries(r.pid_tunings.map((t) => [t.method || t.name || "—", t]))
      : tunePIDLocal(r);

  fillPIDTable(tuning);

  // Info box
  const L   = Number(r.dead_time ?? 0).toFixed(4);
  const tau = Number(r.tau ?? r.tau1 ?? 0).toFixed(4);
  const K   = Number(r.gain ?? 0).toFixed(4);

  document.getElementById("pidInfoBox").innerHTML = `
    <strong>${modelTypeLabel(r.model_type)}</strong>
    — K = ${K} | τ = ${tau} s | L = ${L} s<br>
    <span style="color:var(--text3)">
      Se recomienda IMC/Lambda para mayor robustez ante perturbaciones.
      Configure el bloque PID en ctrlX AXCS con los parámetros de la tabla
      y active el anti-windup.
    </span>
  `;
}


/** Cambia el modelo activo en la tabla de PID. */
function switchPIDModel(i) {
  State.identification.active = i;
  if (State.identification.models.length) {
    renderPID(State.identification.models, i);
  }
}


/* ==================== TABLA ==================== */
/**
 * Rellena el tbody `#pidBody` con las filas del tuning.
 * Cambia dinámicamente los encabezados según `State.ui.pidFormat`.
 */
function fillPIDTable(tuning) {
  const std = State.ui.pidFormat === "standard";

  document.getElementById("col1").textContent = "Kp";
  document.getElementById("col2").textContent = std ? "Ki" : "Ti (s)";
  document.getElementById("col3").textContent = std ? "Kd" : "Td (s)";

  const body = document.getElementById("pidBody");
  body.innerHTML = "";

  Object.entries(tuning).forEach(([m, p]) => {
    const kp = pickNumber(p.Kp, p.kp) ?? 0;
    const ki = pickNumber(p.Ki, p.ki);
    const kd = pickNumber(p.Kd, p.kd);
    const ti = pickNumber(p.Ti, p.ti);
    const td = pickNumber(p.Td, p.td);

    const v2 = std
      ? (ki != null ? ki : kp / Math.max(ti || 0.001, 0.001))
      : ti;
    const v3 = std
      ? (kd != null ? kd : kp * (td || 0))
      : td;

    const desc = p.description || p.desc || "—";

    body.innerHTML += `
      <tr>
        <td class="td-method">${escapeHtml(m)}</td>
        <td class="td-val">${formatNumber(kp, 4)}</td>
        <td class="td-val">${formatNumber(v2, 4)}</td>
        <td class="td-val">${formatNumber(v3, 4)}</td>
        <td class="td-desc">${escapeHtml(desc)}</td>
      </tr>
    `;
  });
}


/* ==================== SINTONÍA LOCAL (fallback) ==================== */
/**
 * Calcula sintonías PID clásicas para un modelo, sin depender del backend.
 * Mismas fórmulas que la versión standalone (ctrlx_final).
 */
function tunePIDLocal(r) {
  const K   = Number(r.gain ?? r.K ?? 1);
  const L   = Math.max(Number(r.dead_time ?? r.delay ?? r.L ?? 0), 0.001);
  const tau = Number(r.tau  ?? r.tau1 ?? 1);
  const t1  = Number(r.tau1 ?? tau);
  const t2  = Number(r.tau2 ?? tau * 0.5);

  const isSOPDT = (r.model_type || "").toLowerCase() === "sopdt";
  const tE  = isSOPDT ? t1 + t2 : tau;
  const lam = Math.max(0.25 * tE, 0.8 * L);

  const imcKp = tE / (K * (lam + L));
  const znKp  = 1.2 / ((K / tE) * L);
  const ccKp  = (1.35 / (K * (L / tE))) * (1 + 0.18 * (L / tE) / (1 + 0.185 * (L / tE)));
  const simcKp = tE / (K * 2 * L);

  return {
    "IMC": {
      Kp: +imcKp.toFixed(4),
      Ti: +tE.toFixed(4),
      Td: 0,
      Ki: +(imcKp / tE).toFixed(4),
      Kd: 0,
      description: "IMC / Lambda — robusto, recomendado para procesos lentos"
    },
    "Ziegler-Nichols": {
      Kp: +znKp.toFixed(4),
      Ti: +(2 * L).toFixed(4),
      Td: +(0.5 * L).toFixed(4),
      Ki: +(znKp / (2 * L)).toFixed(4),
      Kd: +(znKp * 0.5 * L).toFixed(4),
      description: "ZN lazo abierto — agresivo, buena velocidad de respuesta"
    },
    "Cohen-Coon": {
      Kp: +ccKp.toFixed(4),
      Ti: +Math.max(2.5 * L, 0.01).toFixed(4),
      Td: +(0.37 * L).toFixed(4),
      Ki: 0,
      Kd: 0,
      description: "Cohen-Coon — buen balance entre rapidez y estabilidad"
    },
    "SIMC": {
      Kp: +simcKp.toFixed(4),
      Ti: +Math.min(tE, 4 * L).toFixed(4),
      Td: 0,
      Ki: +(simcKp / Math.min(tE, 4 * L)).toFixed(4),
      Kd: 0,
      description: "SIMC (Skogestad) — excelente rechazo de perturbaciones"
    }
  };
}
