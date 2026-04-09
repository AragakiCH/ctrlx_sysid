// ==================== RENDER PID ====================
function renderPID(results) {
  if (!results?.length) return;

  document.getElementById('noPidMsg').style.display = 'none';
  document.getElementById('pidContent').style.display = '';

  const container = document.getElementById('pidTablesContainer');
  container.innerHTML = results
    .filter(r => r.pid_tuning && Object.keys(r.pid_tuning).length > 0)
    .map(r => buildPIDTable(r))
    .join('');

  const best = results[0];
  document.getElementById('pidInfoBox').innerHTML = `
    <strong style="color:var(--accent)">MODELO RECOMENDADO: ${best.method}</strong><br>
    K = ${best.K} | τ = ${best.tau || best.tau1 || '—'} s | L = ${best.delay || best.L || 0} s<br><br>
    <strong style="color:var(--green)">Para ctrlX Core X3:</strong><br>
    Configure el bloque PID en AXCS con los parámetros de la tabla superior.<br>
    Se recomienda el método IMC/Lambda para mayor robustez ante perturbaciones.<br>
    Verifique el modo de anti-windup y los límites de saturación del actuador.
  `;
}

// ==================== BUILD PID TABLE ====================
function buildPIDTable(r) {
  const rows = Object.entries(r.pid_tuning).map(([method, p]) => `
    <tr>
      <td class="td-method">${method}</td>
      <td class="td-value">${p.Kp}</td>
      <td class="td-value">${p.Ti}</td>
      <td class="td-value">${p.Td}</td>
      <td>${p.Ki}</td>
      <td>${p.Kd}</td>
      <td class="td-desc">${p.description}</td>
    </tr>
  `).join('');

  return `
    <div style="font-family:'Share Tech Mono';font-size:10px;color:var(--accent-dim);margin-bottom:8px;letter-spacing:1px">
      MODELO: ${r.method} — Ajuste R² ${(r.fit_quality || 0).toFixed(1)}%
    </div>
    <div class="pid-table-wrapper" style="margin-bottom:20px">
      <table>
        <thead>
          <tr>
            <th>MÉTODO</th>
            <th>Kp</th>
            <th>Ti (s)</th>
            <th>Td (s)</th>
            <th>Ki</th>
            <th>Kd</th>
            <th>DESCRIPCIÓN</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}