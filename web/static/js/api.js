// ==================== LOAD SAMPLE DATA ====================
async function loadSample(scenario) {
  const noise = document.getElementById('noiseLevel').value;
  try {
    const r = await fetch(`${State.API_BASE}/generate_sample?scenario=${scenario}&noise=${noise}`);
    const data = await r.json();
    if (!data.success) throw new Error(data.error);

    const isMA = document.getElementById('signalType').value === 'ma';
    document.getElementById('dataTime').value = data.time.map(v => v.toFixed(1)).join(', ');
    document.getElementById('dataActuator').value = isMA
      ? data.actuator_ma.map(v => v.toFixed(2)).join(', ')
      : data.actuator_pct.map(v => v.toFixed(1)).join(', ');
    document.getElementById('dataSensor').value = isMA
      ? data.sensor_ma.map(v => v.toFixed(2)).join(', ')
      : data.sensor_pct.map(v => v.toFixed(1)).join(', ');

    plotRawData(
      data.time,
      isMA ? data.actuator_ma : data.actuator_pct,
      isMA ? data.sensor_ma : data.sensor_pct
    );
    switchTab('data');
    notify(`Datos de ejemplo cargados: ${scenario.toUpperCase()}`, 'success');
  } catch (e) {
    notify('Error: ' + e.message, 'error');
  }
}

// ==================== RUN IDENTIFICATION ====================
async function runIdentification() {
  const time     = parseCSV(document.getElementById('dataTime').value);
  const actuator = parseCSV(document.getElementById('dataActuator').value);
  const sensor   = parseCSV(document.getElementById('dataSensor').value);
  const setpoint = parseCSV(document.getElementById('dataSetpoint').value);

  if (time.length < 5 || actuator.length < 5 || sensor.length < 5) {
    notify('Se requieren al menos 5 puntos. Ingrese datos o cargue un ejemplo.', 'error');
    return;
  }

  const minLen = Math.min(time.length, actuator.length, sensor.length);
  const body = {
    time: time.slice(0, minLen),
    actuator_ma: actuator.slice(0, minLen),
    sensor_ma: sensor.slice(0, minLen),
    signal_type: document.getElementById('signalType').value,
  };
  if (setpoint.length >= minLen) body.setpoint_ma = setpoint.slice(0, minLen);

  const order = document.getElementById('orderSelect').value;
  let endpoint = `${State.API_BASE}/identify`;
  if (order !== 'auto') {
    endpoint = `${State.API_BASE}/identify_order`;
    body.order = parseInt(order);
    body.actuator = body.actuator_ma;
    body.sensor = body.sensor_ma;
  }

  document.getElementById('loadingOverlay').classList.add('visible');

  try {
    const r = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!data.success) throw new Error(data.error);

    plotRawData(data.time, data.actuator_pct, data.sensor_pct, data.setpoint_pct);

    const results = data.results || (data.result ? [data.result] : []);
    State.identificationResults = { results, time: data.time, sensor: data.sensor_pct };

    renderIdentification(results, data.time, data.sensor_pct);
    renderPID(results);
    renderBode(results[0]);

    // Update metrics bar
    const best = results[0];
    document.getElementById('metricsBar').style.display = '';
    document.getElementById('metBestModel').textContent = best.method || '—';
    document.getElementById('metFitQuality').textContent = (best.fit_quality || 0).toFixed(1) + '%';
    document.getElementById('metK').textContent = (best.K || 0).toFixed(4);
    document.getElementById('metL').textContent = (best.L || best.delay || 0).toFixed(4) + 's';

    switchTab('identification');
    notify('Identificación completada. Ajuste: ' + (best.fit_quality || 0).toFixed(1) + '%', 'success');
  } catch (e) {
    notify('Error en identificación: ' + e.message, 'error');
  } finally {
    document.getElementById('loadingOverlay').classList.remove('visible');
  }
}