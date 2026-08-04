/* =========================================================
   charts.js
   Rendering de todos los gráficos Chart.js del wizard.
   - Paso 2: vista previa del escalón (canvas 2D nativo).
   - Paso 3: cAct (actuador) y cSen (sensor) en tiempo real.
   - Paso 4: cCmp (medido vs modelo).
   ========================================================= */


/* ==================== PASO 2 · PREVIEW DEL ESCALÓN ==================== */
/**
 * Dibuja en `#stepPreview` un escalón según los valores de:
 * `typeAct`, `stepFrom`, `stepTo`, `duration`, `stepDelay`.
 * Se dibuja con canvas 2D — no usa Chart.js.
 */
function drawStepPreview() {
  const c = document.getElementById("stepPreview");
  if (!c) return;

  const ctx = c.getContext("2d");
  c.width  = c.parentElement.offsetWidth || 500;
  c.height = c.parentElement.offsetHeight || 100;

  const W = c.width, H = c.height, P = 22;
  ctx.clearRect(0, 0, W, H);

  const t    = getSignalType();
  const from = parseFloat(document.getElementById("stepFrom").value)  || 8;
  const to   = parseFloat(document.getElementById("stepTo").value)    || 12;
  const dur  = parseFloat(document.getElementById("duration").value)  || 120;
  const del  = parseFloat(document.getElementById("stepDelay").value) || 10;

  const mx = t === "ma" ? 20 : t === "v" ? 10 : 100;
  const mn = t === "ma" ? 4  : 0;

  const norm = (v) => H - P - ((v - mn) / (mx - mn)) * (H - 2 * P);
  const tx   = (x) => P + (x / dur) * (W - 2 * P);

  // Reflejar el % equivalente
  const fromPct = document.getElementById("stepFromPct");
  const toPct   = document.getElementById("stepToPct");
  if (fromPct) fromPct.textContent = "= " + toRange(from, t).toFixed(0) + " %";
  if (toPct)   toPct.textContent   = "= " + toRange(to, t).toFixed(0) + " %";

  // Grilla
  ctx.strokeStyle = "rgba(0,0,0,0.06)";
  ctx.lineWidth   = 0.5;
  [0, 0.25, 0.5, 0.75, 1].forEach((f) => {
    ctx.beginPath();
    ctx.moveTo(P, H - P - f * (H - 2 * P));
    ctx.lineTo(W - P, H - P - f * (H - 2 * P));
    ctx.stroke();
  });

  // Área de retardo
  ctx.fillStyle = "rgba(42,120,214,0.06)";
  ctx.fillRect(tx(0), norm(to), tx(del) - tx(0), norm(from) - norm(to));

  // Línea de retardo (punteada)
  ctx.setLineDash([4, 3]);
  ctx.strokeStyle = "rgba(42,120,214,0.3)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(tx(del), P);
  ctx.lineTo(tx(del), H - P);
  ctx.stroke();
  ctx.setLineDash([]);

  // Curva del escalón
  ctx.strokeStyle = "#2a78d6";
  ctx.lineWidth = 2.5;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(tx(0),   norm(from));
  ctx.lineTo(tx(del), norm(from));
  ctx.lineTo(tx(del), norm(to));
  ctx.lineTo(tx(dur), norm(to));
  ctx.stroke();

  // Etiquetas
  const u = t === "ma" ? "mA" : t === "v" ? "V" : "%";
  ctx.fillStyle = "#898781";
  ctx.font = "10px system-ui";
  ctx.fillText(from.toFixed(1) + " " + u, P + 4, norm(from) - 4);
  ctx.fillText(to.toFixed(1)   + " " + u, P + 4, norm(to)   - 4);

  ctx.fillStyle = "#185FA5";
  ctx.font = "9px system-ui";
  ctx.fillText(del + "s", tx(del) + 4, H - P - 4);
}


/* ==================== PASO 3 · CAPTURA EN TIEMPO REAL ==================== */
/**
 * Pinta los canvas cAct y cSen con la ventana del ensayo.
 * Eje X: FIJO de 0 a `durationS` (segundos), no rueda.
 * Los puntos se posicionan por su tiempo relativo al inicio del ensayo,
 * que se guarda en `sampleStore.time` (segundos desde Inicio).
 *
 * Se llama:
 *   - Al iniciar un ensayo (chart vacío con eje 0..durationS).
 *   - Cada vez que llega una muestra dentro de la ventana del ensayo.
 */
function plotCapture() {
  const store = State.sampleStore;
  const type  = getSignalType();

  const durationS =
    Number(State.ensayo?.durationS) ||
    Number(State.test?.step?.duration_s) ||
    120;

  const time     = store.time;
  const actuator = type === "pct" ? store.actuator_pct : store.actuator_ma;
  const sensor   = type === "pct" ? store.sensor_pct   : store.sensor_ma;

  const actData = time.map((t, i) => ({ x: t, y: actuator[i] }));
  const senData = time.map((t, i) => ({ x: t, y: sensor[i] }));

  drawTimeSeries("cAct", actData, "#2a78d6", durationS);
  drawTimeSeries("cSen", senData, "#1baf7a", durationS);
}

/** Helper interno — crea/actualiza un chart line con eje X lineal fijo. */
function drawTimeSeries(canvasId, data, color, maxX) {
  const el = document.getElementById(canvasId);
  if (!el) return;

  if (State.charts[canvasId]) State.charts[canvasId].destroy();

  State.charts[canvasId] = new Chart(el, {
    type: "line",
    data: {
      datasets: [{
        data,
        borderColor: color,
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.1
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      parsing: false,  // data ya viene como {x,y}
      plugins: { legend: { display: false } },
      scales: {
        x: {
          type: "linear",
          min: 0,
          max: maxX,
          title: {
            display: true,
            text: "Tiempo (s)",
            color: "#898781",
            font: { size: 10 }
          },
          ticks: { color: "#898781", font: { size: 9 }, maxTicksLimit: 12 },
          grid:  { color: "rgba(0,0,0,0.05)" }
        },
        y: {
          ticks: { color: "#898781", font: { size: 9 } },
          grid:  { color: "rgba(0,0,0,0.05)" }
        }
      }
    }
  });
}


/* ==================== PASO 4 · MEDIDO VS MODELO ==================== */
/**
 * Dibuja en `#cCmp` la comparación entre la curva medida y la simulada.
 */
function plotComparison(time, measured, simulated) {
  const el = document.getElementById("cCmp");
  if (!el) return;

  if (State.charts.cCmp) State.charts.cCmp.destroy();

  State.charts.cCmp = new Chart(el, {
    type: "line",
    data: {
      labels: time.map((v) => Number(v).toFixed(1)),
      datasets: [
        {
          label: "Medido",
          data: measured,
          borderColor: "#1baf7a",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.1
        },
        {
          label: "Modelo",
          data: simulated,
          borderColor: "#eb6834",
          borderWidth: 2,
          borderDash: [5, 3],
          pointRadius: 0,
          tension: 0.1
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: "#898781", font: { size: 8 }, maxTicksLimit: 6 },
          grid:  { color: "rgba(0,0,0,0.05)" }
        },
        y: {
          ticks: { color: "#898781", font: { size: 8 } },
          grid:  { color: "rgba(0,0,0,0.05)" }
        }
      }
    }
  });
}
