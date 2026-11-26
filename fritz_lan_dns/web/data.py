from __future__ import annotations

import ipaddress
import sqlite3
from collections import defaultdict
from datetime import datetime

from .. import db
from ..config import Settings
from ..fritz.events import EVENT_LABELS, SEVERITY_LABELS
from ..models import HostRecord, MeshLink, MeshNode
from ..util import (
    format_duration,
    http_url,
    ip_mode_meta,
    source_filter_values,
    source_label,
)

_CONNECTION_META = {
    "LAN": ("🔌", "LAN", "lan"),
    "PLC": ("⚡", "PLC", "plc"),
    "WLAN": ("📶", "WLAN", "wlan"),
    "WLAN 2G": ("📶", "WLAN 2G", "wlan"),
    "WLAN 5G": ("📶", "WLAN 5G", "wlan"),
    "WLAN 6G": ("📶", "WLAN 6G", "wlan"),
}


def display_role(role: str | None) -> str:
    value = (role or "").strip()
    if value.lower() in {"", "unknown", "client", "none"}:
        return ""
    if value.lower() in {"master", "repeater"}:
        return value.capitalize()
    return value[:1].upper() + value[1:]


def connection_meta(connection_type: str | None) -> tuple[str, str, str]:
    value = (connection_type or "").strip().upper()
    return _CONNECTION_META.get(value, ("", "", ""))


def format_kbps(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value / 1000:.1f}"


def speed_display(rx_kbps: int | None, tx_kbps: int | None) -> dict[str, str]:
    if rx_kbps is None and tx_kbps is None:
        return {"label": "", "icon": "", "css": ""}
    label = f"{format_kbps(rx_kbps)} / {format_kbps(tx_kbps)} Mbit/s"
    values = [value for value in (rx_kbps, tx_kbps) if value is not None]
    if not values or max(values) <= 0:
        return {"label": label, "icon": "❌", "css": "offline"}
    if min(values) < 50000:
        return {"label": label, "icon": "🟡", "css": "slow"}
    return {"label": label, "icon": "🟢", "css": "ok"}


_MWAN3_STATUS_CLASS = {
    "online": "ok",
    "offline": "failed",
    "unknown": "stale",
    "notracking": "stale",
}


def _wifi_band(frequency: object) -> str:
    try:
        value = int(float(str(frequency)))
    except (TypeError, ValueError):
        return ""
    if 2400 <= value < 3000:
        return "2.4 GHz"
    if 4900 <= value < 5925:
        return "5 GHz"
    if value >= 5925:
        return "6 GHz"
    return ""


def _wifi_signal_class(signal: object) -> str:
    try:
        value = int(float(str(signal)))
    except (TypeError, ValueError):
        return "stale"
    if value >= -60:
        return "ok"
    if value >= -72:
        return "stale"
    return "failed"


def _wifi_view(info: dict | None) -> dict | None:
    if not isinstance(info, dict):
        return None
    signal = info.get("signal")
    noise = info.get("noise")
    quality = info.get("quality")
    quality_max = info.get("quality_max")
    bitrate = info.get("bitrate")
    try:
        snr = int(signal) - int(noise)
    except (TypeError, ValueError):
        snr = None
    try:
        quality_pct = round(int(quality) * 100 / int(quality_max))
    except (TypeError, ValueError, ZeroDivisionError):
        quality_pct = None
    try:
        rate_mbit = round(int(bitrate) / 1000)
    except (TypeError, ValueError):
        rate_mbit = None
    return {
        "device": info.get("device"),
        "mode": info.get("mode"),
        "ssid": info.get("ssid"),
        "signal": signal,
        "noise": noise,
        "snr": snr,
        "quality_pct": quality_pct,
        "signal_class": _wifi_signal_class(signal),
        "rate_mbit": rate_mbit,
        "band": _wifi_band(info.get("frequency")),
        "channel": info.get("channel"),
    }


