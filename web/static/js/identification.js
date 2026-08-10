/* =========================================================
   identification.js
   Rendering de la sección "Identificación" (paso 4).
   - Barra de métricas (Mejor modelo, R², K, L).
   - Función de transferencia y comparación medido vs modelo.
   - Cards de modelos alternativos.
   Normaliza los modelos que llegan del backend por WebSocket.
   ========================================================= */


/* ==================== NORMALIZACIÓN ==================== */
/**
 * El backend envía R² en escala 0-1 (ver README, "Payload de identificación"),
 * pero toda la UI lo muestra con el sufijo "%". Se convierte aquí, una sola vez,
 * porque todos los renderers leen de State.identification.models.
 *
 * Puede ser negativo: un modelo que ajusta peor que la media da R² < 0. Es el
 * caso típico del modelo integrador sobre un proceso autorregulado, y el signo
 * es información útil, así que no se recorta.
 */
function r2ToPercent(value) {
  const n = asNumber(value);
  return n === null ? null : n * 100;
}

/**
 * Convierte un modelo crudo (del backend o de otra fuente) a la forma canónica
 * que usan el resto de renderers.
 */
function normalizeModelResult(model) {
  return {
    model_type:  model.model_type || model.method || "unknown",
    fit_quality: r2ToPercent(model.fit_quality),
    gain:        asNumber(model.gain ?? model.K),
    dead_time:   asNumber(model.dead_time ?? model.delay ?? model.L),
    tau:         asNumber(model.tau),
    tau1:        asNumber(model.tau1),
    tau2:        asNumber(model.tau2),
    tf_string:   model.tf_string || buildTfString(model),
    pid_tunings: Array.isArray(model.pid_tunings)
      ? model.pid_tunings
      : model.pid_tuning
        ? Object.entries(model.pid_tuning).map(([m, p]) => ({ method: m, ...p }))
        : [],
    simulated:   Array.isArray(model.simulated) ? model.simulated : null
  };
}

/** Reconstruye una cadena representativa de G(s) si el backend no la envió. */
function buildTfString(m) {
  const K = Number(m.gain ?? m.K ?? 1);
  const L = Number(m.dead_time ?? m.delay ?? m.L ?? 0);
  const type = (m.model_type || m.method || "").toLowerCase();

  const delay = L > 0 ? `·e^(-${L.toFixed(3)}s)` : "";

  if (type === "fopdt") {
    const tau = Number(m.tau ?? 1);
    return `${K.toFixed(3)}${delay} / (${tau.toFixed(3)}s + 1)`;
  }
  if (type === "sopdt") {
    const t1 = Number(m.tau1 ?? 1);
    const t2 = Number(m.tau2 ?? 1);
    return `${K.toFixed(3)}${delay} / ((${t1.toFixed(3)}s+1)(${t2.toFixed(3)}s+1))`;
  }
  if (type === "integrating") {
    return `${K.toFixed(3)}${delay} / s`;
  }
  return `${K}`;
}


/* ==================== RENDERING ==================== */
/**
 * Puebla toda la sección de identificación (paso 4).
 * @param {Object[]} models  arreglo de modelos normalizados
 * @param {number[]} yPct    señal medida (sensor, en %) para el chart comparativo
 * @param {number[]} time    vector de tiempo asociado a la señal medida
 * @param {string|null} winnerType  "fopdt" | "sopdt" | "integrating" | null
 */
function renderIdent(models, yPct, time, winnerType) {
  if (!models?.length) return;

  const winner =
    (winnerType && models.find((m) => m.model_type === winnerType)) || models[0];

  // Ocultar empty / mostrar contenido
  document.getElementById("emptyIdent").style.display = "none";
  document.getElementById("metricsRow").style.display = "";

  const bs = document.getElementById("bestSection");
  bs.style.display = "flex";
  bs.style.flexDirection = "column";

  document.getElementById("btnToPID").style.display = "";

  // Métricas
  document.getElementById("mModel").textContent = modelTypeLabel(winner.model_type);
  document.getElementById("mFit").textContent   = formatNumber(winner.fit_quality, 1) + "%";
  document.getElementById("mK").textContent     = formatNumber(winner.gain, 4);
  document.getElementById("mL").textContent     = formatNumber(winner.dead_time, 4) + "s";

  // Función de transferencia
  renderTransferFunction(winner);

  // Chart medido vs modelo (solo si tenemos curva simulada)
  if (Array.isArray(winner.simulated) && winner.simulated.length && time?.length) {
    plotComparison(time, yPct, winner.simulated);
  }

  // Cards de modelos alternativos
  renderAltCards(models, winner.model_type);
}


