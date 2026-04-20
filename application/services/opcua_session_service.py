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

    def _validate_connection(self, url: str, user: str, password: str) -> str:
        opc = CtrlxOpcUaClient(url=url, user=user, password=password)

        try:
            print(f"[LOGIN] Probando OPC UA url={url} user={user}")
            opc.connect()
            print("[LOGIN] OPC UA connect OK")

            root = opc.get_root_node()

            # Llegar hasta el nodo sym
            sym_node = opc.browse_by_names(
                root,
                "Objects",
                "Datalayer",
                "plc",
                "app",
                "Application",
                "sym",
            )

            if sym_node is None:
                raise RuntimeError(
                    "Conectó al OPC UA, pero no se encontró el nodo 'sym'. "
                    "Revisa Symbol Configuration o la ruta del árbol OPC UA."
                )

            print("[LOGIN] Nodo sym encontrado OK")

            # Obtener hijos de sym (programas IEC expuestos)
            children = sym_node.get_children()
            if not children:
                raise RuntimeError(
                    "Conectó al OPC UA, pero el nodo 'sym' no tiene programas expuestos."
                )

            print(f"[LOGIN] Hijos detectados en sym: {len(children)}")

            named_children = []
            for child in children:
                try:
                    browse_name = child.get_browse_name().Name
                    named_children.append((browse_name, child))
                    print(f"[LOGIN] Programa detectado en sym: {browse_name}")
                except Exception as exc:
                    print(f"[LOGIN] No se pudo leer browse_name de un hijo: {exc}")

            if not named_children:
                raise RuntimeError(
                    "Conectó al OPC UA, pero no se pudo obtener el nombre de los programas dentro de 'sym'."
                )

            # Nombres preferidos conocidos
            preferred_names = ["PLC_PRG", "PRG_Main"]

            selected_program_name = None
            selected_program_node = None

            for preferred in preferred_names:
                for name, child in named_children:
                    if name == preferred:
                        selected_program_name = name
                        selected_program_node = child
                        break
                if selected_program_node is not None:
                    break

            # Si no encuentra uno conocido, toma el primero disponible
            if selected_program_node is None:
                selected_program_name, selected_program_node = named_children[0]
                print(
                    f"[LOGIN] No se encontró un nombre preferido. "
                    f"Se usará el primer programa disponible: {selected_program_name}"
                )
            else:
                print(f"[LOGIN] Programa preferido encontrado: {selected_program_name}")

            # Validación final simple
            if selected_program_node is None:
                raise RuntimeError(
                    "Conectó al OPC UA, pero no se pudo determinar un programa válido dentro de 'sym'."
                )

            print(f"[LOGIN] Programa seleccionado: {selected_program_name}")

            return selected_program_name

        except Exception as exc:
            print(f"[LOGIN] ERROR REAL: {exc}")
            raise

        finally:
            try:
                opc.disconnect()
            except Exception as exc:
                print(f"[LOGIN] Error al desconectar OPC UA: {exc}")

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