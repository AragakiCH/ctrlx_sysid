document.addEventListener("DOMContentLoaded", () => {
  const ipSelect = document.getElementById("ipSelect");
  const programSelect = document.getElementById("programSelect");
  const userInput = document.getElementById("user");
  const passwordInput = document.getElementById("password");
  const btnDiscover = document.getElementById("btnDiscoverPrograms");
  const loginForm = document.getElementById("loginForm");
  const errorDiv = document.getElementById("errorMessage");
  const errorText = document.getElementById("errorText");

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

  btnDiscover.addEventListener("click", async () => {
    const url = ipSelect.value;
    const user = userInput.value.trim();
    const password = passwordInput.value;

    if (!url || !user || !password) {
      errorText.innerText =
        "Complete IP, Usuario y Clave para buscar programas.";
      errorDiv.style.display = "flex";
      return;
    }

    programSelect.innerHTML = '<option value="">Buscando...</option>';
    errorDiv.style.display = "none";

    try {
      const response = await fetch(`${API_BASE}/api/opcua/discover-programs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, user, password }),
      });

      const data = await response.json();
      programSelect.innerHTML = "";

      if (data.ok && Array.isArray(data.programs) && data.programs.length > 0) {
        data.programs.forEach((prog) => {
          const opt = document.createElement("option");
          opt.value = prog;
          opt.textContent = prog;
          programSelect.appendChild(opt);
        });

        btnDiscover.remove();
        programSelect.style.width = "100%";
      } else {
        programSelect.innerHTML =
          '<option value="">No se hallaron programas</option>';
        errorText.innerText = data.detail || "Credenciales incorrectas o no hay programas.";
        errorDiv.style.display = "flex";
      }
    } catch (err) {
      console.error("Error discover-programs:", err);
      programSelect.innerHTML = '<option value="">Error</option>';
      errorText.innerText = "Error de conexión al buscar programas.";
      errorDiv.style.display = "flex";
    }
  });

  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const programName = programSelect.value;

    if (!programName || programName.includes("...")) {
      errorText.innerText = "Primero obtenga y seleccione un programa.";
      errorDiv.style.display = "flex";
      return;
    }

    const payload = {
      user: userInput.value.trim(),
      password: passwordInput.value,
      url: ipSelect.value,
      program_name: programName,
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
        window.location.href = `${API_BASE}/app`;
      } else {
        errorText.innerText = data.detail || "Error en el inicio de sesión final.";
        errorDiv.style.display = "flex";
      }
    } catch (err) {
      console.error("Error login:", err);
      errorText.innerText = "Error de servidor.";
      errorDiv.style.display = "flex";
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