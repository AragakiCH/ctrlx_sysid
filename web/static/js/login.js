// web/static/js/login.js

document.addEventListener("DOMContentLoaded", () => {
  const ipSelect = document.getElementById("ipSelect");
  const programSelect = document.getElementById("programSelect");
  const userInput = document.getElementById("user");
  const passwordInput = document.getElementById("password");
  const btnDiscover = document.getElementById("btnDiscoverPrograms");
  const loginForm = document.getElementById("loginForm");
  const errorDiv = document.getElementById("errorMessage");
  const errorText = document.getElementById("errorText");

  // --- 1. FUNCIÓN PARA DESCUBRIR HOSTS AL INICIO ---
  // --- 1. FUNCIÓN PARA DESCUBRIR HOSTS AL INICIO ---
const discoverHosts = async () => {
  try {
    const response = await fetch("http://localhost:8000/api/opcua/discover");
    const devices = await response.json();
    ipSelect.innerHTML = "";
    
    if (devices.length === 0) {
      ipSelect.innerHTML = '<option value="">No hay dispositivos</option>';
      return;
    }

    devices.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.url;
      
      // Usamos el punto sólido que ya tenías
      opt.textContent = `${d.host} ●`;
      
      // Aplicamos el color según el estado
      if (d.tcp_ok) {
        opt.style.color = "#28a745"; // Verde Rexroth/Industrial
        opt.style.fontWeight = "bold";
      } else {
        opt.style.color = "#888888"; // Gris para desconectado
      }
      
      ipSelect.appendChild(opt);
    });
  } catch (err) {
    console.error("Error hosts:", err);
  }
};

  discoverHosts();

  // --- 2. BOTÓN PARA OBTENER PROGRAMAS (PRIMER POST) ---
  btnDiscover.addEventListener("click", async () => {
    const url = ipSelect.value;
    const user = userInput.value.trim();
    const password = passwordInput.value.trim();

    if (!url || !user || !password) {
      errorText.innerText =
        "Complete IP, Usuario y Clave para buscar programas.";
      errorDiv.style.display = "flex";
      return;
    }

    programSelect.innerHTML = '<option value="">Buscando...</option>';
    errorDiv.style.display = "none";

    try {
      // POST para obtener programas
      const response = await fetch(
        "http://localhost:8000/api/opcua/discover-programs",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, user, password }),
        },
      );

      const data = await response.json();
      programSelect.innerHTML = "";

      // ... dentro de la lógica del btnDiscover.addEventListener ...

      if (data.ok && Array.isArray(data.programs) && data.programs.length > 0) {
        data.programs.forEach((prog) => {
          const opt = document.createElement("option");
          opt.value = prog;
          opt.textContent = prog;
          programSelect.appendChild(opt);
        });

        // Eliminamos el botón
        btnDiscover.remove();

        // OPCIONAL: Reforzamos el ancho por JS por si las moscas
        programSelect.style.width = "100%";
      } else {
        programSelect.innerHTML =
          '<option value="">No se hallaron programas</option>';
        errorText.innerText = "Credenciales incorrectas o no hay programas.";
        errorDiv.style.display = "flex";
      }
    } catch (err) {
      programSelect.innerHTML = '<option value="">Error</option>';
      errorText.innerText = "Error de conexión al buscar programas.";
      errorDiv.style.display = "flex";
    }
  });

  // --- 3. LOGIN FINAL (SEGUNDO POST) ---
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
      password: passwordInput.value.trim(),
      url: ipSelect.value,
      program_name: programName,
    };

    try {
      const response = await fetch("http://localhost:8000/api/opcua/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (response.ok) {
        localStorage.setItem("isAuth", "true");
        window.location.href = "/app";
      } else {
        errorText.innerText = "Error en el inicio de sesión final.";
        errorDiv.style.display = "flex";
      }
    } catch (err) {
      errorText.innerText = "Error de servidor.";
      errorDiv.style.display = "flex";
    }
  });

  // Lógica de ver/ocultar password (opcional por si se te pasó)
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
