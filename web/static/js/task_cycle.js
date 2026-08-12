/* =========================================================
   task_cycle.js
   Ciclo de tarea del MainTask, visto y ajustado desde la vista.

   El `Intervalo` del MainTask vive en la configuración de
   tareas del proyecto de ctrlX PLC Engineering. NO es una
   variable del programa: no se exporta en la Configuración de
   símbolos, no cuelga de `sym` y por tanto no hay ningún nodo
   OPC UA que escribir. Por OPC UA el ciclo de tarea no existe.

   Lo que sí funciona es una VARIABLE PUENTE en PLC_PRG que el
   código IEC del PLC lee para ajustar el intervalo. La app
   escribe esa variable; el cambio de tarea lo hace el PLC.
   Ver docs/CICLO_DE_TAREA.md.

   Por qué el ajuste es solo hacia abajo: el ciclo de tarea es
   cada cuánto el PLC CALCULA, y el muestreo cada cuánto la app
   MIRA. Mirar más despacio de lo que calcula es normal y no
   cuesta nada. Mirar más rápido devuelve valores repetidos,
   porque la variable todavía no cambió. Solo ese caso justifica
   tocar el PLC.
   ========================================================= */

const TASK_CYCLE_REFRESH_MS = 5000;

let taskCycleTimer = null;
let ultimaFirmaCiclo = null;


/** Consulta el estado del ciclo. Devuelve null si el backend no responde. */
async function leerCicloDeTarea() {
  try {
    return await apiCall("/api/plc/task-cycle");
  } catch (_) {
    return null;
  }
}


/** Qué decir a partir del estado del ciclo. `null` si no hay nada que mostrar. */
function describirCiclo(estado) {
  if (!estado || !estado.variable) return null;

  if (estado.cycle_ms === null || estado.cycle_ms === undefined) {
    const motivo = estado.reason ? ` ${estado.reason}` : "";
    return {
      nivel: "warn",
      titulo: `No se pudo leer '${estado.variable}'`,
      detalle:
        `La variable puente tiene que existir en el programa y estar ` +
        `expuesta en la Configuración de símbolos.${motivo}`,
    };
  }

  const ciclo = `${estado.cycle_ms} ms`;

  if (estado.oversampling) {
    const arreglo = estado.sync_enabled
      ? `Con la casilla marcada, la app baja el ciclo al guardar el muestreo.`
      : `Marca la casilla para que la app baje el ciclo, o sube el tiempo ` +
        `de muestreo hasta <code>${ciclo}</code>.`;

    return {
      nivel: "warn",
      titulo: `El MainTask calcula cada ${ciclo}: estás mirando más rápido`,
      detalle:
        `Por debajo del ciclo de tarea la variable todavía no cambió, así que ` +
        `llegan muestras repetidas. La curva sale escalonada y la ` +
        `identificación lee mal el tiempo muerto. ${arreglo}`,
    };
  }

  // El motivo viene del servidor y a veces ya trae punto: sin quitarlo se
  // duplica al cerrar la frase.
  const motivo = (estado.reason || "el servidor no acepta escribirla")
    .replace(/\s*\.?\s*$/, "");

  const escritura = estado.writable ? "" : ` Solo lectura: ${motivo}.`;

  return {
    nivel: "ok",
    titulo: `MainTask calculando cada ${ciclo}`,
    detalle:
      `El PLC calcula al menos tan rápido como la app mira, que es lo que ` +
      `hace falta.${escritura}`,
  };
}


function renderCiclo(estado) {
  const caja = document.getElementById("taskCycleBox");
  if (!caja) return;

  const info = describirCiclo(estado);

  if (!info) {
    caja.hidden = true;
    ultimaFirmaCiclo = null;
    return;
  }

  const firma = `${info.nivel}|${info.titulo}|${info.detalle}`;
  if (firma === ultimaFirmaCiclo) return;
  ultimaFirmaCiclo = firma;

  caja.hidden = false;
  caja.classList.remove("ok", "warn");
  caja.classList.add(info.nivel);

  document.getElementById("taskCycleHead").textContent = info.titulo;
  document.getElementById("taskCycleDetail").innerHTML = info.detalle;
}


/** Refleja en los controles lo que el backend tiene guardado. */
function aplicarEstadoCicloAControles(estado) {
  if (!estado) return;

  const check = document.getElementById("syncTaskCycle");
  if (check) check.checked = Boolean(estado.sync_enabled);

  const select = document.getElementById("varTaskCycle");
  if (select && estado.variable && select.value !== estado.variable) {
    // Puede que los desplegables aún no se hayan poblado con el catálogo.
    if ([...select.options].some((o) => o.value === estado.variable)) {
      select.value = estado.variable;
    }
  }
}


async function refrescarCicloDeTarea() {
  const estado = await leerCicloDeTarea();
  renderCiclo(estado);
  aplicarEstadoCicloAControles(estado);
}


/** Manda la variable puente y/o el interruptor al backend. */
async function guardarConfigCiclo() {
  const variable = document.getElementById("varTaskCycle")?.value ?? "";
  const sync = document.getElementById("syncTaskCycle")?.checked ?? false;

  try {
    const estado = await apiCall("/api/plc/task-cycle/config", {
      method: "POST",
      body: { variable, sync_enabled: sync },
    });

    renderCiclo(estado);
    aplicarEstadoCicloAControles(estado);
  } catch (err) {
    console.warn("No se pudo configurar el ciclo de tarea:", err.message);
    setStatus(`Ciclo de tarea: ${err.message}`, "error");
  }
}


/**
 * Puebla el desplegable con el catálogo de variables del PLC.
 *
 * Se expone en `window` porque quien conoce el catálogo es websocket.js, que
 * lo recibe en cada muestra.
 */
function poblarVariablesDeCiclo(nombres) {
  const select = document.getElementById("varTaskCycle");
  if (!select || !Array.isArray(nombres) || !nombres.length) return;

  const elegido = select.value;
  const actuales = [...select.options].map((o) => o.value);

  // Repoblar en cada muestra tiraría la selección del usuario a media edición.
  if (actuales.length === nombres.length + 1 && nombres.every((n) => actuales.includes(n))) {
    return;
  }

  select.innerHTML = '<option value="">— sin vincular —</option>';
  nombres.forEach((nombre) => {
    const opcion = document.createElement("option");
    opcion.value = nombre;
    opcion.textContent = nombre;
    select.appendChild(opcion);
  });

  if (elegido && nombres.includes(elegido)) select.value = elegido;
}


document.addEventListener("DOMContentLoaded", () => {
  refrescarCicloDeTarea();

  if (taskCycleTimer) clearInterval(taskCycleTimer);
  taskCycleTimer = setInterval(refrescarCicloDeTarea, TASK_CYCLE_REFRESH_MS);

  document.getElementById("varTaskCycle")?.addEventListener("change", guardarConfigCiclo);
  document.getElementById("syncTaskCycle")?.addEventListener("change", guardarConfigCiclo);

  // Tras cambiar el muestreo, el backend puede haber bajado el ciclo: hay que
  // volver a leerlo para que el aviso no se quede con el valor viejo.
  document.getElementById("sampleTime")?.addEventListener("change", () => {
    setTimeout(refrescarCicloDeTarea, 1200);
  });
});


window.refrescarCicloDeTarea = refrescarCicloDeTarea;
window.poblarVariablesDeCiclo = poblarVariablesDeCiclo;
window.describirCiclo = describirCiclo;
window.renderCiclo = renderCiclo;
