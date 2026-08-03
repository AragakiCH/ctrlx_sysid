from __future__ import annotations

from domain.models.pid import PIDTuning
from domain.models.transfer_function import TransferFunctionModel


class ControllerTuner:
    """
    Calcula constantes PID a partir del modelo identificado.

    Todos los métodos devuelven Kp, Ti y Td (forma estándar) y de ahí
    se derivan Ki y Kd, que es lo que consume la UI y el bloque PID.
    """

    # Piso numérico para evitar divisiones por cero cuando L -> 0.
    MIN_DEAD_TIME = 1e-3

    def tune_fopdt(self, model: TransferFunctionModel) -> list[PIDTuning]:
        if model.model_type != "fopdt":
            return []

        k = model.gain
        tau = model.tau or 0.0
        dead_time = model.dead_time

        if abs(k) < 1e-9 or tau <= 1e-9:
            return []

        return self._tune_from_fopdt_params(k=k, tau=tau, dead_time=dead_time)

    def _tune_from_fopdt_params(
        self,
        k: float,
        tau: float,
        dead_time: float,
        suffix: str = "",
    ) -> list[PIDTuning]:
        l = max(dead_time, self.MIN_DEAD_TIME)
        tau = max(tau, 1e-6)

        tunings: list[PIDTuning] = []

        def name(base: str) -> str:
            return f"{base}{suffix}"

        # ---------------- IMC / Lambda ----------------
        # lambda_c es la constante de tiempo de lazo cerrado deseada.
        lambda_c = max(0.25 * tau, 0.8 * l)
        kp_imc = tau / (k * (lambda_c + l))
        ti_imc = tau
        td_imc = 0.0

        tunings.append(
            PIDTuning.from_standard(
                method=name("IMC"),
                kp=kp_imc,
                ti=ti_imc,
                td=td_imc,
                lambda_c=lambda_c,
                description="IMC / Lambda — Robusto, recomendado para procesos lentos",
            )
        )

        # ---------------- Ziegler-Nichols lazo abierto ----------------
        # R = K/tau es la pendiente de reacción del proceso.
        r = k / tau
        if abs(r) > 1e-12:
            kp_zn = 1.2 / (r * l)
            ti_zn = 2.0 * l
            td_zn = 0.5 * l

            tunings.append(
                PIDTuning.from_standard(
                    method=name("Ziegler-Nichols"),
                    kp=kp_zn,
                    ti=ti_zn,
                    td=td_zn,
                    description="ZN lazo abierto — Agresivo, buena velocidad de respuesta",
                )
            )

        # ---------------- Cohen-Coon ----------------
        ratio = l / tau
        if ratio > 1e-9:
            kp_cc = (1.35 / (k * ratio)) * (1.0 + 0.18 * ratio / (1.0 + 0.185 * ratio))

            if ratio < 1.0:
                ti_cc = l * (2.5 - 2.0 * ratio) / (1.0 - 0.39 * ratio)
            else:
                ti_cc = 2.5 * l

            ti_cc = max(ti_cc, 0.01)
            td_cc = 0.37 * l / (1.0 + 0.185 * ratio)

            tunings.append(
                PIDTuning.from_standard(
                    method=name("Cohen-Coon"),
                    kp=kp_cc,
                    ti=ti_cc,
                    td=td_cc,
                    description="Cohen-Coon — Balance entre rapidez y estabilidad",
                )
            )

        # ---------------- SIMC (Skogestad) ----------------
        # Regla estándar: tc = L  =>  Kc = tau / (K * 2L), Ti = min(tau, 4*(tc+L))
        tc = l
        kp_simc = tau / (k * (tc + l))
        ti_simc = min(tau, 4.0 * (tc + l))

        tunings.append(
            PIDTuning.from_standard(
                method=name("SIMC"),
                kp=kp_simc,
                ti=ti_simc,
                td=0.0,
                lambda_c=tc,
                description="SIMC (Skogestad) — Excelente rechazo de perturbaciones",
            )
        )

        return tunings

    def tune_sopdt(self, model: TransferFunctionModel) -> list[PIDTuning]:
        if model.model_type != "sopdt":
            return []

        k = model.gain
        tau1 = model.tau1 or 0.0
        tau2 = model.tau2 or 0.0
        dead_time = model.dead_time

        if abs(k) < 1e-9:
            return []

        if tau1 <= 1e-9 and tau2 <= 1e-9:
            return []

        # Media-regla de Skogestad: la constante rápida se reparte entre
        # la constante dominante y el tiempo muerto efectivo.
        tau_fast, tau_slow = sorted([tau1, tau2])
        tau_eq = max(tau_slow + tau_fast / 2.0, 1e-6)
        dead_time_eq = dead_time + tau_fast / 2.0

        return self._tune_from_fopdt_params(
            k=k,
            tau=tau_eq,
            dead_time=dead_time_eq,
            suffix=" (SOPDT eq.)",
        )

    def tune_integrating(self, model: TransferFunctionModel) -> list[PIDTuning]:
        if model.model_type != "integrating":
            return []

        k = model.gain
        dead_time = model.dead_time

        if abs(k) < 1e-9:
            return []

        l = max(dead_time, self.MIN_DEAD_TIME)
        tunings: list[PIDTuning] = []

        # SIMC para proceso integrador: Kc = 1/(K*(tc+L)), Ti = 4*(tc+L), tc = L
        tc = l
        kp_simc = 1.0 / (k * (tc + l))
        ti_simc = 4.0 * (tc + l)

        tunings.append(
            PIDTuning.from_standard(
                method="SIMC-Integrating",
                kp=kp_simc,
                ti=ti_simc,
                td=0.0,
                lambda_c=tc,
                description="SIMC integrador — Ajuste por defecto para procesos de nivel",
            )
        )

        # IMC clásico para integrador puro (más conservador)
        lambda_c = 2.0 * l
        kp_imc = (2.0 * lambda_c + l) / (k * (lambda_c + l) ** 2)
        ti_imc = 2.0 * lambda_c + l

        tunings.append(
            PIDTuning.from_standard(
                method="IMC-Integrating",
                kp=kp_imc,
                ti=ti_imc,
                td=0.0,
                lambda_c=lambda_c,
                description="IMC integrador — Más conservador, menos sobreimpulso",
            )
        )

        return tunings

    def tune(self, model: TransferFunctionModel) -> list[PIDTuning]:
        """Despacha al método correcto según el tipo de modelo."""
        if model.model_type == "fopdt":
            return self.tune_fopdt(model)
        if model.model_type == "sopdt":
            return self.tune_sopdt(model)
        if model.model_type == "integrating":
            return self.tune_integrating(model)
        return []