def mwan3_view(entry: dict | None) -> dict | None:
    if not entry:
        return None
    data = entry.get("data") or {}
    wireless = data.get("wireless") or {}
    interfaces = []
    for name, item in (data.get("interfaces") or {}).items():
        if not isinstance(item, dict):
            continue
        tracks = [track for track in item.get("track_ip") or [] if isinstance(track, dict)]
        up = sum(
            1 for track in tracks if str(track.get("status", "")).lower() == "up"
        )
        skipped = sum(
            1 for track in tracks if str(track.get("status", "")).lower() == "skipped"
        )
        status = str(item.get("status") or "unknown").lower()
        tracking = item.get("tracking") or (
            "active" if item.get("running") else "inactive"
        )
        interfaces.append(
            {
                "name": name,
                "status": status,
                "status_class": _MWAN3_STATUS_CLASS.get(status, "stale"),
                "tracking": tracking,
                "online": format_duration(item.get("online")),
                "uptime": format_duration(item.get("uptime")),
                "score": item.get("score"),
                "lost": item.get("lost"),
                "track_up": up,
                "track_skipped": skipped,
                "track_total": len(tracks),
                "wifi": _wifi_view(wireless.get(name)),
            }
        )
    interfaces.sort(key=lambda row: row["name"].lower())

    policies = []
    for family, values in (data.get("policies") or {}).items():
        if not isinstance(values, dict):
            continue
        for name, members in values.items():
            clean_members = [
                {"interface": member.get("interface"), "percent": member.get("percent")}
                for member in members or []
                if isinstance(member, dict) and member.get("interface")
            ]
            policies.append(
                {"family": family, "name": name, "members": clean_members}
            )

    connected = data.get("connected") or {}
    return {
        "started_at": entry.get("started_at"),
        "interfaces": interfaces,
        "policies": policies,
        "connected_v4": len(connected.get("ipv4") or []),
        "connected_v6": len(connected.get("ipv6") or []),
    }


_BAND_CLASS = {"2.4 GHz": "band-24", "5 GHz": "band-5", "6 GHz": "band-6"}
_BAND_ORDER = {"2.4 GHz": 0, "5 GHz": 1, "6 GHz": 2, "": 9}
_SECURITY_LABELS = {
    "WPA2PSK": "WPA2-PSK",
    "WPA3PSK": "WPA3-PSK",
    "WPA2_WPA3_MIXED": "WPA2/WPA3",
    "WPAand11i": "WPA/WPA2",
    "11iandWPA3": "WPA2/WPA3",
    "OWE": "OWE",
    "UNDEFINED": "-",
}
_OPMODE_LABELS = {
    "AP": "AP",
    "AP_GUEST": "Gast-AP",
    "REPEATER": "Uplink (Repeater)",
    "STATION": "Uplink (Client)",
}