/** Escribe numerador, denominador y término de retardo en la caja de G(s). */
function renderTransferFunction(m) {
  const K = Number(m.gain ?? 0);
  const L = Number(m.dead_time ?? 0);
  const type = (m.model_type || "").toLowerCase();

  document.getElementById("tfNum").textContent = K.toFixed(4);

  let den;
  if (type === "fopdt")            den = `${Number(m.tau).toFixed(4)}s + 1`;
  else if (type === "sopdt")       den = `(${Number(m.tau1).toFixed(3)}s+1)(${Number(m.tau2).toFixed(3)}s+1)`;
  else if (type === "integrating") den = "s";
  else                             den = "1";

  document.getElementById("tfDen").textContent = den;
  document.getElementById("tfDelay").textContent =
    L > 0 ? `× e^(−${L.toFixed(4)}s)` : "";
}


/** Cards apiladas con cada modelo alternativo (clic para seleccionar).
 *  Marca visualmente:
 *    - `.winner` → la ganadora por R² (badge "MEJOR"), no cambia con la selección.
 *    - `.sel`    → la card actualmente seleccionada por el usuario (empieza como la ganadora).
 */
function renderAltCards(models, winnerType) {
  const ac = document.getElementById("altCards");
  if (!ac) return;

  const activeIdx = State.identification.active ?? 0;

  ac.innerHTML = models
    .map((r, i) => {
      const K = Number(r.gain ?? 0).toFixed(3);
      const L = Number(r.dead_time ?? 0).toFixed(2);
      const type = (r.model_type || "").toLowerCase();

      let tf;
      if (type === "fopdt")            tf = `${K} / (${Number(r.tau).toFixed(1)}s+1)`;
      else if (type === "sopdt")       tf = `${K} / ((${Number(r.tau1).toFixed(1)}s+1)(${Number(r.tau2).toFixed(1)}s+1))`;
      else if (type === "integrating") tf = `${K} / s`;
      else                             tf = `${K}`;

      if (parseFloat(L) > 0) tf += `·e^(-${L}s)`;

      const isWinner = r.model_type === winnerType;
      const isActive = i === activeIdx;

      const cls = [
        "alt-card",
        isWinner ? "winner" : "",
        isActive ? "sel"    : ""
      ].filter(Boolean).join(" ");

      return `
        <div class="${cls}" onclick="selectAlt(${i})">
          <div class="alt-method">
            <span>${modelTypeLabel(r.model_type)}</span>
            ${isWinner ? '<span class="winner-badge">MEJOR</span>' : ""}
          </div>
          <div class="alt-r2">R² ${formatNumber(r.fit_quality, 1)}%</div>
          <div class="alt-tf">${tf}</div>
        </div>
      `;
    })
    .join("");
}


/**
 * Al seleccionar un modelo alternativo, todo el panel superior
 * (métricas, función de transferencia, chart Medido-vs-Modelo, Bode)
 * y la tabla PID se recalculan para ese modelo.
 * El "MEJOR" (badge en la card ganadora) queda anclado al winner por R²,
 * no se mueve con la selección.
 */
function selectAlt(i) {
  const models = State.identification.models;
  if (!models.length) return;

  const selected = models[i];
  if (!selected) return;

  State.identification.active = i;

  // 1. Selección visual en las alt-cards
  document.querySelectorAll(".alt-card").forEach((c, j) => c.classList.toggle("sel", j === i));

  // 2. Métricas del top
  document.getElementById("mModel").textContent = modelTypeLabel(selected.model_type);
  document.getElementById("mFit").textContent   = formatNumber(selected.fit_quality, 1) + "%";
  document.getElementById("mK").textContent     = formatNumber(selected.gain, 4);
  document.getElementById("mL").textContent     = formatNumber(selected.dead_time, 4) + "s";

  // 3. Caja G(s)
  renderTransferFunction(selected);

  // 4. Chart Medido vs Modelo.
  //
  // Se dibuja sobre la MISMA ventana con la que se ajustó, guardada al llegar
  // el resultado. Antes se usaba el sampleStore completo: `simulated` cubre
  // solo la ventana (unas decenas de muestras, con el tiempo re-basado a 0) y
  // el buffer dura todo el ensayo, así que Chart.js emparejaba por índice y el
  // modelo salía aplastado contra el arranque del eje. El primer render se
  // veía bien porque ese sí usaba la ventana; se descuadraba en cuanto se
  // tocaba una card de modelo alternativo.
  if (Array.isArray(selected.simulated) && selected.simulated.length) {
    const ventana = State.identification.window;

    if (ventana?.time?.length) {
      plotComparison(ventana.time, ventana.sensor, selected.simulated);
    } else {
      // Sin ventana guardada (resultado viejo, o llegó sin `window`) se cae al
      // buffer, pero recortado a lo que cubre el modelo para no desalinear.
      const s = State.sampleStore;
      const type = getSignalType();
      const measured = type === "pct" ? s.sensor_pct : s.sensor_ma;
      const n = Math.min(s.time.length, selected.simulated.length);

      if (n) {
        plotComparison(s.time.slice(0, n), measured.slice(0, n), selected.simulated.slice(0, n));
      }
    }
  }

  // 5. Diagrama de Bode
  if (typeof renderBode === "function") renderBode(selected);

  // 6. Sintonía PID
  renderPID(models, i);
}
