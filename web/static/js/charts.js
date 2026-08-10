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

  const time = store.time;

  // Actuador: la variable que el PLC REPORTA en el rol `actuator`.
  //
  // Antes esto leía `store.actuator_ideal`, una clave que no existe en
  // `sampleStore` y que nadie llenaba nunca: el gráfico dibujaba `undefined`
  // en todos los puntos. Y aunque hubiera existido, mostrar el escalón ideal
  // aquí hace que cambiar la variable mapeada no tenga ningún efecto visible
  // y oculta justo lo que hay que ver: si el actuador obedeció.
  //
  // El escalón comandado se dibuja punteado detrás, vía buildPlanOverlay.
  const actuator = type === "pct" ? store.actuator_pct : store.actuator_ma;

  // Sensor: chart muestra los valores REALES del PLC.
  const sensor = type === "pct" ? store.sensor_pct : store.sensor_ma;

  const actData = time.map((t, i) => ({ x: t, y: actuator[i] }));
  const senData = time.map((t, i) => ({ x: t, y: sensor[i] }));

  drawTimeSeries("cAct", actData, "#2a78d6", durationS, buildPlanOverlay(type));
  drawTimeSeries("cSen", senData, "#1baf7a", durationS);
}


/**
 * Línea objetivo del actuador: el perfil COMPLETO que mandó `test_started`.
 *
 * Se dibuja punteada y por debajo de la señal capturada. La comparación entre
 * las dos es el diagnóstico más útil de la vista:
 *
 *   punteada = lo que el backend COMANDA
 *   sólida   = lo que el PLC REPORTA en la variable mapeada
 *
 * Si la sólida no sigue a la punteada, o la escritura no está armada, o el
 * actuador saturó, o esa variable la pisa el propio programa del PLC.
 */
function buildPlanOverlay(type) {
  const plan = State.ensayo?.plan;
  if (!plan || !Array.isArray(plan.time) || !plan.time.length) return null;

  const values = type === "pct" ? plan.actuator_pct : plan.actuator;
  if (!Array.isArray(values)) return null;

  return {
    data: plan.time.map((t, i) => ({ x: t, y: values[i] })),
    borderColor: "rgba(42,120,214,0.35)",
    borderWidth: 1.5,
    borderDash: [6, 4],
    pointRadius: 0,
    tension: 0
  };
}


/**
 * Helper interno — crea/actualiza un chart line con eje X lineal fijo.
 * `overlay` es un dataset opcional que se pinta DEBAJO de `data`.
 */
