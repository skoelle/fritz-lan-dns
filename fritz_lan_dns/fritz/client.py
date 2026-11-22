from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

import requests
import urllib3
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from ..util import parse_int

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class FritzBoxError(RuntimeError):
    pass


@dataclass
class HostFetch:
    entries: list[dict[str, Any]]
    raw_xml: str
    errors: list[str]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_new_values(xml_text: str) -> dict[str, str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FritzBoxError(f"Ungültige SOAP-Antwort: {exc}") from exc
    values: dict[str, str] = {}
    for element in root.iter():
        name = _local_name(element.tag)
        if name.startswith("New"):
            values[name] = (element.text or "").strip()
    if not values and "Fault" in xml_text:
        raise FritzBoxError(f"SOAP-Fehler: {xml_text[:500]}")
    return values


def _clean_mesh_json(text: str) -> str:
    text = text.replace("\r", "").replace("\\/", "/")
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r':\s*"\s*"""\s*,', ': "",', text)
    text = re.sub(r':\s*"""\s*,', ': "",', text)
    return text


def load_mesh_json(text: str) -> dict:
    cleaned = _clean_mesh_json(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_error:
        fallback = cleaned.replace("\\", "")
        try:
            return json.loads(fallback)
        except json.JSONDecodeError as exc:
            raise FritzBoxError(f"Mesh-JSON konnte nicht gelesen werden: {exc}") from first_error


class FritzClient:
    def __init__(
        self,
        ip: str,
        user: str,
        password: str,
        *,
        timeout: int = 25,
        verify_tls: bool = False,
    ) -> None:
        self.ip = ip
        self.user = user
        self.password = password
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "fritz-lan-dns/0.1"})

    def _auths(self):
        return (HTTPDigestAuth(self.user, self.password), HTTPBasicAuth(self.user, self.password))

    def _post(self, url: str, *, data: bytes, headers: dict[str, str]) -> requests.Response:
        last: requests.Response | None = None
        for auth in self._auths():
            response = self.session.post(
                url,
                data=data,
                headers=headers,
                auth=auth,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
            last = response
            if response.status_code != 401:
                break
        assert last is not None
        return last

    def _get(self, url: str) -> requests.Response:
        last: requests.Response | None = None
        for auth in self._auths():
            response = self.session.get(
                url,
                auth=auth,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
            last = response
            if response.status_code != 401:
                break
        assert last is not None
        return last

    def soap(
        self,
        control: str,
        service: str,
        action: str,
        arguments: dict[str, str] | None = None,
        *,
        scheme: str = "http",
        port: int = 49000,
    ) -> dict[str, str]:
        inner = "".join(
            f"<{key}>{escape(str(value))}</{key}>"
            for key, value in (arguments or {}).items()
        )
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f"<s:Body><u:{action} xmlns:u=\"{service}\">{inner}</u:{action}>"
            "</s:Body></s:Envelope>"
        )
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{service}#{action}"',
        }
        url = f"{scheme}://{self.ip}:{port}/upnp/control/{control}"
        response = self._post(url, data=body.encode("utf-8"), headers=headers)
        if response.status_code >= 400:
            raise FritzBoxError(
                f"TR-064 {action} fehlgeschlagen: HTTP {response.status_code}"
            )
        return _parse_new_values(response.text)

    def get_security_port(self) -> int:
        values = self.soap(
            "deviceinfo",
            "urn:dslforum-org:service:DeviceInfo:1",
            "GetSecurityPort",
        )
        raw = values.get("NewSecurityPort")
        if not raw:
            raise FritzBoxError("GetSecurityPort lieferte keinen Port")
        return int(raw)

    def get_mesh_list_path(self) -> str:
        values = self.soap(
            "hosts",
            "urn:dslforum-org:service:Hosts:1",
            "X_AVM-DE_GetMeshListPath",
        )
        path = values.get("NewX_AVM-DE_MeshListPath")
        if not path:
            raise FritzBoxError("X_AVM-DE_GetMeshListPath lieferte keinen Pfad")
        return path

    def fetch_mesh_text(self, path: str, security_port: int) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            suffix = path if path.startswith("/") else f"/{path}"
            url = f"https://{self.ip}:{security_port}{suffix}"
        response = self._get(url)
        if response.status_code >= 400:
            raise FritzBoxError(f"Mesh-Liste konnte nicht geladen werden: HTTP {response.status_code}")
        return response.text

    def get_device_info(self) -> dict[str, str]:
        return self.soap(
            "deviceinfo",
            "urn:dslforum-org:service:DeviceInfo:1",
            "GetInfo",
        )

    def get_wlan_info(self, index: int) -> dict[str, str]:
        service = f"urn:dslforum-org:service:WLANConfiguration:{index}"
        return self.soap(f"wlanconfig{index}", service, "GetInfo")

    def get_wlan_associations(self, index: int) -> list[dict[str, str]]:
        service = f"urn:dslforum-org:service:WLANConfiguration:{index}"
        values = self.soap(f"wlanconfig{index}", service, "GetTotalAssociations")
        total = parse_int(values.get("NewTotalAssociations")) or 0
        entries: list[dict[str, str]] = []
        for position in range(total):
            try:
                entry = self.soap(
                    f"wlanconfig{index}",
                    service,
                    "GetGenericAssociatedDeviceInfo",
                    {"NewAssociatedDeviceIndex": str(position)},
                )
            except FritzBoxError:
                break
            entries.append(entry)
        return entries

    def get_hosts(self) -> HostFetch:
        values = self.soap(
            "hosts",
            "urn:dslforum-org:service:Hosts:1",
            "GetHostNumberOfEntries",
        )
        raw_count = values.get("NewHostNumberOfEntries")
        if raw_count is None:
            raise FritzBoxError("GetHostNumberOfEntries lieferte keine Anzahl")
        count = int(raw_count)
        entries: list[dict[str, Any]] = []
        raw_parts: list[str] = []
        errors: list[str] = []
        for index in range(count):
            try:
                entry = self.soap(
                    "hosts",
                    "urn:dslforum-org:service:Hosts:1",
                    "GetGenericHostEntry",
                    {"NewIndex": str(index)},
                )
                entries.append(entry)
                raw_parts.append(json.dumps(entry, ensure_ascii=False))
            except FritzBoxError as exc:
                errors.append(f"Host {index}: {exc}")
        return HostFetch(entries=entries, raw_xml="\n".join(raw_parts), errors=errors)
