from __future__ import annotations

from typing import Any

from ..models import HostRecord
from ..util import device_description, parse_bool


def _host_key(entry: dict[str, Any], index: int) -> str:
    mac = str(entry.get("NewMACAddress") or "").strip().lower()
    if mac:
        return f"mac:{mac}"
    ip = str(entry.get("NewIPAddress") or "").strip()
    if ip:
        return f"ip:{ip}"
    name = str(entry.get("NewHostName") or "").strip().lower()
    if name:
        return f"name:{name}"
    return f"index:{index}"


def parse_host_entries(
    entries: list[dict[str, Any]],
    descriptions: dict[str, str] | None = None,
    static_dhcp_by_mac: dict[str, bool] | None = None,
) -> list[HostRecord]:
    descriptions = descriptions or {}
    static_dhcp_by_mac = static_dhcp_by_mac or {}
    hosts: list[HostRecord] = []
    for index, entry in enumerate(entries):
        hostname = str(entry.get("NewHostName") or "").strip()
        mac = str(entry.get("NewMACAddress") or "").strip().lower() or None
        ip = str(entry.get("NewIPAddress") or "").strip() or None
        address_source = str(entry.get("NewAddressSource") or "").strip().lower()
        static_dhcp = None
        if address_source == "dhcp" and mac in static_dhcp_by_mac:
            static_dhcp = static_dhcp_by_mac[mac]
        description = device_description(hostname, descriptions)
        hosts.append(
            HostRecord(
                key=_host_key(entry, index),
                hostname=hostname,
                ip=ip,
                mac=mac,
                active=parse_bool(entry.get("NewActive")),
                interface_type=str(entry.get("NewInterfaceType") or ""),
                last_seen=str(entry.get("NewX_AVM-DE_LastSeen") or "").strip() or None,
                source="fritz",
                description=description,
                address_source=address_source,
                static_dhcp=static_dhcp,
            )
        )
    return hosts
