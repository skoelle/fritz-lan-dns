from __future__ import annotations

from typing import Any

from ..models import MeshNode, WlanClient, WlanInterface, WlanSnapshot
from ..util import parse_bool, parse_int

_BAND_ORDER = {"2.4 GHz": 0, "5 GHz": 1, "6 GHz": 2, "": 9}
_UPLINK_OPMODES = {"REPEATER", "STATION"}
_INFRASTRUCTURE_ROLES = {"master", "slave", "repeater"}


def band_from_frequency(value: object) -> str:
    frequency = parse_int(value)
    if frequency is None:
        return ""
    if frequency > 100_000:
        frequency //= 1000
    if 2400 <= frequency < 2500:
        return "2.4 GHz"
    if 4900 <= frequency < 5925:
        return "5 GHz"
    if 5925 <= frequency <= 7125:
        return "6 GHz"
    return ""


def band_from_interface_name(name: str | None) -> str:
    value = (name or "").upper()
    if "6G" in value:
        return "6 GHz"
    if "5G" in value:
        return "5 GHz"
    if "2G" in value:
        return "2.4 GHz"
    return ""


def band_from_channel(channel: int | None) -> str:
    if channel is None:
        return ""
    if 1 <= channel <= 14:
        return "2.4 GHz"
    if 32 <= channel <= 196:
        return "5 GHz"
    return ""


def format_channel_width(value: object) -> str:
    if value in (None, "", 0, "0"):
        return ""
    text = str(value).strip()
    if text.endswith("MHz"):
        return text
    if text.isdigit():
        return f"{text} MHz"
    return text


def _streams(value: object) -> int | None:
    if not isinstance(value, list):
        return None
    counts = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        count = parse_int(item[1])
        if count is not None:
            counts.append(count)
    return max(counts) if counts else None


