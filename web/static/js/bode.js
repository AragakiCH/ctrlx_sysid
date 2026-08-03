/* =========================================================
   bode.js
   Diagrama de Bode (magnitud y fase) para el modelo ganador.
   Renderiza en los canvas `cBodeMag` y `cBodePhase`.
   ========================================================= */


/**
 * Renderiza el diagrama de Bode a partir de un modelo identificado.
 * Soporta FOPDT, SOPDT e Integrante.
 * @param {Object} r modelo normalizado (ver identification.js)
 */
function renderBode(r) {
  if (!r) return;

  document.getElementById("emptyBode").style.display = "none";
  const bc = document.getElementById("bodeCharts");
  bc.style.display = "flex";
  bc.style.flexDirection = "column";

  const K    = Number(r.gain ?? r.K ?? 1);
  const L    = Number(r.dead_time ?? r.delay ?? r.L ?? 0);
  const tau  = Number(r.tau  ?? r.tau1 ?? 10);
  const tau2 = Number(r.tau2 ?? 0);
  const modelType = (r.model_type || r.method || "").toLowerCase();

  // Frecuencias logarítmicas de 1e-3 a 1e2 rad/s
  const freqs = Array.from({ length: 200 }, (_, i) => Math.pow(10, -3 + (5 * i) / 199));
  const mag = [];
  const phase = [];

  freqs.forEach((w) => {
    let M, P;

    if (modelType === "fopdt") {
      M = K / Math.sqrt(1 + (w * tau) ** 2);
      P = -Math.atan(w * tau) - w * L;
    } else if (modelType === "sopdt") {
      M = K / (Math.sqrt(1 + (w * tau) ** 2) * Math.sqrt(1 + (w * tau2) ** 2));
      P = -Math.atan(w * tau) - Math.atan(w * tau2) - w * L;
    } else if (modelType === "integrating") {
      M = K / w;
      P = -Math.PI / 2 - w * L;
    } else {
      M = K;
      P = 0;
    }

    mag.push(20 * Math.log10(Math.max(Math.abs(M), 1e-12)));
    phase.push((P * 180) / Math.PI);
  });

  const labels = freqs.map((v) => v.toExponential(1));

  drawBodeChart("cBodeMag",   labels, mag,   "#2a78d6", "Magnitud (dB)");
  drawBodeChart("cBodePhase", labels, phase, "#eb6834", "Fase (°)");
}


/** Helper interno — crea/actualiza un chart de Bode (magnitud o fase). */
function drawBodeChart(canvasId, labels, data, color, yLabel) {
  const el = document.getElementById(canvasId);
  if (!el) return;

  if (State.charts[canvasId]) State.charts[canvasId].destroy();

  State.charts[canvasId] = new Chart(el, {
    type: "line",
    data: {
      labels,
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
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: "#898781", font: { size: 9 }, maxTicksLimit: 10 },
          grid:  { color: "rgba(0,0,0,0.05)" },
          title: { display: true, text: "Frecuencia (rad/s)", color: "#898781", font: { size: 10 } }
        },
        y: {
          ticks: { color: "#898781", font: { size: 9 } },
          grid:  { color: "rgba(0,0,0,0.05)" },
          title: { display: true, text: yLabel, color: "#898781", font: { size: 10 } }
        }
      }
    }
  });
}
