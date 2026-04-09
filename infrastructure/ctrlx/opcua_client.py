from __future__ import annotations

import os
from typing import Optional, Tuple

from opcua import Client


class OpcUaConnectionError(Exception):
    pass


class CtrlxOpcUaClient:
    def __init__(
        self,
        url: str,
        user: str = "",
        password: str = "",
        timeout_connect: Optional[float] = None,
    ) -> None:
        self.url = url
        self.user = user
        self.password = password

        self.timeout_connect = timeout_connect or float(
            os.getenv("OPCUA_TIMEOUT_CONNECT", "5.0")
        )

        self.client_cert = os.getenv("OPCUA_CLIENT_CERT", "")
        self.client_key = os.getenv("OPCUA_CLIENT_KEY", "")

        self._client: Optional[Client] = None

    @staticmethod
    def _opc_host_port(url: str) -> Tuple[str, str]:
        x = url.split("://", 1)[-1]
        hostport = x.split("/", 1)[0]
        if ":" in hostport:
            host, port = hostport.split(":", 1)
        else:
            host, port = hostport, "4840"
        return host, port

    @staticmethod
    def _replace_host(endpoint_url: str, new_host: str) -> str:
        old_host, _ = CtrlxOpcUaClient._opc_host_port(endpoint_url)
        return endpoint_url.replace(old_host, new_host, 1)

    @staticmethod
    def _tokens_of(endpoint) -> set[str]:
        return {t.TokenType.name for t in (endpoint.UserIdentityTokens or [])}

    def _score_endpoint(self, endpoint) -> int:
        tokens = self._tokens_of(endpoint)

        if self.user and "UserName" not in tokens:
            return -10_000

        mode = int(endpoint.SecurityMode)
        sp = (endpoint.SecurityPolicyUri or "").lower()

        if "basic256sha256" in sp:
            sp_score = 30
        elif "basic256" in sp:
            sp_score = 20
        elif "none" in sp:
            sp_score = 0
        else:
            sp_score = 10

        return mode * 100 + sp_score

    @staticmethod
    def _policy_mode_from_endpoint(endpoint) -> Tuple[str, str]:
        uri = (endpoint.SecurityPolicyUri or "").lower()

        if uri.endswith("#none") or "none" in uri:
            policy = "None"
        elif "basic256sha256" in uri:
            policy = "Basic256Sha256"
        elif "basic256" in uri:
            policy = "Basic256"
        else:
            policy = "Basic256Sha256"

        mode_int = int(endpoint.SecurityMode)
        if mode_int == 2:
            mode = "SignAndEncrypt"
        elif mode_int == 1:
            mode = "Sign"
        else:
            mode = "None"

        return policy, mode

    def _build_probe_client(self) -> Client:
        probe = Client(self.url, timeout=self.timeout_connect)
        if self.user:
            probe.set_user(self.user)
            probe.set_password(self.password)
        return probe

    def _discover_best_endpoint(self):
        probe = self._build_probe_client()
        connected = False

        try:
            probe.connect()
            connected = True
            endpoints = probe.get_endpoints()
        except Exception as exc:
            raise OpcUaConnectionError(
                f"No se pudieron obtener endpoints desde {self.url}: {exc}"
            ) from exc
        finally:
            if connected:
                try:
                    probe.disconnect()
                except Exception:
                    pass

        if not endpoints:
            raise OpcUaConnectionError("El servidor OPC UA no devolvió endpoints.")

        best = max(endpoints, key=self._score_endpoint, default=None)
        if not best or self._score_endpoint(best) < 0:
            raise OpcUaConnectionError(
                "No existe un endpoint compatible con autenticación UserName."
            )

        return best

    def connect(self) -> Client:
        best = self._discover_best_endpoint()

        policy, mode = self._policy_mode_from_endpoint(best)
        endpoint_url = best.EndpointUrl or self.url

        base_host, _ = self._opc_host_port(self.url)
        endpoint_url = self._replace_host(endpoint_url, base_host)

        client = Client(endpoint_url, timeout=self.timeout_connect)

        if policy != "None" and mode != "None":
            client.set_security_string(f"{policy},{mode}")

            if self.client_cert and self.client_key:
                client.load_client_certificate(self.client_cert)
                client.load_private_key(self.client_key)

        if self.user:
            client.set_user(self.user)
            client.set_password(self.password)

        try:
            client.connect()
        except Exception as exc:
            raise OpcUaConnectionError(
                f"No se pudo conectar al endpoint OPC UA {endpoint_url}: {exc}"
            ) from exc

        self._client = client
        return client

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            finally:
                self._client = None

    @property
    def client(self) -> Client:
        if self._client is None:
            raise OpcUaConnectionError("No hay cliente OPC UA conectado.")
        return self._client

    def get_root_node(self):
        return self.client.get_root_node()

    def browse_by_names(self, root, *names):
        cur = root
        for name in names:
            found = None
            for child in cur.get_children():
                if child.get_browse_name().Name == name:
                    found = child
                    break
            if not found:
                return None
            cur = found
        return cur

    def read_value(self, node):
        try:
            value_node = node.get_child(["2:Value"])
            return value_node.get_value()
        except Exception:
            return node.get_value()