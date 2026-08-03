/* =========================================================
   export.js
   Exporta un reporte TXT con el modelo identificado y las
   sintonías PID calculadas. Se activa desde el botón de la
   topbar (aparece cuando hay resultados).
   ========================================================= */


/** Descarga `ctrlx_pid_reporte.txt` con toda la info del último resultado. */
function exportResults() {
  const models = State.identification.models;
  if (!models.length) return;

  const best   = models[State.identification.active || 0];
  const tuning = best.pid_tunings?.length
    ? Object.fromEntries(best.pid_tunings.map((t) => [t.method || "—", t]))
    : tunePIDLocal(best);

  const varAct  = document.getElementById("varAct").value;
  const varSen  = document.getElementById("varSen").value;
  const typeAct = document.getElementById("typeAct").value;
  const typeSen = document.getElementById("typeSen").value;
  const dt      = document.getElementById("sampleTime").value;

  let txt = "ctrlX CORE X3 — Reporte de Identificación y Sintonía PID\n";
  txt += "=".repeat(55) + "\n\n";

  // Variables
  txt += "CONFIGURACIÓN DE VARIABLES\n" + "-".repeat(30) + "\n";
  txt += `  Actuador : ${varAct} [${BADGE_LBL[typeAct]}]\n`;
  txt += `  Sensor   : ${varSen} [${BADGE_LBL[typeSen]}]\n`;
  txt += `  Muestreo : ${dt} s\n\n`;

  // Mejor modelo
  const K = Number(best.gain ?? 0);
  const L = Number(best.dead_time ?? 0);

  txt += "MEJOR MODELO IDENTIFICADO\n" + "-".repeat(30) + "\n";
  txt += `  Modelo   : ${modelTypeLabel(best.model_type)}\n`;
  txt += `  Ajuste   : R² = ${formatNumber(best.fit_quality, 2)}%\n`;
  txt += `  Ganancia : K  = ${K.toFixed(6)}\n`;
  if (best.tau)  txt += `  Tau      : τ  = ${Number(best.tau).toFixed(6)} s\n`;
  if (best.tau1) txt += `  Tau1     : τ₁ = ${Number(best.tau1).toFixed(6)} s\n  Tau2     : τ₂ = ${Number(best.tau2).toFixed(6)} s\n`;
  txt += `  Dead Time: L  = ${L.toFixed(6)} s\n\n`;

  // Función de transferencia
  txt += "FUNCIÓN DE TRANSFERENCIA\n" + "-".repeat(30) + "\n";
  const type = (best.model_type || "").toLowerCase();
  if (type === "fopdt") {
    txt += `  G(s) = ${K.toFixed(4)} * exp(-${L.toFixed(4)}s) / (${Number(best.tau).toFixed(4)}s + 1)\n\n`;
  } else if (type === "sopdt") {
    txt += `  G(s) = ${K.toFixed(4)} * exp(-${L.toFixed(4)}s) / ((${Number(best.tau1).toFixed(4)}s+1)(${Number(best.tau2).toFixed(4)}s+1))\n\n`;
  } else {
    txt += `  G(s) = ${K.toFixed(4)} * exp(-${L.toFixed(4)}s) / s\n\n`;
  }

  // Sintonía PID
  txt += "SINTONÍA PID\n" + "-".repeat(30) + "\n";
  txt += `  ${"Método".padEnd(20)} ${"Kp".padEnd(10)} ${"Ti(s)".padEnd(10)} ${"Td(s)".padEnd(10)} ${"Ki".padEnd(10)} Kd\n`;

  Object.entries(tuning).forEach(([m, p]) => {
    const kp = pickNumber(p.Kp, p.kp) ?? 0;
    const ti = pickNumber(p.Ti, p.ti) ?? 0;
    const td = pickNumber(p.Td, p.td) ?? 0;
    const ki = pickNumber(p.Ki, p.ki) ?? (kp / Math.max(ti, 0.001));
    const kd = pickNumber(p.Kd, p.kd) ?? (kp * td);

    txt +=
      `  ${m.padEnd(20)} ` +
      `${String(kp).padEnd(10)} ` +
      `${String(ti).padEnd(10)} ` +
      `${String(td).padEnd(10)} ` +
      `${String(ki).padEnd(10)} ` +
      `${kd}\n`;
  });

  // Descarga
  const blob = new Blob([txt], { type: "text/plain;charset=utf-8" });
  const a    = document.createElement("a");
  a.href     = URL.createObjectURL(blob);
  a.download = "ctrlx_pid_reporte.txt";
  a.click();
  URL.revokeObjectURL(a.href);
}
