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
    // Si hay ensayo capturado, se redibuja con las nuevas unidades.
    if (typeof plotCapture === "function") plotCapture();
  });

  document.getElementById("typeSen")?.addEventListener("change", () => {
    if (typeof plotCapture === "function") plotCapture();
  });

  // ---------- Cambio de variable (paso 1) -> reasignar mapeo en el backend ----------
  // Sin esto los <select> son puramente decorativos: el PLCReader sigue
  // leyendo las variables que se fijaron en el login.
  ["varAct", "varSen", "varSP"].forEach((id) => {
    document.getElementById(id)?.addEventListener("change", () => {
      applyMappingChange();
    });
  });

  // ---------- Cerrar toast de notificaciones ----------
  document.getElementById("toastClose")?.addEventListener("click", () => {
    if (typeof dismissStatus === "function") dismissStatus();
  });

  // ---------- Redraw del preview al resize ----------
  window.addEventListener("resize", () => {
    if (document.getElementById("p2")?.classList.contains("active")) {
      drawStepPreview();
    }
  });

  // ---------- Auto-conexión WS (sin arrancar ensayo) ----------
  // Se abre la conexión apenas carga la app para que los dropdowns de
  // variables (paso 1) se pueblen y los live values del paso 3 se
  // actualicen. NO arranca un ensayo — para eso está el botón "Inicio"
  // del paso 3, que resetea el buffer y arranca el timer.
  if (typeof ensureWebSocket === "function") {
    ensureWebSocket();
  }

  // Si el backend quedó en modo importado (recarga de página), reflejarlo.
  if (typeof refreshImportState === "function") {
    refreshImportState();
  }
});


/* =========================================================
   IMPORTAR ARCHIVO (topbar)
   Abre el selector de archivos del sistema operativo. Por ahora
   sin lógica: solo dispara el picker. La carga real se cablea
   cuando definamos el formato y el endpoint.
   ========================================================= */
function openFileImport() {
  const input = document.getElementById("fileImportInput");
  if (input) input.click();
}


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
   MAPEO DE VARIABLES (paso 1)
   Cuando el usuario cambia un <select> de variable en el paso 1,
   se envía el mapeo completo al backend con POST /api/opcua/mapping.
   El backend limpia el buffer y descarta la identificación en curso.
   ========================================================= */
async function applyMappingChange() {
  // El mapping completo (5 roles). `time` y `signal_type` no tienen UI todavía,
  // así que se preservan de lo que el backend nos mandó vía sample.mapping.
  const mapping = {
    time:        State.mapping.time        || null,
    actuator:    document.getElementById("varAct")?.value || null,
    sensor:      document.getElementById("varSen")?.value || null,
    setpoint:    document.getElementById("varSP")?.value  || null,
    signal_type: State.mapping.signal_type || null
  };

  // Mientras el cambio viaja, `populateVariableDropdowns` no debe re-sincronizar
  // los <select> con el mapeo viejo: se verían saltar al valor anterior.
  State.mappingPending = true;
  setStatus("Actualizando mapeo de variables...", "running");

  try {
    const resp = await Backend.setMapping(mapping);

    // El backend devuelve el mapping efectivo (con los null resueltos vía alias).
    State.mapping = { ...State.mapping, ...(resp.mapping || mapping) };

    try {
      localStorage.setItem("plcMapping", JSON.stringify(State.mapping));
    } catch (_) {}

    // El buffer local se capturó leyendo OTRAS variables. Si no se limpia, la
    // pantalla sigue mostrando los números viejos y da la impresión de que el
    // cambio no surtió efecto.
    resetSampleStore();
    clearLiveValues();
    if (typeof plotCapture === "function") plotCapture();

    setStatus(
      `Mapeo actualizado — u: ${mapping.actuator || "—"} · ` +
        `y: ${mapping.sensor || "—"} · SP: ${mapping.setpoint || "—"}. ` +
        `Buffer e identificación descartados.`,
      "ok"
    );
  } catch (err) {
    console.error("Error al cambiar mapping:", err);
    setStatus(`No se pudo cambiar el mapeo: ${err.message}`, "error");
  } finally {
    // Se libera pase lo que pase: si falló, la próxima muestra devuelve los
    // <select> a lo que el backend realmente tiene.
    State.mappingPending = false;
  }
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
