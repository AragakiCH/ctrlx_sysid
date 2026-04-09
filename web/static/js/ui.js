// ==================== CLOCK ====================
function updateClock() {
  const now = new Date();
  document.getElementById('clockDisplay').textContent =
    now.toLocaleTimeString('es-PE', { hour12: false });
}
updateClock();
setInterval(updateClock, 1000);

// ==================== SIGNAL LABELS ====================
function updateSignalLabels() {
  const isMA = document.getElementById('signalType').value === 'ma';
  document.getElementById('actUnit').textContent = isMA ? 'mA' : '%';
  document.getElementById('senUnit').textContent = isMA ? 'mA' : '%';
  document.getElementById('spUnit').textContent = isMA ? 'mA (opcional)' : '% (opcional)';
  document.getElementById('dataActuator').placeholder = isMA ? '4.0, 4.0, 12.0, 12.0 ...' : '40, 40, 60, 60 ...';
  document.getElementById('dataSensor').placeholder = isMA ? '4.0, 4.1, 5.2, 8.3 ...' : '40, 40.5, 45, 55 ...';
}

// ==================== TABS ====================
function switchTab(tab) {
  const tabNames = ['data', 'identification', 'pid', 'bode'];
  document.querySelectorAll('.tab').forEach((t, i) => {
    t.classList.toggle('active', tabNames[i] === tab);
  });
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  setTimeout(() => Object.values(State.charts).forEach(c => c?.resize?.()), 50);
}

// ==================== NOTIFICATION ====================
function notify(msg, type = 'info') {
  const n = document.getElementById('notification');
  n.textContent = msg;
  n.className = 'notification show ' + type;
  setTimeout(() => n.className = 'notification', 3500);
}

// ==================== PARSE CSV ====================
function parseCSV(str) {
  if (!str.trim()) return [];
  return str.split(/[\n,;]+/).map(v => parseFloat(v.trim())).filter(v => !isNaN(v));
}

// ==================== CLEAR ====================
function clearAll() {
  ['dataTime', 'dataActuator', 'dataSensor', 'dataSetpoint'].forEach(id => {
    document.getElementById(id).value = '';
  });

  ['actuator', 'sensor', 'comparison', 'bode'].forEach(key => {
    if (State.charts[key]) {
      State.charts[key].destroy();
      delete State.charts[key];
    }
  });

  document.getElementById('noResultsMsg').style.display = 'flex';
  document.getElementById('idChartCard').style.display = 'none';
  document.getElementById('resultsGrid').style.display = 'none';
  document.getElementById('metricsBar').style.display = 'none';
  document.getElementById('resultsGrid').innerHTML = '';

  document.getElementById('noPidMsg').style.display = 'flex';
  document.getElementById('pidContent').style.display = 'none';

  document.getElementById('noBodeMsg').style.display = 'flex';
  document.getElementById('bodeContent').style.display = 'none';

  State.identificationResults = null;
  notify('Panel limpiado', 'info');
}