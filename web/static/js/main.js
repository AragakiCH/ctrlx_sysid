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

  // ---------- Auto-conexión WS ----------
  // Se abre la conexión apenas carga la app para que los dropdowns de
  // variables (paso 1) se pueblen con lo que envía el backend. Si no
  // hay sesión OPC UA activa, el WS conecta pero no llega ningún sample
  // (documentado en el README). El botón "Inicio" del paso 3 sigue
  // funcionando como reset del buffer.
  if (typeof startCapture === "function") {
    startCapture();
  }
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


/* =========================================================
   LOGOUT
   Cierra la sesión OPC UA en el backend, limpia storage
   local y redirige al login.
   ========================================================= */
async function logout() {
  // Cerrar el WebSocket si sigue abierto (para el hilo de lectura del PLC).
  try {
    if (typeof stopCapture === "function") stopCapture();
  } catch (_) {}

  // Cerrar sesión en el backend (limpia credenciales OPC UA y buffer).
  try {
    await fetch(`${State.API_BASE}/api/opcua/logout`, { method: "POST" });
  } catch (err) {
    console.error("Error en logout:", err);
  }

  // Limpiar storage local.
  localStorage.removeItem("isAuth");
  localStorage.removeItem("plcProgram");
  localStorage.removeItem("plcMapping");
  localStorage.removeItem("plc_session");
  sessionStorage.clear();

  // Volver al login.
  window.location.href = "/";
}
