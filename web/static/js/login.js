document.addEventListener("DOMContentLoaded", () => {
  const ipSelect       = document.getElementById("ipSelect");
  const programSelect  = document.getElementById("programSelect");
  const userInput      = document.getElementById("user");
  const passwordInput  = document.getElementById("password");
  const btnDiscover    = document.getElementById("btnDiscoverPrograms");
  const loginForm      = document.getElementById("loginForm");
  const errorDiv       = document.getElementById("errorMessage");
  const errorText      = document.getElementById("errorText");

  function getApiBase() {
    if (window.State?.API_BASE) {
      return window.State.API_BASE.replace(/\/$/, "");
    }
    const origin = window.location.origin;
    const parts  = window.location.pathname.split("/").filter(Boolean);
    const prefix = parts.length ? `/${parts[0]}` : "";
    return `${origin}${prefix}`;
  }

  const API_BASE = getApiBase();
  console.log("API_BASE =", API_BASE);

  function showError(msg) {
    errorText.innerText = msg;
    errorDiv.style.display = "flex";
  }
  function clearError() { errorDiv.style.display = "none"; }

  function credentials() {
    return {
      url:      ipSelect.value,
      user:     userInput.value.trim(),
      password: passwordInput.value,
    };
  }

  // ==================== HOSTS ====================
  const discoverHosts = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/opcua/discover`);
      const devices  = await response.json();
      ipSelect.innerHTML = "";

      if (devices.length === 0) {
        ipSelect.innerHTML = '<option value="">No hay dispositivos</option>';
        return;
      }

      devices.forEach((d) => {
        const opt = document.createElement("option");
        opt.value = d.url;
        opt.textContent = `${d.host} ●`;
        opt.style.color = d.tcp_ok ? "#28a745" : "#888888";
        if (d.tcp_ok) opt.style.fontWeight = "bold";
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
      showError("Complete Dispositivo, Usuario y Clave para buscar programas.");
      return;
    }

    programSelect.innerHTML = '<option value="">Buscando...</option>';
    clearError();

    try {
      const response = await fetch(`${API_BASE}/api/opcua/discover-programs`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ url, user, password }),
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

        // Si solo hay uno, selecciónelo directamente.
        if (data.programs.length === 1) {
          programSelect.value = data.programs[0];
        }
      } else {
        programSelect.innerHTML = '<option value="">No se hallaron programas</option>';
        showError(data.detail || "Credenciales incorrectas o no hay programas.");
      }
    } catch (err) {
      console.error("Error discover-programs:", err);
      programSelect.innerHTML = '<option value="">Error</option>';
      showError("Error de conexión al buscar programas.");
    }
  });

  // ==================== LOGIN ====================
  // Se envía sin `mapping`: el backend usa sus alias por defecto
  // (rActuator, rSensor, rTimeSec, rSetPoint, uiSignalType). El
  // ajuste del mapeo, si hiciera falta, se hace en la vista principal
  // (paso 1 — dropdowns de variables).
  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const programName = programSelect.value;

    if (!programName) {
      showError("Primero obtenga y seleccione un programa.");
      return;
    }

    const { url, user, password } = credentials();

    const payload = {
      user,
      password,
      url,
      program_name: programName,
    };

    try {
      const response = await fetch(`${API_BASE}/api/opcua/login`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(payload),
      });

      const data = await response.json();

      if (response.ok) {
        localStorage.setItem("isAuth", "true");
        localStorage.setItem("plcProgram", programName);
        window.location.href = `${API_BASE}/app`;
      } else {
        showError(data.detail || "Error en el inicio de sesión.");
      }
    } catch (err) {
      console.error("Error login:", err);
      showError("Error de servidor.");
    }
  });

  // ==================== TOGGLE PASSWORD ====================
  const togglePassword = document.getElementById("togglePassword");
  if (togglePassword) {
    togglePassword.addEventListener("click", function () {
      const type = passwordInput.getAttribute("type") === "password" ? "text" : "password";
      passwordInput.setAttribute("type", type);
      this.classList.toggle("fa-eye");
      this.classList.toggle("fa-eye-slash");
    });
  }
});
