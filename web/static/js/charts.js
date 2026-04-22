function getSignalUnit() {
  const sigType = document.getElementById("signalType")?.value;
  return sigType === "ma" ? "mA" : "%";
}

const crosshairPlugin = {
  id: "crosshair",

  afterDraw(chart) {
    if (!chart.tooltip?._active?.length) return;

    const ctx = chart.ctx;
    const activePoint = chart.tooltip._active[0];
    const x = activePoint.element.x;

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, chart.chartArea.top);
    ctx.lineTo(x, chart.chartArea.bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = "#999";
    ctx.setLineDash([4, 4]);
    ctx.stroke();
    ctx.restore();
  },
};

// ==================== RAW DATA ====================
function plotRawData(time, actuator, sensor, setpoint = []) {
  const unit = getSignalUnit();
  const signalType = document.getElementById("signalType")?.value;
  const sensorColor = signalType === "percent" ? "#ff9800" : "#1a9e5c";
  const actuatorColor = signalType === "percent" ? "#ff9800" : "#1a9e5c";

  const makeConfig = (label, data, color, yLabel, withSetpoint) => ({
    type: "line",
    data: {
      labels: time.map((v) => Number(v).toFixed(2)),
      datasets: [
        {
          label,
          data,
          borderColor: color,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.12,
          fill: false,
        },
        ...(withSetpoint && setpoint?.length
          ? [
              {
                label: "Set Point",
                data: setpoint,
                borderColor: "#c47a00",
                borderWidth: 1.5,
                borderDash: [6, 4],
                pointRadius: 0,
                tension: 0,
                fill: false,
              },
            ]
          : []),
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      plugins: {
        legend: { display: false },
      },
      scales: {
        x: {
          ticks: {
            color: "#7a8fa0",
            font: { family: "IBM Plex Mono", size: 9 },
            maxTicksLimit: 10,
          },
          grid: { color: "rgba(160,180,200,0.25)" },
          title: {
            display: true,
            text: "Tiempo (s)",
            color: "#7a8fa0",
            font: { family: "IBM Plex Sans", size: 9 },
          },
        },
        y: {
          ticks: {
            color: "#7a8fa0",
            font: { family: "IBM Plex Mono", size: 9 },
          },
          grid: { color: "rgba(160,180,200,0.25)" },
          title: {
            display: true,
            text: yLabel,
            color: "#7a8fa0",
            font: { family: "IBM Plex Sans", size: 9 },
          },
        },
      },
    },
  });

  // if (window.State?.charts?.actuator) window.State.charts.actuator.destroy();
  // // if (window.State?.charts?.sensor) window.State.charts.sensor.destroy();

  // if (window.State?.charts?.sensor) {
  //   window.State.charts.sensor.destroy();
  // }

  // window.State.charts.sensor = new Chart(
  //   document.getElementById("chartSensor"),
  //   makeConfig("Sensor", sensor, sensorColor, `Sensor (${unit})`, false),
  // );
  // ACTUADOR
  if (window.State?.charts?.actuator) {
    window.State.charts.actuator.destroy();
  }

  window.State.charts.actuator = new Chart(
    document.getElementById("chartActuator"),
    makeConfig("Actuador", actuator, actuatorColor, `Actuador (${unit})`, true)
  );

  // SENSOR
  if (window.State?.charts?.sensor) {
    window.State.charts.sensor.destroy();
  }

  window.State.charts.sensor = new Chart(
    document.getElementById("chartSensor"),
    makeConfig("Sensor", sensor, sensorColor, `Sensor (${unit})`, false),
  );
}

// ==================== COMPARISON ====================
function plotComparison(time, sensorMeasured, results) {
  const colors = ["#e03050", "#7048c8", "#e08000", "#0078c8"];

  const datasets = [
    {
      label: "Medido",
      data: sensorMeasured,
      borderColor: "#1a9e5c",
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.12,
    },
    ...results.map((r, i) => ({
      label: r.method,
      data: r.simulated,
      borderColor: colors[i] || "#999999",
      borderWidth: 2,
      borderDash: i > 0 ? [5, 4] : [],
      pointRadius: 0,
      tension: 0.12,
    })),
  ];

  if (window.State?.charts?.comparison)
    window.State.charts.comparison.destroy();

  window.State.charts.comparison = new Chart(
    document.getElementById("chartComparison"),
    {
      type: "line",
      data: {
        labels: time.map((v) => Number(v).toFixed(2)),
        datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 350 },
        plugins: {
          legend: {
            display: true,
            labels: {
              color: "#7a8fa0",
              font: { family: "IBM Plex Sans", size: 9 },
              boxWidth: 20,
            },
          },
        },
        scales: {
          x: {
            ticks: {
              color: "#7a8fa0",
              font: { family: "IBM Plex Mono", size: 9 },
              maxTicksLimit: 10,
            },
            grid: { color: "rgba(160,180,200,0.25)" },
          },
          y: {
            ticks: {
              color: "#7a8fa0",
              font: { family: "IBM Plex Mono", size: 9 },
            },
            grid: { color: "rgba(160,180,200,0.25)" },
          },
        },
      },
    },
  );
}