def _utilization_class(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value <= 30:
        return "low"
    if value <= 60:
        return "medium"
    return "high"


def _signal_class(value: int | None, unit: str) -> str:
    if value is None:
        return "unknown"
    if unit == "%":
        if value >= 70:
            return "good"
        if value >= 40:
            return "medium"
        return "bad"
    if value >= -60:
        return "good"
    if value >= -75:
        return "medium"
    return "bad"


def _interface_view(interface: dict, clients: list[dict]) -> dict:
    band = str(interface.get("band") or "")
    utilization = interface.get("channel_utilization")
    try:
        utilization_value = int(utilization) if utilization is not None else None
    except (TypeError, ValueError):
        utilization_value = None
    streams_tx = interface.get("streams_tx")
    streams_rx = interface.get("streams_rx")
    if streams_tx and streams_rx:
        streams_label = f"{streams_tx}×{streams_rx}"
    elif streams_tx or streams_rx:
        streams_label = f"{streams_tx or streams_rx}×"
    else:
        streams_label = "-"
    security = str(interface.get("security") or "")
    return dict(interface) | {
        "band_class": _BAND_CLASS.get(band, "band-unknown"),
        "mode_label": _OPMODE_LABELS.get(
            str(interface.get("opmode") or "").upper(),
            str(interface.get("opmode") or "-"),
        ),
        "security_label": _SECURITY_LABELS.get(security, security or "-"),
        "utilization_label": (
            f"{utilization_value} %" if utilization_value is not None else "-"
        ),
        "utilization_class": _utilization_class(utilization_value),
        "streams_label": streams_label,
        "clients": clients,
    }


def wlan_view(entry: dict | None, open_ips: set[str] | None = None) -> dict | None:
    if not entry:
        return None
    open_ips = open_ips or set()
    data = entry.get("data") or {}
    interfaces = [
        item for item in data.get("interfaces") or [] if isinstance(item, dict)
    ]
    raw_clients = [
        item for item in data.get("clients") or [] if isinstance(item, dict)
    ]

    clients_by_ap: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for client in raw_clients:
        signal = client.get("signal")
        try:
            signal_value = int(signal) if signal is not None else None
        except (TypeError, ValueError):
            signal_value = None
        unit = str(client.get("signal_unit") or "")
        signal_label = (
            f"{signal_value} {unit}".strip() if signal_value is not None else "-"
        )
        rx = client.get("rx_kbps")
        tx = client.get("tx_kbps")
        rates = []
        for value in (rx, tx):
            try:
                rates.append(f"{int(value) / 1000:.0f}")
            except (TypeError, ValueError):
                rates.append("-")
        rate_label = f"{rates[0]} / {rates[1]} Mbit/s"
        ip = client.get("client_ip")
        ap_band = str(client.get("ap_band") or "")
        clients_by_ap[
            (str(client.get("ap_node_uid") or ""), str(client.get("ap_interface_name") or ""))
        ].append(
            dict(client)
            | {
                "ap_band_class": _BAND_CLASS.get(ap_band, "band-unknown"),
                "signal_label": signal_label,
                "signal_class": _signal_class(signal_value, unit),
                "rate_label": rate_label,
                "source_label": source_label(client.get("source")),
                "http_open": bool(ip and ip in open_ips),
                "http_url": http_url(ip) if ip and ip in open_ips else None,
            }
        )

    devices_by_key: dict[str, dict] = {}
    for interface in interfaces:
        key = str(
            interface.get("device_ip")
            or interface.get("node_uid")
            or interface.get("device_name")
        )
        device = devices_by_key.setdefault(
            key,
            {
                "key": key,
                "name": str(interface.get("device_name") or "-"),
                "model": str(interface.get("device_model") or ""),
                "ip": interface.get("device_ip"),
                "online": bool(interface.get("online")),
                "source": str(interface.get("source") or ""),
                "source_label": source_label(interface.get("source")),
                "description": str(interface.get("description") or ""),
                "interfaces": [],
                "uplinks": [],
                "ap_count": 0,
                "guest_count": 0,
                "client_count": 0,
            },
        )
        iface_clients = clients_by_ap.get(
            (
                str(interface.get("node_uid") or ""),
                str(interface.get("interface_name") or ""),
            ),
            [],
        )
        view = _interface_view(interface, iface_clients)
        if interface.get("is_uplink"):
            device["uplinks"].append(view)
        else:
            device["interfaces"].append(view)
            device["ap_count"] += 1
            if interface.get("is_guest"):
                device["guest_count"] += 1
            device["client_count"] += len(iface_clients)

    def device_ip_key(device: dict) -> tuple[int, int, int, str]:
        name = str(device.get("name") or "").lower()
        try:
            address = ipaddress.ip_address(str(device.get("ip")))
        except (TypeError, ValueError):
            return (1, 0, 0, name)
        return (0, address.version, int(address), name)

    devices = list(devices_by_key.values())
    for device in devices:
        device["interfaces"].sort(
            key=lambda item: (
                _BAND_ORDER.get(str(item.get("band") or ""), 9),
                str(item.get("interface_name") or "").lower(),
            )
        )
        device["uplinks"].sort(
            key=lambda item: str(item.get("interface_name") or "").lower()
        )
        device["online_label"] = "🟢 online" if device["online"] else "🔴 offline"
        device["ip_open"] = bool(device["ip"] and device["ip"] in open_ips)
    devices.sort(key=device_ip_key)

    clients = [client for items in clients_by_ap.values() for client in items]
    clients.sort(
        key=lambda item: (
            str(item.get("ap_device_name") or "").lower(),
            str(item.get("ap_interface_name") or "").lower(),
            str(item.get("client_name") or "").lower(),
        )
    )

    ap_interfaces = [
        interface for interface in interfaces if not interface.get("is_uplink")
    ]
    bands = {
        band: sum(1 for item in ap_interfaces if item.get("band") == band)
        for band in ("2.4 GHz", "5 GHz", "6 GHz")
    }
    primary_5g = None
    for device in devices:
        for interface in device["interfaces"]:
            if interface.get("band") != "5 GHz":
                continue
            primary_5g = {
                "device_name": device["name"],
                "channel": interface.get("channel"),
                "channel_width": interface.get("channel_width"),
                "utilization_label": interface.get("utilization_label"),
                "utilization_class": interface.get("utilization_class"),
            }
            break
        if primary_5g:
            break
    return {
        "started_at": entry.get("started_at"),
        "devices": devices,
        "clients": clients,
        "summary": {
            "devices": len(devices),
            "online_devices": sum(1 for device in devices if device["online"]),
            "ap_radios": len(ap_interfaces),
            "uplinks": len(interfaces) - len(ap_interfaces),
            "clients": len(clients),
            "guest_radios": sum(1 for item in ap_interfaces if item.get("is_guest")),
            "bands": bands,
            "primary_5g": primary_5g,
        },
    }


_EVENT_SEVERITY_CLASSES = {
    "critical": "failed",
    "warning": "stale",
    "info": "",
}


def _format_event_time(value: str | None) -> str:
    if not value:
        return "-"
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%y %H:%M")
    except ValueError:
        return value


def wlan_events_view(
    conn: sqlite3.Connection,
    *,
    days: int = 7,
    include_info: bool = False,
    limit: int = 50,
) -> list[dict]:
    rows = db.load_wlan_events(
        conn, days=days, include_info=include_info, limit=limit
    )
    events = []
    for row in rows:
        event_id = int(row.get("event_id") or 0)
        severity = str(row.get("severity") or "info")
        entries = int(row.get("entries") or 0)
        occurrences = int(row.get("occurrences") or 0)
        message = str(row.get("message") or "")
        events.append(
            dict(row)
            | {
                "event_id": event_id,
                "severity": severity,
                "severity_label": SEVERITY_LABELS.get(severity, severity),
                "severity_class": _EVENT_SEVERITY_CLASSES.get(severity, ""),
                "label": EVENT_LABELS.get(event_id) or message[:160] or "WLAN-Ereignis",
                "node": str(row.get("node") or ""),
                "channel": str(row.get("channel") or ""),
                "count": max(entries, occurrences),
                "last_seen_label": _format_event_time(row.get("last_seen")),
                "first_seen_label": _format_event_time(row.get("first_seen")),
            }
        )
    return events


def _event_summary_item(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "event_at": row.get("event_at"),
        "event_at_label": _format_event_time(row.get("event_at")),
        "channel": str(row.get("channel") or ""),
        "node": str(row.get("node") or ""),
        "message": str(row.get("message") or ""),
    }


def wlan_events_summary(
    conn: sqlite3.Connection, *, hours: int = 24
) -> dict:
    rows = db.load_wlan_event_counts(conn, hours=hours)
    counts = {int(row["event_id"]): int(row["entries"]) for row in rows}
    cac_rows = [
        row
        for row in (
            db.latest_wlan_event(conn, 794),
            db.latest_wlan_event(conn, 787),
        )
        if row
    ]
    last_cac = max(cac_rows, key=lambda row: row["event_at"]) if cac_rows else None
    radar = counts.get(796, 0)
    cac = counts.get(794, 0) + counts.get(787, 0)
    interference = counts.get(791, 0)
    strong_interference = counts.get(783, 0)
    return {
        "radar": radar,
        "cac": cac,
        "interference": interference,
        "strong_interference": strong_interference,
        "repeater": counts.get(719, 0),
        "bandwidth": counts.get(674, 0),
        "active": bool(radar or cac or interference or strong_interference),
        "last_radar": _event_summary_item(db.latest_wlan_event(conn, 796)),
        "last_cac": _event_summary_item(last_cac),
        "last_interference": _event_summary_item(db.latest_wlan_event(conn, 791)),
    }


def dashboard(conn: sqlite3.Connection, settings: Settings) -> dict:
    latest = db.latest_snapshot(conn)
    latest_ok = db.latest_snapshot(conn, only_successful=True)
    history_rows = db.snapshot_history(conn, limit=8)
    failed_latest = conn.execute(
        "SELECT * FROM snapshots WHERE status = 'failed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if failed_latest and latest_ok and failed_latest["id"] < latest_ok["id"]:
        failed_latest = None
    counts = db.counts(conn)
    dns_records = db.load_dns_records(conn)
    open_ips = db.load_http_open_ips(conn)
    return {
        "latest": latest,
        "history": history_rows,
        "failed": dict(failed_latest) if failed_latest else None,
        "counts": counts,
        "ip_modes": db.ip_mode_counts(conn),
        "conflicts": [record for record in dns_records if record.status == "conflict"],
        "http_open_ips": open_ips,
        "mwan3": mwan3_view(db.latest_mwan3(conn)),
        "wlan": wlan_view(db.latest_wlan(conn), open_ips),
        "wlan_events": wlan_events_view(conn, days=7, limit=3),
        "wlan_event_summary": wlan_events_summary(conn, hours=24),
        "settings": settings,
    }


def _table_row(
    node: MeshNode,
    depth: int,
    is_master: bool,
    speed: tuple[int | None, int | None] | None = None,
) -> dict:
    icon, label, css = connection_meta(node.connection_type)
    rx_kbps, tx_kbps = speed or (node.plc_rx_kbps, node.plc_tx_kbps)
    speed_view = speed_display(rx_kbps, tx_kbps)
    return {
        "depth": depth,
        "indent": max(0, depth - 1),
        "node": node,
        "is_master": is_master,
        "role_label": display_role(node.mesh_role),
        "connection_icon": icon,
        "connection_label": label,
        "connection_class": css,
        "speed_label": speed_view["label"],
        "speed_icon": speed_view["icon"],
        "speed_class": speed_view["css"],
        "online_label": "🟢 online" if node.online else "🔴 offline",
        "source": node.source,
    }


def _name_key(node: MeshNode) -> tuple[str, str]:
    return (node.device_name.lower(), node.uid)


def _child_key(node: MeshNode) -> tuple[int, str, str]:
    return (1 if node.source == "openwrt" else 0, node.device_name.lower(), node.uid)


def _ip_key(node: MeshNode) -> tuple[int, int, str, str]:
    try:
        address = ipaddress.IPv4Address(node.ip) if node.ip else None
    except ValueError:
        address = None
    if address is None:
        return (1, 0, node.device_name.lower(), node.uid)
    return (0, int(address), node.device_name.lower(), node.uid)


def _openwrt_parent_ip(settings: Settings) -> str:
    if not settings.openwrt_enabled:
        return ""
    return (settings.openwrt_topology_parent_ip or "").strip()


def _link_speed(link: MeshLink) -> int:
    return max(
        (value for value in (link.rx_kbps, link.tx_kbps) if value is not None),
        default=0,
    )


def _openwrt_host_node(
    host: HostRecord, *, parent_uid: str | None, online: bool
) -> MeshNode:
    return MeshNode(
        uid=f"openwrt:{host.key}",
        device_name=host.hostname or host.ip or host.key,
        device_model="",
        mesh_role="",
        ip=host.ip,
        mac=host.mac,
        online=online,
        parent_uid=parent_uid,
        connection_type="",
        description=host.description,
        source="openwrt",
    )


def topology(conn: sqlite3.Connection, settings: Settings) -> dict:
    openwrt_parent_ip = _openwrt_parent_ip(settings)
    latest = db.latest_snapshot(conn)
    if latest is None:
        return {
            "latest": None,
            "rows": [],
            "offline": [],
            "offline_rows": [],
            "online": [],
            "links": [],
            "master": None,
            "summary": {"total": 0, "online": 0, "offline": 0, "plc": 0},
            "openwrt_parent": None,
            "openwrt_note": "",
            "http_open_ips": db.load_http_open_ips(conn),
        }

    nodes = db.nodes_for_snapshot(conn, latest["id"])
    links = db.links_for_snapshot(conn, latest["id"])
    by_uid = {node.uid: node for node in nodes}
    link_by_pair: dict[frozenset[str], MeshLink] = {}
    for link in links:
        if link.state.upper() != "CONNECTED":
            continue
        key = frozenset((link.node_a_uid, link.node_b_uid))
        current = link_by_pair.get(key)
        if current is None or _link_speed(link) > _link_speed(current):
            link_by_pair[key] = link
    node_speeds: dict[str, tuple[int | None, int | None]] = {}
    for node in nodes:
        if not node.parent_uid:
            continue
        link = link_by_pair.get(frozenset((node.uid, node.parent_uid)))
        if link is not None:
            node_speeds[node.uid] = (link.rx_kbps, link.tx_kbps)
    children: dict[str | None, list[MeshNode]] = defaultdict(list)
    for node in nodes:
        parent = node.parent_uid if node.parent_uid in by_uid else None
        children[parent].append(node)

    openwrt_parent = None
    openwrt_children: list[MeshNode] = []
    openwrt_offline: list[MeshNode] = []
    openwrt_note = ""
    if openwrt_parent_ip:
        openwrt_parent = next(
            (node for node in nodes if node.ip == openwrt_parent_ip), None
        )
        existing_ips = {node.ip for node in nodes if node.ip}
        for host in db.load_hosts(conn):
            if host.source != "openwrt" or not host.hostname or not host.ip:
                continue
            if host.ip in existing_ips:
                continue
            if host.active:
                if openwrt_parent is not None:
                    openwrt_children.append(
                        _openwrt_host_node(host, parent_uid=openwrt_parent.uid, online=True)
                    )
            else:
                openwrt_offline.append(
                    _openwrt_host_node(host, parent_uid=None, online=False)
                )
        if openwrt_parent is None:
            openwrt_note = f"OpenWrt-Parent {openwrt_parent_ip} nicht gefunden"

    if openwrt_parent is not None:
        for node in openwrt_children:
            by_uid[node.uid] = node
            children[openwrt_parent.uid].append(node)

    rows: list[dict] = []
    visited: set[str] = set()

    def walk(uid: str, depth: int, is_master: bool) -> None:
        node = by_uid.get(uid)
        if node is None or uid in visited:
            return
        visited.add(uid)
        rows.append(_table_row(node, depth, is_master, node_speeds.get(node.uid)))
        for child in sorted(children.get(uid, []), key=_child_key):
            walk(child.uid, depth + 1, False)

    master_uid = next(
        (node.uid for node in nodes if node.mesh_role.lower() == "master"),
        None,
    )
    root_uid = master_uid or (nodes[0].uid if nodes else None)
    if root_uid:
        root = by_uid[root_uid]
        visited.add(root_uid)
        rows.append(_table_row(root, 0, root_uid == master_uid, node_speeds.get(root_uid)))

        direct = sorted(
            (node for node in children.get(root_uid, []) if node.mesh_role.lower() != "slave"),
            key=_child_key,
        )
        slaves = sorted(
            (node for node in children.get(root_uid, []) if node.mesh_role.lower() == "slave"),
            key=_ip_key,
        )
        for node in (*direct, *slaves):
            walk(node.uid, 1, False)

    offline = [node for node in nodes if not node.online or node.uid not in visited]
    offline.sort(key=lambda node: (node.device_name.lower(), node.uid))
    online = [node for node in nodes if node.online and node.uid in visited]
    summary = {
        "total": len(nodes),
        "online": len(online),
        "offline": len(offline),
        "plc": sum(1 for node in nodes if (node.connection_type or "").upper() == "PLC"),
    }
    offline_all = offline + openwrt_offline
    return {
        "latest": latest,
        "rows": rows,
        "offline": offline_all,
        "offline_rows": [_table_row(node, 0, False) for node in offline_all],
        "online": online,
        "links": links,
        "master": by_uid.get(master_uid) if master_uid else None,
        "summary": summary,
        "openwrt_parent": openwrt_parent,
        "openwrt_note": openwrt_note,
        "http_open_ips": db.load_http_open_ips(conn),
    }


def hosts(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    status: str = "",
    source: str = "",
    ip_mode: str = "",
) -> list[dict]:
    clauses: list[str] = []
    params: list[object] = []
    if query:
        like = f"%{query.lower()}%"
        clauses.append(
            "(lower(COALESCE(hostname, '')) LIKE ? OR COALESCE(ip, '') LIKE ? "
            "OR lower(COALESCE(mac, '')) LIKE ? OR lower(COALESCE(description, '')) LIKE ?)"
        )
        params.extend([like, like, like, like])
    if status == "active":
        clauses.append("active = 1")
    elif status == "inactive":
        clauses.append("active = 0")
    if ip_mode == "static":
        clauses.append("lower(COALESCE(address_source, '')) = 'static'")
    elif ip_mode == "dhcp":
        clauses.append(
            "lower(COALESCE(address_source, '')) = 'dhcp' "
            "AND COALESCE(static_dhcp, 0) = 0"
        )
    elif ip_mode == "reserved":
        clauses.append(
            "lower(COALESCE(address_source, '')) = 'dhcp' AND static_dhcp = 1"
        )
    elif ip_mode == "unknown":
        clauses.append(
            "(COALESCE(address_source, '') = '' "
            "OR (lower(address_source) = 'dhcp' AND static_dhcp IS NULL))"
        )
    source_values = source_filter_values(source)
    if source_values:
        placeholders = ", ".join("?" for _ in source_values)
        clauses.append(f"source IN ({placeholders})")
        params.extend(source_values)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT key, hostname, ip, mac, active, interface_type, last_seen,
               source, description, first_seen, updated_at, address_source,
               static_dhcp
        FROM hosts {where}
        ORDER BY active DESC, lower(COALESCE(hostname, '')), key
        """,
        params,
    ).fetchall()
    open_ips = db.load_http_open_ips(conn)
    return [
        dict(row)
        | {
            "http_open": row["ip"] in open_ips,
            "http_url": http_url(row["ip"]) if row["ip"] in open_ips else None,
            "source_label": source_label(row["source"]),
            "ip_mode": ip_mode_meta(
                row["address_source"], row["static_dhcp"], row["source"]
            ),
        }
        for row in rows
    ]


def wlan(
    conn: sqlite3.Connection, *, days: int = 7, include_info: bool = False
) -> dict:
    open_ips = db.load_http_open_ips(conn)
    event_days = max(1, min(days, 365))
    return {
        "latest": db.latest_snapshot(conn),
        "wlan": wlan_view(db.latest_wlan(conn), open_ips),
        "events": wlan_events_view(
            conn,
            days=event_days,
            include_info=include_info,
            limit=50,
        ),
        "event_summary": wlan_events_summary(conn, hours=24),
        "event_days": event_days,
        "include_info": include_info,
        "http_open_ips": open_ips,
    }


def dns(conn: sqlite3.Connection, settings: Settings) -> dict:
    records = db.load_dns_records(conn)
    latest = db.latest_snapshot(conn)
    return {
        "records": records,
        "static": db.load_dns_static(conn),
        "latest": latest,
        "serial": db.get_setting(conn, "zone_serial", "0"),
        "settings": settings,
        "conflicts": [record for record in records if record.status == "conflict"],
        "http_open_ips": db.load_http_open_ips(conn),
    }


def history(conn: sqlite3.Connection, limit: int = 100) -> dict:
    return {
        "snapshots": db.snapshot_history(conn, limit=limit),
        "latest": db.latest_snapshot(conn),
    }


def snapshot(conn: sqlite3.Connection, snapshot_id: int) -> dict | None:
    detail = db.snapshot_detail(conn, snapshot_id)
    if detail is None:
        return None
    return {
        "snapshot": detail,
        "nodes": db.nodes_for_snapshot(conn, snapshot_id),
        "links": db.links_for_snapshot(conn, snapshot_id),
        "http_open_ips": db.load_http_open_ips(conn),
    }
