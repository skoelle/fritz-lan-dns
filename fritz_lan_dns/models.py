from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MeshNode:
    uid: str
    device_name: str
    device_model: str
    mesh_role: str
    ip: str | None
    mac: str | None
    online: bool = False
    parent_uid: str | None = None
    connection_type: str | None = None
    plc_rx_kbps: int | None = None
    plc_tx_kbps: int | None = None
    description: str = ""


@dataclass
class MeshLink:
    node_a_uid: str
    node_b_uid: str
    interface_type: str
    interface_name: str
    state: str
    rx_kbps: int | None = None
    tx_kbps: int | None = None


@dataclass
class MeshSnapshot:
    nodes: list[MeshNode]
    links: list[MeshLink]
    master_uid: str | None
    raw: dict


@dataclass
class HostRecord:
    key: str
    hostname: str
    ip: str | None
    mac: str | None
    active: bool
    interface_type: str = ""
    last_seen: str | None = None
    source: str = "fritz"
    description: str = ""


@dataclass
class DnsRecord:
    hostname: str
    fqdn: str
    ip: str
    mac: str | None
    status: str
    conflict: str | None = None
    last_seen: str | None = None

    @property
    def writable(self) -> bool:
        return self.status in ("active", "stale", "static")