// ==================== BODE ====================
function plotBode(result) {
  if (!result) return;

  const modelType = (result.model_type || "").toLowerCase();
  const K = Number(result.gain ?? 1);
  const L = Number(result.dead_time ?? 0);

  const freq = [];
  const mag = [];
  const phase = [];

  for (let i = -3; i <= 2; i += 0.03) {
    const w = Math.pow(10, i);
    freq.push(w);

    let real = 0;
    let imag = 0;

    if (modelType === "fopdt") {
      const tau = Number(result.tau ?? 1);
      const denReal = 1;
      const denImag = w * tau;
      const denMag2 = denReal * denReal + denImag * denImag;

      real = (K * denReal) / denMag2;
      imag = (-K * denImag) / denMag2;
    } else if (modelType === "sopdt") {
      const tau1 = Number(result.tau1 ?? 1);
      const tau2 = Number(result.tau2 ?? 1);

      // G(jw)=K/((1+jw*t1)(1+jw*t2))
      const a = 1 - w * tau1 * (w * tau2);
      const b = w * (tau1 + tau2);
      const denMag2 = a * a + b * b;

      real = (K * a) / denMag2;
      imag = (-K * b) / denMag2;
    } else if (modelType === "integrating") {
      // G(jw)=K/(jw)
      real = 0;
      imag = -K / w;
    } else {
      real = K;
      imag = 0;
    }

    const magnitude = Math.sqrt(real * real + imag * imag);
    const phaseDegBase = (Math.atan2(imag, real) * 180) / Math.PI;
    const delayPhase = (-(w * L) * 180) / Math.PI;
    const totalPhase = phaseDegBase + delayPhase;

    mag.push(20 * Math.log10(Math.max(magnitude, 1e-12)));
    phase.push(totalPhase);
  }

  if (window.State?.charts?.bode) window.State.charts.bode.destroy();

  window.State.charts.bode = new Chart(document.getElementById("chartBode"), {
    type: "line",
    data: {
      labels: freq.map((v) => v.toExponential(1)),
      datasets: [
        {
          label: "Magnitud (dB)",
          data: mag,
          borderColor: "#0078c8",
          borderWidth: 2,
          pointRadius: 0,
          yAxisID: "yMag",
        },
        {
          label: "Fase (°)",
          data: phase,
          borderColor: "#e03050",
          borderWidth: 2,
          borderDash: [6, 3],
          pointRadius: 0,
          yAxisID: "yPhase",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 350 },
      plugins: {
        legend: {
          labels: {
            color: "#7a8fa0",
            font: { family: "IBM Plex Sans", size: 9 },
          },
        },
      },
      scales: {
        x: {
          type: "category",
          ticks: {
            color: "#7a8fa0",
            font: { family: "IBM Plex Mono", size: 9 },
            maxTicksLimit: 12,
          },
          grid: { color: "rgba(160,180,200,0.25)" },
          title: {
            display: true,
            text: "Frecuencia (rad/s)",
            color: "#7a8fa0",
            font: { family: "IBM Plex Sans", size: 9 },
          },
        },
        yMag: {
          position: "left",
          ticks: {
            color: "#0078c8",
            font: { family: "IBM Plex Mono", size: 9 },
          },
          grid: { color: "rgba(160,180,200,0.25)" },
          title: {
            display: true,
            text: "Magnitud (dB)",
            color: "#0078c8",
            font: { family: "IBM Plex Sans", size: 9 },
          },
        },
        yPhase: {
          position: "right",
          ticks: {
            color: "#e03050",
            font: { family: "IBM Plex Mono", size: 9 },
          },
          grid: { drawOnChartArea: false },
          title: {
            display: true,
            text: "Fase (°)",
            color: "#e03050",
            font: { family: "IBM Plex Sans", size: 9 },
          },
        },
      },
    },
  });
}
