// // ==================== RENDER PID ====================
// function renderPID(results) {
//   if (!results?.length) return;

//   document.getElementById('noPidMsg').style.display = 'none';
//   document.getElementById('pidContent').style.display = '';

//   const container = document.getElementById('pidTablesContainer');
//   container.innerHTML = results
//     .filter(r => r.pid_tuning && Object.keys(r.pid_tuning).length > 0)
//     .map(r => buildPIDTable(r))
//     .join('');

//   const best = results[0];
//   document.getElementById('pidInfoBox').innerHTML = `
//     <strong style="color:var(--accent)">MODELO RECOMENDADO: ${best.method}</strong><br>
//     K = ${best.K} | τ = ${best.tau || best.tau1 || '—'} s | L = ${best.delay || best.L || 0} s<br><br>
//     <strong style="color:var(--green)">Para ctrlX Core X3:</strong><br>
//     Configure el bloque PID en AXCS con los parámetros de la tabla superior.<br>
//     Se recomienda el método IMC/Lambda para mayor robustez ante perturbaciones.<br>
//     Verifique el modo de anti-windup y los límites de saturación del actuador.
//   `;
// }

// // ==================== BUILD PID TABLE ====================
// function buildPIDTable(r) {
//   const rows = Object.entries(r.pid_tuning).map(([method, p]) => `
//     <tr>
//       <td class="td-method">${method}</td>
//       <td class="td-value">${p.Kp}</td>
//       <td class="td-value">${p.Ti}</td>
//       <td class="td-value">${p.Td}</td>
//       <td>${p.Ki}</td>
//       <td>${p.Kd}</td>
//       <td class="td-desc">${p.description}</td>
//     </tr>
//   `).join('');

//   return `
//     <div style="font-family:'Share Tech Mono';font-size:10px;color:var(--accent-dim);margin-bottom:8px;letter-spacing:1px">
//       MODELO: ${r.method} — Ajuste R² ${(r.fit_quality || 0).toFixed(1)}%
//     </div>
//     <div class="pid-table-wrapper" style="margin-bottom:20px">
//       <table>
//         <thead>
//           <tr>
//             <th>MÉTODO</th>
//             <th>Kp</th>
//             <th>Ti (s)</th>
//             <th>Td (s)</th>
//             <th>Ki</th>
//             <th>Kd</th>
//             <th>DESCRIPCIÓN</th>
//           </tr>
//         </thead>
//         <tbody>${rows}</tbody>
//       </table>
//     </div>
//   `;
// }

let pidChart = null;

function plotPIDTunings(tunings) {
  if (!Array.isArray(tunings) || !tunings.length) return;

  const labels = tunings.map((t) => t.method || "Método");

  const kpData = tunings.map((t) => Number(t.kp ?? t.Kp) || 0);
  const kiData = tunings.map((t) => Number(t.ki ?? t.Ki) || 0);
  const kdData = tunings.map((t) => Number(t.kd ?? t.Kd) || 0);

  const ctx = document.getElementById("chartPID");
  if (!ctx) return;

  // destruir gráfico anterior
  if (pidChart) {
    pidChart.destroy();
  }

  pidChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Kp",
          data: kpData,
          backgroundColor: "#0078c8",
        },
        {
          label: "Ki",
          data: kiData,
          backgroundColor: "#1a9e5c",
        },
        {
          label: "Kd",
          data: kdData,
          backgroundColor: "#e03050",
        },
      ],
    },
    options: {
      responsive: true,
      plugins: {
        legend: {
          position: "top",
        },
      },
      scales: {
        y: {
          beginAtZero: true,
        },
      },
    },
  });
}
