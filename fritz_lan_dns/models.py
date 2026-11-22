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
    source: str = "mesh"


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
class WlanInterface:
    node_uid: str
    device_name: str
    device_model: str
    device_ip: str | None
    online: bool
    source: str
    description: str
    interface_name: str
    ssid: str
    band: str
    channel: int | None
    channel_width: str
    channel_utilization: int | None
    security: str
    opmode: str
    phymodes: str
    streams_tx: int | None
    streams_rx: int | None
    steering_enabled: bool
    is_guest: bool
    is_uplink: bool
    client_count: int = 0


@dataclass
class WlanClient:
    ap_node_uid: str
    ap_interface_name: str
    ap_device_name: str
    ap_ssid: str
    ap_band: str
    client_node_uid: str
    client_name: str
    client_ip: str | None
    client_mac: str | None
    online: bool
    signal: int | None
    signal_unit: str
    rx_kbps: int | None
    tx_kbps: int | None
    last_connected: str | None
    source: str
    description: str = ""


@dataclass
class WlanSnapshot:
    interfaces: list[WlanInterface]
    clients: list[WlanClient]


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
    source: str = ""

    @property
    def writable(self) -> bool:
        return self.status in ("active", "stale", "static")
