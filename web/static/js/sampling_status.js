/* =========================================================
   sampling_status.js
   Muestra el ritmo REAL de muestreo debajo del campo "Tiempo
   de muestreo".

   Ese campo es una PETICIÓN, no un hecho:

   * Por suscripción, el servidor OPC UA puede conceder un
     intervalo mayor que el pedido. No está obligado a aceptar
     10 ms.
   * Si la suscripción no sale, se cae a polling, y ahí el
     techo lo pone la latencia de red: pedir 20 ms sobre un
     enlace de 64 ms da 64 ms, sin que nada falle.

   Sin este aviso la diferencia se descubre tarde y mal: la
   curva sale con menos puntos de los esperados, el escalón se
   detecta corrido y la identificación da un ajuste pobre que
   parece culpa del modelo.

   No se corrige nada automáticamente. El backend ya reporta
   pedido contra concedido; aquí solo se hace visible para que
   la decisión sea del usuario.
   ========================================================= */

const SAMPLING_REFRESH_MS = 4000;

let samplingTimer = null;
let ultimaFirma = null;   // evita repintar cuando nada cambió


/** Convierte segundos a un texto legible en ms o s. */
function formatearPeriodo(segundos) {
  if (segundos === null || segundos === undefined) return "—";

  const ms = segundos * 1000;
  if (ms < 1000) {
    // Por debajo de 100 ms un decimal sí aporta: 20 ms vs 20.5 ms.
    return `${ms < 100 ? ms.toFixed(1).replace(/\.0$/, "") : Math.round(ms)} ms`;
  }
  return `${segundos.toFixed(segundos < 10 ? 2 : 1)} s`;
}


/**
 * Decide qué decir a partir del bloque `sampling` del status.
 * Devuelve `null` si no hay nada que mostrar (sin sesión OPC UA).
 */
function describirMuestreo(sampling) {
  if (!sampling || !sampling.mode) return null;

  const pedido   = sampling.requested_period_s;
  const real     = sampling.revised_period_s;
  const porSusc  = sampling.mode === "subscription";
  const cumple   = sampling.honored;

  const textoPedido = formatearPeriodo(pedido);
  const textoReal   = formatearPeriodo(real);

  // Todavía sin medición: acaba de arrancar y no hay dos muestras.
  if (real === null || real === undefined) {
    return {
      nivel: "",
      titulo: porSusc ? "Muestreo por suscripción" : "Muestreo por polling",
      detalle: `Pedido <code>${textoPedido}</code>. Midiendo el ritmo real...`,
    };
  }

  if (cumple) {
    return {
      nivel: "ok",
      titulo: porSusc
        ? `Suscripción OPC UA a ${textoReal}`
        : `Polling a ${textoReal}`,
      detalle: porSusc
        ? `El ctrlX muestrea y envía en lotes, así que el ritmo lo marca ` +
          `el PLC y no la red. Pedido <code>${textoPedido}</code>.`
        : `Una lectura por muestra. Se está cumpliendo el ` +
          `<code>${textoPedido}</code> pedido, pero el margen depende de la red.`,
    };
  }

  // Lo real no alcanza lo pedido: hay que decir cuánto y por qué.
  if (porSusc) {
    return {
      nivel: "warn",
      titulo: `El ctrlX concedió ${textoReal}, no los ${textoPedido} pedidos`,
      detalle:
        `El servidor OPC UA no está obligado a aceptar el intervalo pedido. ` +
        `La curva va a tener puntos cada <code>${textoReal}</code>. ` +
        `Puedes seguir así, o subir el tiempo de muestreo a ` +
        `<code>${textoReal}</code> para que lo configurado coincida con lo real.`,
    };
  }

  // El motivo viene del servidor y no siempre trae punto final; sin cerrarlo,
  // la frase siguiente se pega y se lee como una sola.
  const motivo = sampling.reason
    ? ` Motivo: ${sampling.reason.replace(/\s*\.?\s*$/, "")}.`
    : "";

  return {
    nivel: "warn",
    titulo: `Polling a ${textoReal}, no los ${textoPedido} pedidos`,
    detalle:
      `Sin suscripción, cada muestra cuesta un viaje de ida y vuelta, así que ` +
      `el periodo no puede bajar de la latencia de red.${motivo} ` +
      `Para bajar de ahí hace falta que el ctrlX acepte suscripciones.`,
  };
}


/** Pinta el bloque. `sampling` puede venir null. */
function renderSampling(sampling) {
  const caja = document.getElementById("samplingReal");
  if (!caja) return;

  const info = describirMuestreo(sampling);

  if (!info) {
    caja.hidden = true;
    ultimaFirma = null;
    return;
  }

  const firma = `${info.nivel}|${info.titulo}|${info.detalle}`;
  if (firma === ultimaFirma) return;
  ultimaFirma = firma;

  caja.hidden = false;
  caja.classList.remove("ok", "warn");
  if (info.nivel) caja.classList.add(info.nivel);

  document.getElementById("samplingHead").textContent  = info.titulo;
  document.getElementById("samplingDetail").innerHTML  = info.detalle;
}


/** Consulta el status y repinta. Silencioso: es información, no una acción. */
async function refrescarSampling() {
  try {
    const estado = await Backend.getOpcuaStatus();
    renderSampling(estado?.sampling);
  } catch (_) {
    // Sin backend no hay nada que decir sobre el ritmo; se deja lo último.
  }
}


/**
 * Repinta tras cambiar el tiempo de muestreo.
 *
 * El backend necesita un momento para reabrir la suscripción con el nuevo
 * intervalo, así que se consulta dos veces: una enseguida y otra después.
 */
function refrescarSamplingTrasCambio() {
  refrescarSampling();
  setTimeout(refrescarSampling, 1200);
}


document.addEventListener("DOMContentLoaded", () => {
  refrescarSampling();

  if (samplingTimer) clearInterval(samplingTimer);
  samplingTimer = setInterval(refrescarSampling, SAMPLING_REFRESH_MS);

  document
    .getElementById("sampleTime")
    ?.addEventListener("change", refrescarSamplingTrasCambio);
});


window.refrescarSampling = refrescarSampling;
window.renderSampling = renderSampling;
window.describirMuestreo = describirMuestreo;
