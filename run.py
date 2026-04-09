import time

from application.services.identification_pipeline_service import IdentificationPipelineService
from application.services.identification_service import IdentificationService
from application.services.realtime_service import RealtimeService
from application.services.step_detector_service import StepDetectorService
from infrastructure.ctrlx.plc_reader import PLCReader


realtime_service = RealtimeService(max_buffer_size=500)
identification_service = IdentificationService()
step_detector_service = StepDetectorService(min_step_delta=1.0)
pipeline_service = IdentificationPipelineService(
    identification_service=identification_service,
    step_detector_service=step_detector_service,
)

_last_step_index = None
_min_separation_samples = 20


def on_sample(sample: dict) -> None:
    global _last_step_index

    realtime_service.add_sample(sample)
    latest = realtime_service.get_latest_sample()
    size = realtime_service.get_buffer_size()

    print(
        f"[{size}] "
        f"time={latest.get('time')} | "
        f"actuator={latest.get('actuator')} | "
        f"sensor={latest.get('sensor')} | "
        f"setpoint={latest.get('setpoint')}"
    )

    if size < 40:
        return

    try:
        series = realtime_service.get_signal_series()
        step_index = step_detector_service.find_latest_rising_step_index(series.actuator)

        if step_index is None:
            return

        if _last_step_index is not None and abs(step_index - _last_step_index) < _min_separation_samples:
            return

        result = pipeline_service.process_series(series)
        if result is None:
            return

        print("\n--- COMPARACION DE MODELOS ---")
        for model in result["models"]:
            print(
                f"{model['model_type'].upper()}: "
                f"K={model['gain']:.4f}, "
                f"Fit R2={model['fit_quality']:.4f}"
            )

            if model["model_type"] == "fopdt":
                print(f"  Tau={model['tau']:.4f}, L={model['dead_time']:.4f}")

            elif model["model_type"] == "sopdt":
                print(
                    f"  Tau1={model['tau1']:.4f}, "
                    f"Tau2={model['tau2']:.4f}, "
                    f"L={model['dead_time']:.4f}"
                )

            elif model["model_type"] == "integrating":
                print(f"  L={model['dead_time']:.4f}")

            for pid in model["pid_tunings"]:
                print(
                    f"  {pid['method']}: "
                    f"Kp={pid['kp']:.4f}, Ki={pid['ki']:.4f}, Kd={pid['kd']:.4f}"
                )

        print(f"GANADOR: {result['winner'].upper()}")
        print("-------------------------------\n")

        _last_step_index = step_index

    except Exception as exc:
        print(f"[IDENT ERROR] {exc}")


if __name__ == "__main__":
    reader = PLCReader(
        url="opc.tcp://192.168.1.1:4840",
        user="boschrexroth",
        password="boschrexroth",
        on_sample=on_sample,
        period_s=0.2,
    )
    reader.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        reader.stop()