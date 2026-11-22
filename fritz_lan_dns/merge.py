from __future__ import annotations

from .models import HostRecord, MeshNode

_SOURCE_RANK = {"fritz": 3, "openwrt": 2, "mesh": 1}


def _merge_into(target: HostRecord, source: HostRecord) -> None:
    if not target.hostname and source.hostname:
        target.hostname = source.hostname
    if not target.ip and source.ip:
        target.ip = source.ip
    if not target.mac and source.mac:
        target.mac = source.mac
    if not target.interface_type and source.interface_type:
        target.interface_type = source.interface_type
    if not target.description and source.description:
        target.description = source.description
    if not target.last_seen and source.last_seen:
        target.last_seen = source.last_seen
    target.active = target.active or source.active
    if _SOURCE_RANK.get(source.source, 0) > _SOURCE_RANK.get(target.source, 0):
        target.source = source.source


def merge_hosts(
    host_entries: list[HostRecord],
    mesh_nodes: list[MeshNode],
    descriptions: dict[str, str] | None = None,
    extra_hosts: list[HostRecord] | None = None,
) -> list[HostRecord]:
    descriptions = descriptions or {}
    records: list[HostRecord] = []
    by_mac: dict[str, HostRecord] = {}
    by_ip: dict[str, HostRecord] = {}
    by_name: dict[str, HostRecord] = {}
    by_key: dict[str, HostRecord] = {}

    def add(record: HostRecord) -> None:
        existing: HostRecord | None = None
        if record.mac:
            existing = by_mac.get(record.mac.lower())
        if existing is None and record.ip:
            existing = by_ip.get(record.ip)
        if existing is None and record.hostname:
            existing = by_name.get(record.hostname.lower())
        if existing is not None:
            _merge_into(existing, record)
            if existing.mac:
                by_mac[existing.mac.lower()] = existing
            if existing.ip:
                by_ip[existing.ip] = existing
            if existing.hostname:
                by_name[existing.hostname.lower()] = existing
            return

        records.append(record)
        by_key[record.key] = record
        if record.mac:
            by_mac[record.mac.lower()] = record
        if record.ip:
            by_ip[record.ip] = record
        if record.hostname:
            by_name[record.hostname.lower()] = record

    def add_extra(record: HostRecord) -> None:
        mac = (record.mac or "").lower()
        existing = by_mac.get(mac) if mac else None
        if existing is not None and record.ip and existing.ip == record.ip:
            _merge_into(existing, record)
            return
        if existing is None and record.ip:
            ip_existing = by_ip.get(record.ip)
            if (
                ip_existing is not None
                and ip_existing.hostname
                and ip_existing.hostname.lower() == record.hostname.lower()
            ):
                _merge_into(ip_existing, record)
                return

        base_key = record.key
        key = base_key
        counter = 2
        while key in by_key:
            key = f"openwrt:{base_key}" if counter == 2 else f"openwrt:{base_key}:{counter}"
            counter += 1
        record.key = key
        records.append(record)
        by_key[key] = record

    for record in host_entries:
        add(record)

    for node in mesh_nodes:
        hostname = node.device_name.strip()
        key = f"mac:{node.mac.lower()}" if node.mac else f"mesh:{node.uid}"
        add(
            HostRecord(
                key=key,
                hostname=hostname,
                ip=node.ip,
                mac=node.mac,
                active=node.online,
                interface_type=node.connection_type or "",
                last_seen=None,
                source="mesh",
                description=node.description or descriptions.get(hostname.lower(), ""),
            )
        )

    for record in extra_hosts or []:
        add_extra(record)

    records.sort(key=lambda record: (record.hostname.lower(), record.key))
    return records
