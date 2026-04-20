from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from infrastructure.ctrlx.opcua_client import CtrlxOpcUaClient
from infrastructure.ctrlx.plc_reader import PLCReader


class OpcUaSessionService:
    def __init__(
        self,
        on_sample: Callable[[dict], None],
        period_s: float = 0.2,
        reset_runtime_state: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_sample = on_sample
        self._period_s = period_s
        self._reset_runtime_state = reset_runtime_state

        self._lock = threading.RLock()

        self._reader: Optional[PLCReader] = None
        self._current_url: Optional[str] = None
        self._current_user: Optional[str] = None
        self._current_password: Optional[str] = None

        self._last_error: Optional[str] = None
        self._last_login_ts: Optional[float] = None

    def _validate_connection(self, url: str, user: str, password: str) -> None:
        opc = CtrlxOpcUaClient(url=url, user=user, password=password)

        try:
            print(f"[LOGIN] Probando OPC UA url={url} user={user}")
            opc.connect()
            print("[LOGIN] OPC UA connect OK")

            root = opc.get_root_node()
            plc_prg = opc.browse_by_names(
                root,
                "Objects",
                "Datalayer",
                "plc",
                "app",
                "Application",
                "sym",
                "PLC_PRG",
            )

            if plc_prg is None:
                raise RuntimeError(
                    "Conectó al OPC UA, pero no se encontró PLC_PRG. "
                    "Revisa Symbol Configuration o la ruta del árbol OPC UA."
                )

            print("[LOGIN] PLC_PRG encontrado OK")

        except Exception as exc:
            print(f"[LOGIN] ERROR REAL: {exc}")
            raise
        finally:
            opc.disconnect()

    def login(self, url: str, user: str, password: str) -> dict:
        clean_url = (url or "").strip()
        clean_user = (user or "").strip()
        clean_password = password or ""

        if not clean_url:
            raise ValueError("Falta la URL OPC UA.")
        if not clean_user:
            raise ValueError("Falta el usuario OPC UA.")
        if not clean_password:
            raise ValueError("Falta la contraseña OPC UA.")

        # Primero valida conexión real
        self._validate_connection(
            url=clean_url,
            user=clean_user,
            password=clean_password,
        )

        with self._lock:
            # Detener reader anterior si existía
            if self._reader is not None:
                try:
                    self._reader.stop()
                except Exception:
                    pass
                finally:
                    self._reader = None

            # Limpiar buffer/resultados viejos para no mezclar sesiones
            if self._reset_runtime_state is not None:
                self._reset_runtime_state()

            self._current_url = clean_url
            self._current_user = clean_user
            self._current_password = clean_password
            self._last_error = None
            self._last_login_ts = time.time()

            self._reader = PLCReader(
                url=clean_url,
                user=clean_user,
                password=clean_password,
                on_sample=self._on_sample,
                period_s=self._period_s,
            )
            self._reader.start()

            return {
                "ok": True,
                "url": self._current_url,
                "user": self._current_user,
                "started": True,
            }

    def logout(self, clear_runtime: bool = True) -> dict:
        with self._lock:
            if self._reader is not None:
                try:
                    self._reader.stop()
                except Exception:
                    pass
                finally:
                    self._reader = None

            self._current_url = None
            self._current_user = None
            self._current_password = None
            self._last_login_ts = None

            if clear_runtime and self._reset_runtime_state is not None:
                self._reset_runtime_state()

            return {"ok": True, "logged_out": True}

    def stop(self) -> None:
        with self._lock:
            if self._reader is not None:
                try:
                    self._reader.stop()
                except Exception:
                    pass
                finally:
                    self._reader = None

    def get_status(
        self,
        buffer_size: int = 0,
        has_latest: bool = False,
        has_identification: bool = False,
    ) -> dict:
        with self._lock:
            reader_running = bool(
                self._reader is not None
                and self._reader._thread is not None
                and self._reader._thread.is_alive()
            )

            return {
                "authenticated": bool(self._current_url and self._current_user),
                "connected": reader_running,
                "url": self._current_url,
                "user": self._current_user,
                "buffer_size": buffer_size,
                "has_latest": has_latest,
                "has_identification": has_identification,
                "last_error": self._last_error,
                "last_login_ts": self._last_login_ts,
            }

    @property
    def current_url(self) -> Optional[str]:
        return self._current_url

    @property
    def current_user(self) -> Optional[str]:
        return self._current_user

    @property
    def is_authenticated(self) -> bool:
        return bool(self._current_url and self._current_user)