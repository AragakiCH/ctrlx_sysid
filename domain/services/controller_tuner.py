from __future__ import annotations

from domain.models.pid import PIDTuning
from domain.models.transfer_function import TransferFunctionModel


class ControllerTuner:
    def tune_fopdt(self, model: TransferFunctionModel) -> list[PIDTuning]:
        if model.model_type != "fopdt":
            return []

        k = model.gain
        tau = model.tau or 0.0
        dead_time = model.dead_time

        if abs(k) < 1e-9 or tau <= 1e-9:
            return []

        tunings: list[PIDTuning] = []

        # IMC / Lambda
        lam = max(dead_time, tau * 0.5, 1e-6)
        kp_imc = tau / (k * (lam + dead_time))
        ti_imc = tau + dead_time / 2.0
        td_imc = (tau * dead_time) / (2.0 * tau + dead_time) if (2.0 * tau + dead_time) > 1e-9 else 0.0
        ki_imc = kp_imc / ti_imc if ti_imc > 1e-9 else 0.0
        kd_imc = kp_imc * td_imc

        tunings.append(
            PIDTuning(
                method="IMC",
                kp=kp_imc,
                ki=ki_imc,
                kd=kd_imc,
            )
        )

        # Ziegler-Nichols reacción del proceso
        if dead_time > 1e-9:
            kp_zn = 1.2 * tau / (k * dead_time)
            ti_zn = 2.0 * dead_time
            td_zn = 0.5 * dead_time
            ki_zn = kp_zn / ti_zn if ti_zn > 1e-9 else 0.0
            kd_zn = kp_zn * td_zn

            tunings.append(
                PIDTuning(
                    method="Ziegler-Nichols",
                    kp=kp_zn,
                    ki=ki_zn,
                    kd=kd_zn,
                )
            )

        # SIMC
        tc = max(dead_time, tau * 0.5, 1e-6)
        kp_simc = (1.0 / k) * (tau / (tc + dead_time))
        ti_simc = min(tau, 4.0 * (tc + dead_time)) if dead_time > 0 else tau
        ki_simc = kp_simc / ti_simc if ti_simc > 1e-9 else 0.0

        tunings.append(
            PIDTuning(
                method="SIMC",
                kp=kp_simc,
                ki=ki_simc,
                kd=0.0,
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

        # Equivalencia simple SOPDT -> FOPDT
        tau_eq = max(tau1 + tau2, 1e-6)

        fopdt_equivalent = TransferFunctionModel(
            model_type="fopdt",
            gain=k,
            tau=tau_eq,
            dead_time=dead_time,
            tf_string=(
                f"SOPDT equivalente: K={k:.4f}, "
                f"tau_eq={tau_eq:.4f}, L={dead_time:.4f}"
            ),
        )

        tunings = self.tune_fopdt(fopdt_equivalent)

        # Renombrar métodos para que en la UI quede claro que vinieron del SOPDT
        renamed: list[PIDTuning] = []
        for tuning in tunings:
            renamed.append(
                PIDTuning(
                    method=f"{tuning.method} (SOPDT eq.)",
                    kp=tuning.kp,
                    ki=tuning.ki,
                    kd=tuning.kd,
                )
            )

        return renamed

    def tune_integrating(self, model: TransferFunctionModel) -> list[PIDTuning]:
        if model.model_type != "integrating":
            return []

        k = model.gain
        dead_time = model.dead_time

        if abs(k) < 1e-9:
            return []

        tunings: list[PIDTuning] = []

        lam = max(dead_time * 2.0, 1.0)
        kp = 1.0 / (k * (lam + dead_time))
        ti = 4.0 * (lam + dead_time)
        ki = kp / ti if ti > 1e-9 else 0.0

        tunings.append(
            PIDTuning(
                method="IMC-Integrating",
                kp=kp,
                ki=ki,
                kd=0.0,
            )
        )

        return tunings