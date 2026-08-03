/* =========================================================
   main.js
   Punto de entrada. Se carga al final del <body>.
   - Inicializa la vista (estado de conexión, preview, badges).
   - Cableado de event listeners globales.
   - Implementa `loadSample` (botones Demo del paso 1).
   ========================================================= */


document.addEventListener("DOMContentLoaded", () => {
  // ---------- Estado inicial ----------
  setConnectionStatus(false);

  // Sincronizar badges con el value inicial de cada select
  updateBadge("act");
  updateBadge("sen");
  updateBadge("sp");

  // Preview del escalón (paso 2) — dibujo inicial
  drawStepPreview();

  // ---------- Redraw del preview al cambiar tipo de señal actuador ----------
  document.getElementById("typeAct")?.addEventListener("change", () => {
    drawStepPreview();
    // Los valores en vivo pueden cambiar de unidad
    refreshLiveViews?.();
  });

  document.getElementById("typeSen")?.addEventListener("change", () => {
    refreshLiveViews?.();
  });

  // ---------- Redraw del preview al resize ----------
  window.addEventListener("resize", () => {
    if (document.getElementById("p2")?.classList.contains("active")) {
      drawStepPreview();
    }
  });
});


/* =========================================================
   BOTONES DEMO (paso 1)
   El backend de ctrlx_sysid no expone /api/generate_sample,
   así que por ahora solo mostramos un aviso.
   ========================================================= */
function loadSample(scenario) {
  setStatus(
    `Demo "${scenario.toUpperCase()}" no disponible con este backend. ` +
      `Los datos deben provenir del PLC vía WebSocket.`,
    "error"
  );
}
