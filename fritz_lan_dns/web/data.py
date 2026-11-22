from __future__ import annotations

import ipaddress
import sqlite3
from collections import defaultdict

from .. import db
from ..config import Settings
from ..models import HostRecord, MeshNode
from ..util import (
    format_duration,
    http_url,
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


def plc_display(rx_kbps: int | None, tx_kbps: int | None) -> dict[str, str]:
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


def mwan3_view(entry: dict | None) -> dict | None:
    if not entry:
        return None
    data = entry.get("data") or {}
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
    return {
        "latest": latest,
        "history": history_rows,
        "failed": dict(failed_latest) if failed_latest else None,
        "counts": counts,
        "conflicts": [record for record in dns_records if record.status == "conflict"],
        "http_open_ips": db.load_http_open_ips(conn),
        "mwan3": mwan3_view(db.latest_mwan3(conn)),
        "settings": settings,
    }


def _table_row(node: MeshNode, depth: int, is_master: bool) -> dict:
    icon, label, css = connection_meta(node.connection_type)
    plc = plc_display(node.plc_rx_kbps, node.plc_tx_kbps)
    return {
        "depth": depth,
        "indent": max(0, depth - 1),
        "node": node,
        "is_master": is_master,
        "role_label": display_role(node.mesh_role),
        "connection_icon": icon,
        "connection_label": label,
        "connection_class": css,
        "plc_label": plc["label"],
        "plc_icon": plc["icon"],
        "plc_class": plc["css"],
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
        rows.append(_table_row(node, depth, is_master))
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
        rows.append(_table_row(root, 0, root_uid == master_uid))

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
    source_values = source_filter_values(source)
    if source_values:
        placeholders = ", ".join("?" for _ in source_values)
        clauses.append(f"source IN ({placeholders})")
        params.extend(source_values)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT key, hostname, ip, mac, active, interface_type, last_seen,
               source, description, first_seen, updated_at
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
        }
        for row in rows
    ]


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
