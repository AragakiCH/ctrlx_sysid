// web/static/js/login.js

document.addEventListener("DOMContentLoaded", () => {
  const ipSelect = document.getElementById("ipSelect");
  const ipHiddenInput = document.getElementById("ip");
  const loginForm = document.getElementById("loginForm");
  const errorDiv = document.getElementById("errorMessage");
  const errorText = document.getElementById("errorText");

  // --- 1. FUNCIÓN PARA DESCUBRIR HOSTS ---
  const discoverHosts = async () => {
    try {
      const response = await fetch("http://localhost:8000/api/opcua/discover");
      const devices = await response.json();

      // Limpiamos el select
      ipSelect.innerHTML = "";

      if (devices.length === 0) {
        ipSelect.innerHTML =
          '<option value="">No se encontraron dispositivos</option>';
        return;
      }

      // ... dentro de la función discoverHosts ...
      devices.forEach((device) => {
        const option = document.createElement("option");
        option.value = device.url;

        // Usamos texto más limpio. El estado lo manejaremos con el color en el CSS
        const statusText = device.tcp_ok ? "● Online" : "● Offline";
        option.textContent = `${device.host} ${statusText}`;

        // Si no hay conexión, lo ponemos en gris
        if (!device.tcp_ok) {
          option.style.color = "#888";
        } else {
          option.style.color = "#28a745"; // Color verde éxito
        }

        ipSelect.appendChild(option);
      });

      // Seteamos el primer valor en el hidden input por defecto
      ipHiddenInput.value = ipSelect.value;
    } catch (err) {
      console.error("Error descubriendo hosts:", err);
      ipSelect.innerHTML =
        '<option value="">Error al cargar dispositivos</option>';
    }
  };

  // Ejecutar al cargar
  discoverHosts();

  // Actualizar el input hidden cuando el usuario cambie de host
  ipSelect.addEventListener("change", (e) => {
    ipHiddenInput.value = e.target.value;
  });

  // --- 2. LÓGICA PARA VER/OCULTAR CONTRASEÑA ---
  const togglePassword = document.getElementById("togglePassword");
  const passwordInput = document.getElementById("password");

  if (togglePassword && passwordInput) {
    togglePassword.addEventListener("click", function () {
      const type =
        passwordInput.getAttribute("type") === "password" ? "text" : "password";
      passwordInput.setAttribute("type", type);
      this.classList.toggle("fa-eye");
      this.classList.toggle("fa-eye-slash");
    });
  }

  // --- 3. LÓGICA DE ENVÍO DE FORMULARIO ---
  if (loginForm) {
    loginForm.addEventListener("submit", async (e) => {
      e.preventDefault();

      // Ahora el "fullUrl" es directamente el value del select
      const fullUrl = ipSelect.value;
      const user = document.getElementById("user").value.trim();
      const pass = document.getElementById("password").value.trim();

      const payload = {
        user: user,
        password: pass,
        url: fullUrl, // Ya viene como opc.tcp://192.168.1.1:4840
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
          errorText.innerText = "Usuario o contraseña incorrectos";
          errorDiv.style.display = "flex";
        }
      } catch (err) {
        errorText.innerText = "Error de conexión con el servidor";
        errorDiv.style.display = "flex";
      }
    });
  }
});