def _phymodes(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return ", ".join(str(item) for item in value if item)


def _rate(link: dict[str, Any], direction: str) -> int | None:
    for prefix in ("cur_data_rate", "max_data_rate"):
        value = parse_int(link.get(f"{prefix}_{direction}"))
        if value is not None:
            return value
    return None


def _signal_dbm(link: dict[str, Any]) -> int | None:
    values: list[int] = []
    for key in ("rx_rcpi", "tx_rcpi"):
        value = parse_int(link.get(key))
        if value is None or value in (0, 255):
            continue
        if -120 <= value <= 0:
            values.append(value)
        elif 1 <= value <= 254:
            values.append(round(value / 2 - 110))
    return max(values) if values else None


def _valid_ip(value: object) -> str | None:
    text = str(value or "").strip()
    if not text or text in {"0.0.0.0", "::"}:
        return None
    return text


def _ap_interface(interface: dict[str, Any]) -> bool:
    if _is_uplink(interface):
        return False
    opmode = str(interface.get("opmode") or "").upper()
    name = str(interface.get("name") or "").upper()
    if opmode.startswith("AP") or name.startswith("AP"):
        return True
    return bool(str(interface.get("ssid") or "").strip()) or not opmode


def _is_uplink(interface: dict[str, Any]) -> bool:
    opmode = str(interface.get("opmode") or "").upper()
    name = str(interface.get("name") or "").upper()
    return opmode in _UPLINK_OPMODES or name.startswith("UPLINK")


def _is_guest(interface: dict[str, Any]) -> bool:
    opmode = str(interface.get("opmode") or "").upper()
    name = str(interface.get("name") or "").upper()
    return "GUEST" in opmode or "GUEST" in name


def _interface_record(
    node: MeshNode, interface: dict[str, Any]
) -> WlanInterface:
    name = str(interface.get("name") or "").strip()
    channel = parse_int(interface.get("current_channel"))
    info = interface.get("current_channel_info")
    info = info if isinstance(info, dict) else {}
    band = (
        band_from_frequency(info.get("primary_freq"))
        or band_from_interface_name(name)
        or band_from_channel(channel)
    )
    return WlanInterface(
        node_uid=node.uid,
        device_name=node.device_name,
        device_model=node.device_model,
        device_ip=node.ip,
        online=node.online,
        source=node.source,
        description=node.description,
        interface_name=name,
        ssid=str(interface.get("ssid") or "").strip(),
        band=band,
        channel=channel,
        channel_width=format_channel_width(info.get("channel_width")),
        channel_utilization=parse_int(interface.get("channel_utilization")),
        security=str(interface.get("security") or ""),
        opmode=str(interface.get("opmode") or ""),
        phymodes=_phymodes(interface.get("phymodes")),
        streams_tx=_streams(interface.get("supported_streams_tx")),
        streams_rx=_streams(interface.get("supported_streams_rx")),
        steering_enabled=parse_bool(interface.get("steering_enabled")),
        is_guest=_is_guest(interface),
        is_uplink=_is_uplink(interface),
    )


def parse_wlan(raw: dict[str, Any], nodes: dict[str, MeshNode]) -> WlanSnapshot:
    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, list):
        return WlanSnapshot(interfaces=[], clients=[])

    interfaces: list[WlanInterface] = []
    interfaces_by_uid: dict[str, tuple[MeshNode, dict[str, Any], WlanInterface]] = {}
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("uid") or "").strip()
        node = nodes.get(uid)
        if node is None:
            continue
        for index, interface in enumerate(item.get("node_interfaces") or []):
            if not isinstance(interface, dict):
                continue
            if str(interface.get("type") or "").upper() != "WLAN":
                continue
            record = _interface_record(node, interface)
            interfaces.append(record)
            iface_uid = str(interface.get("uid") or "").strip()
            if not iface_uid:
                iface_uid = f"{uid}:{record.interface_name}:{index}"
            interfaces_by_uid[iface_uid] = (node, interface, record)

    nodes_with_ap = {item.node_uid for item in interfaces if not item.is_uplink}
    interfaces = [
        item for item in interfaces if not item.is_uplink or item.node_uid in nodes_with_ap
    ]

    clients: list[WlanClient] = []
    seen: set[tuple[str, str, str]] = set()
    for ap_node, interface, record in interfaces_by_uid.values():
        if not _ap_interface(interface):
            continue
        for link in interface.get("node_links") or []:
            if not isinstance(link, dict):
                continue
            if str(link.get("state") or "").upper() != "CONNECTED":
                continue
            node_1 = str(link.get("node_1_uid") or "").strip()
            node_2 = str(link.get("node_2_uid") or "").strip()
            other_uid = node_2 if node_1 == ap_node.uid else node_1
            if not other_uid or other_uid == ap_node.uid:
                continue
            other = nodes.get(other_uid)
            if other is None:
                continue
            if other.mesh_role.lower() in _INFRASTRUCTURE_ROLES:
                continue
            link_uid = str(link.get("uid") or "").strip()
            dedupe = (
                record.node_uid,
                record.interface_name,
                link_uid or other_uid,
            )
            if dedupe in seen:
                continue
            seen.add(dedupe)
            clients.append(
                WlanClient(
                    ap_node_uid=record.node_uid,
                    ap_interface_name=record.interface_name,
                    ap_device_name=record.device_name,
                    ap_ssid=record.ssid,
                    ap_band=record.band,
                    client_node_uid=other_uid,
                    client_name=other.device_name or other.ip or other.mac or other_uid,
                    client_ip=_valid_ip(other.ip),
                    client_mac=other.mac,
                    online=other.online,
                    signal=_signal_dbm(link),
                    signal_unit="dBm",
                    rx_kbps=_rate(link, "rx"),
                    tx_kbps=_rate(link, "tx"),
                    last_connected=str(link.get("last_connected") or "").strip()
                    or None,
                    source=other.source,
                    description=other.description,
                )
            )
            record.client_count += 1

    clients.sort(
        key=lambda client: (
            client.ap_device_name.lower(),
            client.ap_interface_name.lower(),
            client.client_name.lower(),
            client.client_mac or "",
        )
    )
    interfaces.sort(
        key=lambda interface: (
            interface.device_name.lower(),
            interface.is_uplink,
            _BAND_ORDER.get(interface.band, 9),
            interface.interface_name.lower(),
        )
    )
    return WlanSnapshot(interfaces=interfaces, clients=clients)
