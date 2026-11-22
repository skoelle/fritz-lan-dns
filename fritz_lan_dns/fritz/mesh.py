from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from ..models import MeshLink, MeshNode, MeshSnapshot
from ..util import parse_int


class MeshParseError(ValueError):
    pass


def _ipv4(node: dict[str, Any]) -> str | None:
    addresses = node.get("ip_addresses") or []
    for address in addresses:
        if not isinstance(address, dict):
            continue
        if str(address.get("version", "")).upper() == "V4":
            value = str(address.get("value", "")).split("/")[0].strip()
            if value:
                return value
    return None


def _find_mac(node: dict[str, Any]) -> str | None:
    for key in ("mac_address", "mac", "MACAddress"):
        value = node.get(key)
        if value:
            return str(value).lower()
    hardware = node.get("hardware")
    if isinstance(hardware, dict):
        for key in ("mac_address", "mac", "MACAddress"):
            value = hardware.get(key)
            if value:
                return str(value).lower()
    for interface in node.get("node_interfaces") or []:
        if isinstance(interface, dict):
            for key in ("mac_address", "mac"):
                value = interface.get(key)
                if value:
                    return str(value).lower()
    return None


def _connection_type(interface: dict[str, Any]) -> str:
    iface_type = str(interface.get("type", "")).upper()
    name = str(interface.get("name", ""))
    if iface_type == "WLAN":
        if "2" in name:
            return "WLAN 2G"
        if "5" in name:
            return "WLAN 5G"
        if "6" in name:
            return "WLAN 6G"
        return "WLAN"
    return iface_type or "UNKNOWN"


def _rate(link: dict[str, Any], direction: str) -> int | None:
    for prefix in ("cur_data_rate", "max_data_rate"):
        value = parse_int(link.get(f"{prefix}_{direction}"))
        if value is not None:
            return value
    return None


def parse_mesh(raw: dict[str, Any], descriptions: dict[str, str] | None = None) -> MeshSnapshot:
    descriptions = descriptions or {}
    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, list):
        raise MeshParseError("Mesh-JSON enthält keine 'nodes'-Liste")

    nodes_by_id: dict[str, MeshNode] = {}
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("uid") or "").strip()
        if not uid:
            continue
        name = str(item.get("device_name") or "").strip()
        nodes_by_id[uid] = MeshNode(
            uid=uid,
            device_name=name,
            device_model=str(item.get("device_model") or ""),
            mesh_role=str(item.get("mesh_role") or ""),
            ip=_ipv4(item),
            mac=_find_mac(item),
            description=descriptions.get(name.lower(), ""),
        )

    links: list[MeshLink] = []
    adjacency: dict[str, list[tuple[str, MeshLink, dict[str, Any]]]] = defaultdict(list)
    seen: set[tuple] = set()
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        source_uid = str(item.get("uid") or "").strip()
        if not source_uid:
            continue
        for interface in item.get("node_interfaces") or []:
            if not isinstance(interface, dict):
                continue
            iface_type = str(interface.get("type") or "")
            iface_name = str(interface.get("name") or "")
            for link in interface.get("node_links") or []:
                if not isinstance(link, dict):
                    continue
                a_uid = str(link.get("node_1_uid") or "").strip()
                b_uid = str(link.get("node_2_uid") or "").strip()
                if not a_uid or not b_uid:
                    continue
                state = str(link.get("state") or "")
                key = (
                    tuple(sorted((a_uid, b_uid))),
                    iface_type,
                    iface_name,
                    state,
                )
                if key in seen:
                    continue
                seen.add(key)
                mesh_link = MeshLink(
                    node_a_uid=a_uid,
                    node_b_uid=b_uid,
                    interface_type=iface_type,
                    interface_name=iface_name,
                    state=state,
                    rx_kbps=_rate(link, "rx"),
                    tx_kbps=_rate(link, "tx"),
                )
                links.append(mesh_link)
                if state == "CONNECTED":
                    adjacency[a_uid].append((b_uid, mesh_link, interface))
                    adjacency[b_uid].append((a_uid, mesh_link, interface))

    master_uid = next(
        (uid for uid, node in nodes_by_id.items() if node.mesh_role.lower() == "master"),
        None,
    )
    if master_uid is not None and master_uid in nodes_by_id:
        nodes_by_id[master_uid].online = True
        visited = {master_uid}
        queue: deque[str] = deque([master_uid])
        while queue:
            current = queue.popleft()
            neighbours = sorted(
                adjacency.get(current, []),
                key=lambda item: (
                    nodes_by_id.get(item[0]).device_name.lower()
                    if item[0] in nodes_by_id
                    else item[0]
                ),
            )
            for target_uid, link, interface in neighbours:
                if target_uid in visited or target_uid not in nodes_by_id:
                    continue
                visited.add(target_uid)
                node = nodes_by_id[target_uid]
                node.online = True
                node.parent_uid = current
                node.connection_type = _connection_type(interface)
                if node.connection_type == "PLC":
                    node.plc_rx_kbps = link.rx_kbps
                    node.plc_tx_kbps = link.tx_kbps
                queue.append(target_uid)

    nodes = sorted(nodes_by_id.values(), key=lambda node: node.device_name.lower())
    return MeshSnapshot(nodes=nodes, links=links, master_uid=master_uid, raw=raw)
