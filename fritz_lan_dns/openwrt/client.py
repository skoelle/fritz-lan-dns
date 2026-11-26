from __future__ import annotations

from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NULL_SESSION = "0" * 32
_SESSION_EXPIRED = -32002
_METHOD_NOT_FOUND = -32601
_OBJECT_NOT_FOUND = 4
_METHOD_MISSING = 3


class OpenWrtError(RuntimeError):
    pass


class OpenWrtNotFound(OpenWrtError):
    pass


class OpenWrtSessionExpired(OpenWrtError):
    pass


class OpenWrtClient:
    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        *,
        timeout: int = 25,
        verify_tls: bool = False,
    ) -> None:
        self.url = (url or "").rstrip("/")
        self.endpoint = (
            self.url if self.url.endswith("/ubus") else f"{self.url}/ubus"
        )
        self.user = user
        self.password = password
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "fritz-lan-dns/0.1"})
        self._session_id: str | None = None

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                self.endpoint,
                json=payload,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        except requests.RequestException as exc:
            raise OpenWrtError(f"OpenWrt nicht erreichbar: {exc}") from exc
        if response.status_code >= 400:
            raise OpenWrtError(f"OpenWrt /ubus lieferte HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise OpenWrtError("OpenWrt /ubus lieferte kein gültiges JSON") from exc
        if not isinstance(data, dict):
            raise OpenWrtError("OpenWrt /ubus lieferte eine unerwartete Antwort")
        error = data.get("error")
        if error:
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else error
            if code in (_SESSION_EXPIRED, _METHOD_NOT_FOUND):
                exc_type = (
                    OpenWrtSessionExpired
                    if code == _SESSION_EXPIRED
                    else OpenWrtNotFound
                )
                raise exc_type(f"OpenWrt JSON-RPC: {message}")
            raise OpenWrtError(f"OpenWrt JSON-RPC: {message}")
        return data

    def login(self) -> str:
        data = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "call",
                "params": [
                    NULL_SESSION,
                    "session",
                    "login",
                    {"username": self.user, "password": self.password},
                ],
            }
        )
        result = data.get("result") or []
        if len(result) < 2 or result[0] != 0:
            raise OpenWrtError(
                "OpenWrt-Login fehlgeschlagen (Zugangsdaten oder rpcd-ACL prüfen)"
            )
        session_id = result[1].get("ubus_rpc_session") if isinstance(result[1], dict) else None
        if not session_id:
            raise OpenWrtError("OpenWrt-Login lieferte keine Session")
        self._session_id = session_id
        return session_id

    def call(
        self,
        obj: str,
        method: str,
        arguments: dict[str, Any] | None = None,
        *,
        retry: bool = True,
    ) -> dict[str, Any]:
        if self._session_id is None:
            self.login()
        try:
            data = self._post(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "call",
                    "params": [self._session_id, obj, method, arguments or {}],
                }
            )
        except OpenWrtSessionExpired:
            if not retry:
                raise
            self.login()
            return self.call(obj, method, arguments, retry=False)
        result = data.get("result") or []
        if not result:
            raise OpenWrtError(f"OpenWrt {obj}.{method} lieferte kein Ergebnis")
        code = result[0]
        if code in (_METHOD_MISSING, _OBJECT_NOT_FOUND):
            raise OpenWrtNotFound(f"OpenWrt {obj}.{method} nicht verfügbar")
        if code != 0:
            raise OpenWrtError(f"OpenWrt {obj}.{method} fehlgeschlagen (Code {code})")
        if len(result) < 2 or not isinstance(result[1], dict):
            raise OpenWrtError(f"OpenWrt {obj}.{method} lieferte keine Daten")
        return result[1]

    def _call_first(self, method: str) -> dict[str, Any]:
        last: OpenWrtError | None = None
        for obj in ("luci-rpc", "luci"):
            try:
                return self.call(obj, method)
            except OpenWrtNotFound as exc:
                last = exc
                continue
        raise last or OpenWrtError(f"OpenWrt {method} nicht verfügbar")

    def get_dhcp_leases(self) -> dict[str, Any]:
        return self._call_first("getDHCPLeases")

    def get_host_hints(self) -> dict[str, Any]:
        return self._call_first("getHostHints")

    def get_static_leases(self) -> list[dict[str, Any]]:
        data = self.call("uci", "get", {"config": "dhcp"})
        values = data.get("values") or {}
        leases: list[dict[str, Any]] = []
        for section in values.values():
            if not isinstance(section, dict):
                continue
            if section.get(".type") != "host":
                continue
            name = str(section.get("name") or section.get("hostname") or "").strip()
            ip = str(section.get("ip") or "").strip()
            raw_macs = section.get("mac") or []
            if isinstance(raw_macs, str):
                raw_macs = [raw_macs]
            macs = [
                str(mac).strip().lower()
                for mac in raw_macs
                if str(mac).strip()
            ]
            if not name and not ip and not macs:
                continue
            leases.append({"name": name, "ip": ip, "macs": macs})
        return leases

    def get_mwan3_status(self) -> dict[str, Any]:
        return self.call("mwan3", "status", {})

    def get_network_interfaces(self) -> dict[str, Any]:
        return self.call("network.interface", "dump")

    def get_wireless_info(self, device: str) -> dict[str, Any]:
        return self.call("iwinfo", "info", {"device": device})
