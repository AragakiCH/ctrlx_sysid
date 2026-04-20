window.State = {
  APP_PREFIX: window.APP_PREFIX || "",
  API_BASE: window.API_BASE || window.location.origin,
  WS_BASE: window.WS_BASE || window.location.origin.replace(/^http/, "ws"),
  charts: {
    actuator: null,
    sensor: null,
    comparison: null,
    bode: null
  },
  identificationResults: null
};