document.addEventListener("DOMContentLoaded", () => {
  const ipSelect = document.getElementById("ipSelect");
  const programSelect = document.getElementById("programSelect");
  const userInput = document.getElementById("user");
  const passwordInput = document.getElementById("password");
  const btnDiscover = document.getElementById("btnDiscoverPrograms");
  const loginForm = document.getElementById("loginForm");
  const errorDiv = document.getElementById("errorMessage");
  const errorText = document.getElementById("errorText");
  const mappingGroup = document.getElementById("mappingGroup");
  const mappingFields = document.getElementById("mappingFields");
  const mappingHint = document.getElementById("mappingHint");

  // Roles que el backend espera. Solo time/actuator/sensor son obligatorios
  // para poder identificar; setpoint y signal_type son opcionales.
  const ROLES = [
    { key: "time", label: "Tiempo (s)", required: true },
    { key: "actuator", label: "Actuador (MV)", required: true },
    { key: "sensor", label: "Sensor (PV)", required: true },
    { key: "setpoint", label: "Setpoint", required: false },
    { key: "signal_type", label: "Tipo de señal", required: false },
  ];

  let plcVariables = [];

  function getApiBase() {
    if (window.State?.API_BASE) {
      return window.State.API_BASE.replace(/\/$/, "");
    }

    const origin = window.location.origin;
    const parts = window.location.pathname.split("/").filter(Boolean);
    const prefix = parts.length ? `/${parts[0]}` : "";

    return `${origin}${prefix}`;
  }

  const API_BASE = getApiBase();
  console.log("API_BASE =", API_BASE);

  function showError(message) {
    errorText.innerText = message;
    errorDiv.style.display = "flex";
  }

  function clearError() {
    errorDiv.style.display = "none";
  }

  function credentials() {
    return {
      url: ipSelect.value,
      user: userInput.value.trim(),
      password: passwordInput.value,
    };
  }

  // ==================== HOSTS ====================
  const discoverHosts = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/opcua/discover`);
      const devices = await response.json();
      ipSelect.innerHTML = "";

      if (devices.length === 0) {
        ipSelect.innerHTML = '<option value="">No hay dispositivos</option>';
        return;
      }

      devices.forEach((d) => {
        const opt = document.createElement("option");
        opt.value = d.url;
        opt.textContent = `${d.host} ●`;

        if (d.tcp_ok) {
          opt.style.color = "#28a745";
          opt.style.fontWeight = "bold";
        } else {
          opt.style.color = "#888888";
        }

        ipSelect.appendChild(opt);
      });
    } catch (err) {
      console.error("Error hosts:", err);
    }
  };

  discoverHosts();

  // ==================== PROGRAMAS ====================
  btnDiscover.addEventListener("click", async () => {
    const { url, user, password } = credentials();

    if (!url || !user || !password) {
      showError("Complete IP, Usuario y Clave para buscar programas.");
      return;
    }

    programSelect.innerHTML = '<option value="">Buscando...</option>';
    resetMapping();
    clearError();

    try {
      const response = await fetch(`${API_BASE}/api/opcua/discover-programs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, user, password }),
      });

      const data = await response.json();
      programSelect.innerHTML = "";

      if (data.ok && Array.isArray(data.programs) && data.programs.length > 0) {
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Seleccione un programa...";
        programSelect.appendChild(placeholder);

        data.programs.forEach((prog) => {
          const opt = document.createElement("option");
          opt.value = prog;
          opt.textContent = prog;
          programSelect.appendChild(opt);
        });

        // Si solo hay uno, selecciónelo y cargue sus variables directo.
        if (data.programs.length === 1) {
          programSelect.value = data.programs[0];
          loadVariables();
        }
      } else {
        programSelect.innerHTML =
          '<option value="">No se hallaron programas</option>';
        showError(data.detail || "Credenciales incorrectas o no hay programas.");
      }
    } catch (err) {
      console.error("Error discover-programs:", err);
      programSelect.innerHTML = '<option value="">Error</option>';
      showError("Error de conexión al buscar programas.");
    }
  });

  // Al cambiar de programa, traer TODAS sus variables.
  programSelect.addEventListener("change", () => {
    resetMapping();
    if (programSelect.value) {
      loadVariables();
    }
  });

  // ==================== VARIABLES DEL PROGRAMA ====================
  function resetMapping() {
    plcVariables = [];
    mappingFields.innerHTML = "";
    mappingGroup.style.display = "none";
  }

  async function loadVariables() {
    const { url, user, password } = credentials();
    const programName = programSelect.value;

    if (!url || !user || !password || !programName) return;

    mappingGroup.style.display = "block";
    mappingHint.textContent = "Leyendo variables del programa...";
    mappingFields.innerHTML = "";
    clearError();

    try {
      const response = await fetch(`${API_BASE}/api/opcua/discover-variables`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url,
          user,
          password,
          program_name: programName,
        }),
      });

      const data = await response.json();

      if (!response.ok || !data.ok) {
        resetMapping();
        showError(
          data.detail || "No se pudieron leer las variables del programa.",
        );
        return;
      }

      plcVariables = Array.isArray(data.variables) ? data.variables : [];
      renderMappingFields(plcVariables, data.suggested_mapping || {});
    } catch (err) {
      console.error("Error discover-variables:", err);
      resetMapping();
      showError("Error de conexión al leer las variables.");
    }
  }

  function formatValue(value) {
    if (typeof value === "number") {
      return Number.isInteger(value) ? String(value) : value.toFixed(3);
    }
    return String(value);
  }

  function variableLabel(v) {
    const type = v.data_type && v.data_type !== "UNKNOWN" ? v.data_type : "?";
    const value =
      v.value === null || v.value === undefined ? "—" : formatValue(v.value);
    return `${v.name}  ·  ${type}  ·  ${value}`;
  }

  function renderMappingFields(variables, suggested) {
    mappingFields.innerHTML = "";

    if (!variables.length) {
      mappingHint.textContent = "El programa no expone variables legibles.";
      return;
    }

    mappingHint.textContent = `${variables.length} variables encontradas. Elija cuál corresponde a cada señal.`;

    ROLES.forEach((role) => {
      const wrapper = document.createElement("div");
      wrapper.className = "mapping-field";

      const label = document.createElement("label");
      label.setAttribute("for", `map_${role.key}`);
      label.textContent = role.required ? `${role.label}*` : role.label;

      const select = document.createElement("select");
      select.id = `map_${role.key}`;
      select.dataset.role = role.key;
      select.className = "mapping-select";

      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = role.required ? "— seleccione —" : "— ninguna —";
      select.appendChild(empty);

      variables.forEach((v) => {
        const opt = document.createElement("option");
        opt.value = v.name;
        opt.textContent = variableLabel(v);
        if (!v.numeric) {
          opt.style.color = "#888888";
        }
        select.appendChild(opt);
      });

      const suggestion = suggested[role.key];
      if (suggestion && variables.some((v) => v.name === suggestion)) {
        select.value = suggestion;
      }

      wrapper.appendChild(label);
      wrapper.appendChild(select);
      mappingFields.appendChild(wrapper);
    });
  }

  function collectMapping() {
    const mapping = {};
    ROLES.forEach((role) => {
      const select = document.getElementById(`map_${role.key}`);
      mapping[role.key] = select && select.value ? select.value : null;
    });
    return mapping;
  }

  function missingRequiredRoles(mapping) {
    return ROLES.filter((r) => r.required && !mapping[r.key]).map(
      (r) => r.label,
    );
  }

  // ==================== LOGIN ====================
  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const programName = programSelect.value;

    if (!programName) {
      showError("Primero obtenga y seleccione un programa.");
      return;
    }

    if (!plcVariables.length) {
      showError("Aún no se han leído las variables del programa.");
      return;
    }

    const mapping = collectMapping();
    const missing = missingRequiredRoles(mapping);

    if (missing.length) {
      showError(`Falta asignar: ${missing.join(", ")}.`);
      return;
    }

    const { url, user, password } = credentials();

    const payload = {
      user,
      password,
      url,
      program_name: programName,
      mapping,
    };

    try {
      const response = await fetch(`${API_BASE}/api/opcua/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await response.json();

      if (response.ok) {
        localStorage.setItem("isAuth", "true");
        localStorage.setItem("plcProgram", programName);
        localStorage.setItem(
          "plcMapping",
          JSON.stringify(data.mapping || mapping),
        );
        window.location.href = `${API_BASE}/app`;
      } else {
        showError(data.detail || "Error en el inicio de sesión final.");
      }
    } catch (err) {
      console.error("Error login:", err);
      showError("Error de servidor.");
    }
  });

  const togglePassword = document.getElementById("togglePassword");
  if (togglePassword) {
    togglePassword.addEventListener("click", function () {
      const type =
        passwordInput.getAttribute("type") === "password" ? "text" : "password";
      passwordInput.setAttribute("type", type);
      this.classList.toggle("fa-eye");
      this.classList.toggle("fa-eye-slash");
    });
  }
});
