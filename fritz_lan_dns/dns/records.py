from __future__ import annotations

import ipaddress
from collections import defaultdict

from ..models import DnsRecord, HostRecord
from ..util import sanitize_hostname

_SOURCE_PRIORITY = {"static": 3, "fritz": 2, "openwrt": 2, "mesh": 1, "": 0}


def _candidate_ip(value: str | None, include_ipv6: bool) -> str | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if include_ipv6 and address.version == 6:
        if address.is_link_local or address.is_loopback or address.is_multicast:
            return None
        return str(address)
    if address.version != 4:
        return None
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        return None
    return str(address)


def _winner(candidates: list[dict]) -> dict:
    return sorted(
        candidates,
        key=lambda item: (
            _SOURCE_PRIORITY.get(item["source"], 0),
            1 if item["active"] else 0,
            item["last_seen"] or "",
            item["ip"],
        ),
        reverse=True,
    )[0]


def build_dns_records(
    hosts: list[HostRecord],
    static: list[dict] | None,
    domain: str,
    include_ipv6: bool = False,
) -> list[DnsRecord]:
    candidates: dict[tuple, dict] = {}

    def add(raw_hostname: str | None, ip: str | None, source: str, host: HostRecord | None = None) -> None:
        hostname = sanitize_hostname(raw_hostname, domain)
        address = _candidate_ip(ip, include_ipv6)
        if not hostname or not address:
            return
        key = (hostname, address)
        active = bool(host.active) if host else True
        last_seen = host.last_seen if host else None
        version = ipaddress.ip_address(address).version
        existing = candidates.get(key)
        if existing is None:
            candidates[key] = {
                "hostname": hostname,
                "ip": address,
                "version": version,
                "mac": host.mac if host else None,
                "active": active,
                "last_seen": last_seen,
                "source": source,
            }
            return
        existing["active"] = existing["active"] or active
        existing["last_seen"] = max(existing["last_seen"] or "", last_seen or "") or None
        if not existing["mac"] and host and host.mac:
            existing["mac"] = host.mac
        if _SOURCE_PRIORITY.get(source, 0) > _SOURCE_PRIORITY.get(existing["source"], 0):
            existing["source"] = source

    for entry in static or []:
        add(entry.get("hostname"), entry.get("ip"), "static")
    for host in hosts:
        add(host.hostname, host.ip, host.source or "fritz", host)

    by_host_family: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for candidate in candidates.values():
        by_host_family[(candidate["hostname"], candidate["version"])].append(candidate)

    chosen: list[dict] = []
    for (hostname, version), group in by_host_family.items():
        winner = _winner(group)
        if len(group) > 1:
            addresses = ", ".join(sorted(item["ip"] for item in group))
            family = "IPv4" if version == 4 else "IPv6"
            winner["conflict"] = f"Mehrere {family}-Adressen für {hostname}: {addresses}"
        chosen.append(winner)

    by_ip: dict[str, list[dict]] = defaultdict(list)
    for candidate in chosen:
        by_ip[candidate["ip"]].append(candidate)

    records: list[DnsRecord] = []
    for ip, group in by_ip.items():
        winner = _winner(group)
        for candidate in group:
            if candidate is winner:
                records.append(_to_record(candidate, domain))
                continue
            conflict = f"IP {ip} wird auch von {winner['hostname']} verwendet"
            records.append(
                DnsRecord(
                    hostname=candidate["hostname"],
                    fqdn=f"{candidate['hostname']}.{domain}",
                    ip=candidate["ip"],
                    mac=candidate["mac"],
                    status="conflict",
                    conflict=conflict,
                    last_seen=candidate["last_seen"],
                    source=candidate["source"],
                )
            )

    records.sort(key=lambda record: (record.status == "conflict", record.fqdn, record.ip))
    return records


def _to_record(candidate: dict, domain: str) -> DnsRecord:
    if candidate["source"] == "static":
        status = "static"
    elif candidate["active"]:
        status = "active"
    else:
        status = "stale"
    return DnsRecord(
        hostname=candidate["hostname"],
        fqdn=f"{candidate['hostname']}.{domain}",
        ip=candidate["ip"],
        mac=candidate["mac"],
        status=status,
        conflict=candidate.get("conflict"),
        last_seen=candidate["last_seen"],
        source=candidate["source"],
    )
