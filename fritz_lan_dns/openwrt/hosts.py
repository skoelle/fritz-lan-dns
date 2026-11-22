from __future__ import annotations

import ipaddress
from typing import Any

from ..models import HostRecord


def _candidate(value: Any, version: int) -> str | None:
    if not value:
        return None
    text = str(value).split("/")[0].strip()
    if not text:
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if address.version != version:
        return None
    if address.is_loopback or address.is_link_local or address.is_multicast:
        return None
    if address.is_unspecified:
        return None
    return str(address)


def _first_ip(values: Any, version: int) -> str | None:
    for value in values or []:
        address = _candidate(value, version)
        if address:
            return address
    return None


def _clean_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or name == "*":
        return ""
    return name


def _lease_active(expires: Any) -> bool:
    if expires is None or expires is False:
        return True
    try:
        return int(expires) > 0
    except (TypeError, ValueError):
        return True


def parse_openwrt_hosts(
    leases: dict[str, Any] | None,
    hints: dict[str, Any] | None,
    *,
    include_ipv6: bool = False,
) -> list[HostRecord]:
    leases = leases or {}
    hints = hints or {}
    records: dict[str, HostRecord] = {}

    def add(
        key: str,
        hostname: str,
        ip: str | None,
        mac: str | None,
        *,
        active: bool,
    ) -> None:
        existing = records.get(key)
        if existing is None:
            records[key] = HostRecord(
                key=key,
                hostname=hostname,
                ip=ip,
                mac=mac,
                active=active,
                source="openwrt",
            )
            return
        if not existing.hostname and hostname:
            existing.hostname = hostname
        if not existing.ip and ip:
            existing.ip = ip
        if not existing.mac and mac:
            existing.mac = mac
        existing.active = existing.active or active

    for lease in leases.get("dhcp_leases") or []:
        if not isinstance(lease, dict) or not _lease_active(lease.get("expires")):
            continue
        mac = str(lease.get("macaddr") or "").strip().lower() or None
        ip = _candidate(lease.get("ipaddr"), 4)
        hostname = _clean_name(lease.get("hostname"))
        if not ip or not hostname:
            continue
        key = f"mac:{mac}" if mac else f"ip:{ip}"
        add(key, hostname, ip, mac, active=True)

    if include_ipv6:
        for lease in leases.get("dhcp6_leases") or []:
            if not isinstance(lease, dict) or not _lease_active(lease.get("expires")):
                continue
            mac = str(lease.get("macaddr") or "").strip().lower() or None
            hostname = _clean_name(lease.get("hostname"))
            ip = _candidate(lease.get("ip6addr"), 6) or _first_ip(
                lease.get("ip6addrs"), 6
            )
            if not ip or not hostname:
                continue
            key = f"mac:{mac}" if mac else f"ipv6:{ip}"
            add(key, hostname, ip, mac, active=True)

    for raw_mac, hint in hints.items():
        if not isinstance(hint, dict):
            continue
        hostname = _clean_name(hint.get("name"))
        if not hostname:
            continue
        mac = str(raw_mac or "").strip().lower() or None
        ip = _first_ip(hint.get("ipaddrs"), 4)
        if not ip and include_ipv6:
            ip = _first_ip(hint.get("ip6addrs"), 6)
        if not ip:
            continue
        key = f"mac:{mac}" if mac else f"ip:{ip}"
        add(key, hostname, ip, mac, active=False)

    result = list(records.values())
    result.sort(key=lambda record: (record.hostname.lower(), record.key))
    return result
