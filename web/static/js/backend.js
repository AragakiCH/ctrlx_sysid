/* =========================================================
   backend.js
   Cliente REST para los endpoints que NO cubre test_config.js.
   Provee helpers para /api/opcua/mapping, /api/opcua/status y
   /api/identification/run. Todo el resto (escalas y ensayo)
   vive en test_config.js.
   ========================================================= */


/** Base absoluta de la API. */
function apiBase() {
  if (window.State?.API_BASE) {
    return String(window.State.API_BASE).replace(/\/$/, "");
  }
  return window.location.origin;
}


/** Wrapper de fetch con manejo de errores JSON estándar de FastAPI. */
async function apiCall(path, { method = "GET", body = null } = {}) {
  const options = {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body:    body ? JSON.stringify(body) : null,
    cache:   "no-store"
  };

  const response = await fetch(`${apiBase()}${path}`, options);

  if (response.status === 204) return null;

  let data = null;
  try { data = await response.json(); } catch (_) {}

  if (!response.ok) {
    // 422 = validación de Pydantic (Array de errores).
    if (response.status === 422 && Array.isArray(data?.detail)) {
      const campos = data.detail
        .map((e) => `${(e.loc || []).slice(-1)[0]}: ${e.msg}`)
        .join(" · ");
      throw Object.assign(new Error(campos || "Datos inválidos"), { status: 422, data });
    }

    const msg = (data && (data.detail || data.message)) || `${method} ${path} → ${response.status}`;
    throw Object.assign(new Error(msg), { status: response.status, data });
  }

  return data;
}


const Backend = {

  /** POST /api/opcua/mapping — reasigna qué variable cumple cada rol.
   *  **Limpia el buffer y descarta la identificación** en curso.
   *  @param {Object} mapping - { time, actuator, sensor, setpoint, signal_type }
   */
  async setMapping(mapping) {
    return apiCall("/api/opcua/mapping", {
      method: "POST",
      body:   { mapping }
    });
  },

  /** GET /api/opcua/status — diagnóstico de la sesión OPC UA. */
  async getOpcuaStatus() {
    return apiCall("/api/opcua/status");
  }
};

window.Backend = Backend;
