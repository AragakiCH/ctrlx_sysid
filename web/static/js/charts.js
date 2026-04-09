// ==================== PLOT RAW DATA ====================
function plotRawData(time, actuator, sensor, setpoint) {
  const sigType = document.getElementById('signalType').value;
  const unit = sigType === 'ma' ? 'mA' : '%';

  const makeConfig = (label, data, color, yLabel, withSetpoint) => ({
    type: 'line',
    data: {
      labels: time.map(v => v.toFixed(1)),
      datasets: [
        {
          label,
          data,
          borderColor: color,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.1,
          fill: false,
        },
        ...(withSetpoint && setpoint?.length ? [{
          label: 'Set Point',
          data: setpoint,
          borderColor: '#c47a00',
          borderWidth: 1.5,
          borderDash: [6, 4],
          pointRadius: 0,
          tension: 0,
          fill: false,
        }] : []),
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: '#7a8fa0', font: { family: 'IBM Plex Mono', size: 9 }, maxTicksLimit: 10 },
          grid: { color: 'rgba(160,180,200,0.25)' },
          title: { display: true, text: 'Tiempo (s)', color: '#7a8fa0', font: { family: 'IBM Plex Sans', size: 9 } },
        },
        y: {
          ticks: { color: '#7a8fa0', font: { family: 'IBM Plex Mono', size: 9 } },
          grid: { color: 'rgba(160,180,200,0.25)' },
          title: { display: true, text: yLabel, color: '#7a8fa0', font: { family: 'IBM Plex Sans', size: 9 } },
        },
      },
    },
  });

  if (State.charts.actuator) State.charts.actuator.destroy();
  if (State.charts.sensor) State.charts.sensor.destroy();

  State.charts.actuator = new Chart(
    document.getElementById('chartActuator'),
    makeConfig('Actuador', actuator, '#0078c8', `Actuador (${unit})`, true)
  );
  State.charts.sensor = new Chart(
    document.getElementById('chartSensor'),
    makeConfig('Sensor', sensor, '#1a9e5c', `Sensor (${unit})`, false)
  );
}

// ==================== COMPARISON CHART ====================
function plotComparison(time, sensorPct, results) {
  const colors = ['#e03050', '#7048c8', '#e08000'];
  const datasets = [
    {
      label: 'Medido',
      data: sensorPct,
      borderColor: '#1a9e5c',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.1,
    },
    ...results.map((r, i) => ({
      label: r.method,
      data: r.simulated,
      borderColor: colors[i] || '#ffffff',
      borderWidth: 2,
      borderDash: i > 0 ? [4, 4] : [],
      pointRadius: 0,
      tension: 0.1,
    })),
  ];

  if (State.charts.comparison) State.charts.comparison.destroy();
  State.charts.comparison = new Chart(document.getElementById('chartComparison'), {
    type: 'line',
    data: { labels: time.map(v => v.toFixed(1)), datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 600 },
      plugins: {
        legend: {
          display: true,
          labels: { color: '#7a8fa0', font: { family: 'IBM Plex Sans', size: 9 }, boxWidth: 20 },
        },
      },
      scales: {
        x: { ticks: { color: '#7a8fa0', font: { family: 'IBM Plex Mono', size: 9 }, maxTicksLimit: 10 }, grid: { color: 'rgba(160,180,200,0.25)' } },
        y: { ticks: { color: '#7a8fa0', font: { family: 'IBM Plex Mono', size: 9 } }, grid: { color: 'rgba(160,180,200,0.25)' } },
      },
    },
  });
}

// ==================== BODE CHART ====================
function plotBode(result) {
  if (!result) return;

  const K = result.K || 1;
  const L = result.delay || result.L || 0;
  const tau = result.tau || 10;

  const freq = [], mag = [], phase = [];
  for (let i = -3; i <= 2; i += 0.02) {
    const w = Math.pow(10, i);
    freq.push(w);
    const magVal = K / Math.sqrt(1 + (w * tau) ** 2);
    mag.push(20 * Math.log10(Math.abs(magVal)));
    phase.push(-Math.atan(w * tau) * 180 / Math.PI - w * L * 180 / Math.PI);
  }

  if (State.charts.bode) State.charts.bode.destroy();
  State.charts.bode = new Chart(document.getElementById('chartBode'), {
    type: 'line',
    data: {
      labels: freq.map(v => v.toExponential(1)),
      datasets: [
        { label: 'Magnitud (dB)', data: mag, borderColor: '#0078c8', borderWidth: 2, pointRadius: 0, yAxisID: 'yMag' },
        { label: 'Fase (°)', data: phase, borderColor: '#e03050', borderWidth: 2, borderDash: [6, 3], pointRadius: 0, yAxisID: 'yPhase' },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 600 },
      plugins: {
        legend: { labels: { color: '#7a8fa0', font: { family: 'IBM Plex Sans', size: 9 } } },
      },
      scales: {
        x: {
          type: 'category',
          ticks: { color: '#7a8fa0', font: { family: 'IBM Plex Mono', size: 9 }, maxTicksLimit: 12 },
          grid: { color: 'rgba(160,180,200,0.25)' },
          title: { display: true, text: 'Frecuencia (rad/s)', color: '#7a8fa0', font: { family: 'IBM Plex Sans', size: 9 } },
        },
        yMag: {
          position: 'left',
          ticks: { color: '#0078c8', font: { family: 'IBM Plex Mono', size: 9 } },
          grid: { color: 'rgba(160,180,200,0.25)' },
          title: { display: true, text: 'Magnitud (dB)', color: '#0078c8', font: { family: 'IBM Plex Sans', size: 9 } },
        },
        yPhase: {
          position: 'right',
          ticks: { color: '#e03050', font: { family: 'IBM Plex Mono', size: 9 } },
          grid: { drawOnChartArea: false },
          title: { display: true, text: 'Fase (°)', color: '#e03050', font: { family: 'IBM Plex Sans', size: 9 } },
        },
      },
    },
  });
}