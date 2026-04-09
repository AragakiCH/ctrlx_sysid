// ==================== RENDER IDENTIFICATION ====================
function renderIdentification(results, time, sensorPct) {
  document.getElementById('noResultsMsg').style.display = 'none';
  document.getElementById('idChartCard').style.display = '';
  document.getElementById('resultsGrid').style.display = '';

  plotComparison(time, sensorPct, results);

  const grid = document.getElementById('resultsGrid');
  grid.innerHTML = results.map((r, i) => buildResultCard(r, i === 0)).join('');
}

// ==================== BUILD RESULT CARD ====================
function buildResultCard(r, isBest) {
  const K   = r.K?.toFixed(4) ?? '—';
  const L   = (r.delay ?? r.L ?? 0).toFixed(4);
  const fit = (r.fit_quality || 0).toFixed(1);

  let paramsHTML = `
    <div class="params-grid">
      <div class="param-cell">
        <div class="param-name">GANANCIA K</div>
        <div class="param-value">${K}</div>
      </div>
      <div class="param-cell">
        <div class="param-name">DEAD TIME L</div>
        <div class="param-value">${L}s</div>
      </div>
  `;
  if (r.tau)  paramsHTML += `<div class="param-cell"><div class="param-name">TAU τ</div><div class="param-value">${r.tau.toFixed(4)}s</div></div>`;
  if (r.tau1) paramsHTML += `<div class="param-cell"><div class="param-name">TAU₁ τ₁</div><div class="param-value">${r.tau1.toFixed(4)}s</div></div>`;
  if (r.tau2) paramsHTML += `<div class="param-cell"><div class="param-name">TAU₂ τ₂</div><div class="param-value">${r.tau2.toFixed(4)}s</div></div>`;
  paramsHTML += `</div>`;

  const numStr = r.numerator?.map(v => v.toFixed(4)).join(', ') ?? K;
  const denStr = r.denominator?.map(v => v.toFixed(4)).join('s + ').replace(/(\d+\.\d+)s/g, '$1s') ?? '—';

  return `
    <div class="result-card ${isBest ? 'best' : ''}">
      <div class="result-header">
        <div class="result-method">${r.method || 'MODELO'}</div>
        <div style="display:flex;gap:6px">
          ${isBest ? '<span class="result-badge badge-best">✓ MEJOR</span>' : ''}
          <span class="result-badge badge-quality">R²: ${fit}%</span>
        </div>
      </div>
      <div class="tf-display">
        <div class="tf-label">FUNCIÓN DE TRANSFERENCIA G(s)</div>
        <div class="tf-fraction">
          <div class="tf-num">${numStr}</div>
          <div class="tf-line"></div>
          <div class="tf-den">${denStr}s + 1.0</div>
        </div>
        ${parseFloat(L) > 0 ? `<div class="tf-delay">× e^(-${L}s)</div>` : ''}
      </div>
      ${paramsHTML}
    </div>
  `;
}

// ==================== RENDER BODE (wrapper) ====================
function renderBode(result) {
  if (!result) return;
  document.getElementById('noBodeMsg').style.display = 'none';
  document.getElementById('bodeContent').style.display = '';
  plotBode(result);
}