function drawTimeSeries(canvasId, data, color, maxX, overlay = null) {
  const el = document.getElementById(canvasId);
  if (!el) return;

  if (State.charts[canvasId]) State.charts[canvasId].destroy();

  // El overlay va primero para que quede detrás de la serie capturada.
  const datasets = [];
  if (overlay) datasets.push(overlay);
  datasets.push({
    data,
    borderColor: color,
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.1
  });

  State.charts[canvasId] = new Chart(el, {
    type: "line",
    data: { datasets },
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

  // Las tres series tienen que cubrir el MISMO tramo. Si se recorre `time` con
  // una serie más corta, `serie[i]` es undefined a partir de cierto punto y
  // Number(undefined) da NaN: Chart.js lo salta sin quejarse y sale un gráfico
  // creíble pero falso —una curva aplastada contra el arranque del eje—.
  // Recortar a la longitud común deja el desajuste a la vista en vez de
  // maquillarlo.
  const n = Math.min(
    time?.length || 0,
    measured?.length || 0,
    simulated?.length || 0
  );

  if (!n) return;

  if (time.length !== simulated.length || measured.length !== simulated.length) {
    console.warn(
      `plotComparison: series de distinta longitud (tiempo ${time.length}, ` +
        `medido ${measured.length}, modelo ${simulated.length}). Se recorta a ${n}. ` +
        `El modelo cubre solo la ventana de identificación: hay que graficarlo ` +
        `contra esa ventana, no contra el buffer completo.`
    );
  }

  const eje = time.slice(0, n);
  const puntos = (serie) =>
    eje.map((t, i) => ({ x: Number(t), y: Number(serie[i]) }));

  // Rango completo, para poder volver a él tras hacer zoom.
  const tMin = Number(eje[0]);
  const tMax = Number(eje[n - 1]);
  State.charts.cCmpRango = { min: tMin, max: tMax };

  State.charts.cCmp = new Chart(el, {
    type: "line",
    data: {
      datasets: [
        {
          label: "Medido",
          data: puntos(measured),
          borderColor: "#1baf7a",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.1
        },
        {
          label: "Modelo",
          data: puntos(simulated),
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
      parsing: false,
      plugins: {
        legend: { display: false },
        tooltip: { mode: "nearest", intersect: false }
      },
      scales: {
        // Escala lineal, no de categorías: con las etiquetas como categorías
        // el zoom solo podría cortar por índice de muestra, y aquí interesa
        // acotar por SEGUNDOS — es lo que se compara contra el modelo.
        x: {
          type: "linear",
          min: tMin,
          max: tMax,
          ticks: {
            color: "#898781",
            font: { size: 8 },
            maxTicksLimit: 6,
            callback: (v) => Number(v).toFixed(1)
          },
          grid: { color: "rgba(0,0,0,0.05)" }
        },
        y: {
          ticks: { color: "#898781", font: { size: 8 } },
          grid:  { color: "rgba(0,0,0,0.05)" }
        }
      }
    }
  });

  activarZoomComparacion(el);
}


/* ==================== ZOOM DEL CHART MEDIDO VS MODELO ==================== */
/**
 * Zoom y desplazamiento sobre el eje de tiempo, sin plugins.
 *
 * Se implementa a mano en vez de con chartjs-plugin-zoom porque la app corre
 * embebida en el ctrlX, que no siempre tiene salida a internet: una dependencia
 * por CDN se caería justo en el equipo donde tiene que funcionar.
 *
 * - Rueda           → acerca/aleja alrededor del cursor
 * - Arrastrar       → desplaza
 * - Doble clic      → vuelve al rango completo
 *
 * Con un ensayo largo importado desde CSV, el escalón ocupa una fracción mínima
 * del ancho y es imposible juzgar a ojo si el modelo sigue a la planta.
 */
function activarZoomComparacion(canvas) {
  if (canvas.dataset.zoomActivo === "1") return;
  canvas.dataset.zoomActivo = "1";

  const eje = () => State.charts.cCmp?.options?.scales?.x;

  const aplicar = (min, max) => {
    const chart = State.charts.cCmp;
    const rango = State.charts.cCmpRango;
    if (!chart || !rango) return;

    const anchoTotal = rango.max - rango.min;
    // Un tope de acercamiento evita quedar entre dos muestras, donde no se ve
    // ninguna línea y parece que el gráfico se rompió.
    const anchoMin = anchoTotal / 500;

    if (max - min < anchoMin) return;

    chart.options.scales.x.min = Math.max(rango.min, min);
    chart.options.scales.x.max = Math.min(rango.max, max);
    chart.update("none");
  };

  canvas.addEventListener(
    "wheel",
    (ev) => {
      const chart = State.charts.cCmp;
      const escala = eje();
      if (!chart || !escala) return;

      ev.preventDefault();

      const area = chart.chartArea;
      if (!area) return;

      const min = escala.min;
      const max = escala.max;

      // Punto bajo el cursor: es el que debe quedarse quieto al hacer zoom.
      const rect = canvas.getBoundingClientRect();
      const frac = Math.min(
        Math.max((ev.clientX - rect.left - area.left) / area.width, 0),
        1
      );
      const centro = min + frac * (max - min);

      const factor = ev.deltaY < 0 ? 0.8 : 1.25;

      aplicar(
        centro - (centro - min) * factor,
        centro + (max - centro) * factor
      );
    },
    { passive: false }
  );

  let arrastrando = false;
  let xInicial = 0;
  let rangoInicial = null;

  canvas.addEventListener("mousedown", (ev) => {
    const escala = eje();
    if (!escala) return;

    arrastrando = true;
    xInicial = ev.clientX;
    rangoInicial = { min: escala.min, max: escala.max };
    canvas.style.cursor = "grabbing";
  });

  window.addEventListener("mousemove", (ev) => {
    if (!arrastrando || !rangoInicial) return;

    const chart = State.charts.cCmp;
    const area = chart?.chartArea;
    if (!area) return;

    const ancho = rangoInicial.max - rangoInicial.min;
    const desplazamiento = ((ev.clientX - xInicial) / area.width) * ancho;

    aplicar(rangoInicial.min - desplazamiento, rangoInicial.max - desplazamiento);
  });

  window.addEventListener("mouseup", () => {
    arrastrando = false;
    rangoInicial = null;
    canvas.style.cursor = "";
  });

  canvas.addEventListener("dblclick", () => resetZoomComparacion());
}


/** Devuelve el chart medido-vs-modelo a su rango completo. */
function resetZoomComparacion() {
  const chart = State.charts.cCmp;
  const rango = State.charts.cCmpRango;
  if (!chart || !rango) return;

  chart.options.scales.x.min = rango.min;
  chart.options.scales.x.max = rango.max;
  chart.update("none");
}
