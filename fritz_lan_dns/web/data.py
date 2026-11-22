from __future__ import annotations

import sqlite3
from collections import defaultdict

from .. import db
from ..config import Settings
from ..models import MeshNode

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
    }


def topology(conn: sqlite3.Connection) -> dict:
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
        }

    nodes = db.nodes_for_snapshot(conn, latest["id"])
    links = db.links_for_snapshot(conn, latest["id"])
    by_uid = {node.uid: node for node in nodes}
    children: dict[str | None, list[MeshNode]] = defaultdict(list)
    for node in nodes:
        parent = node.parent_uid if node.parent_uid in by_uid else None
        children[parent].append(node)
    for group in children.values():
        group.sort(key=lambda node: (node.device_name.lower(), node.uid))

    rows: list[dict] = []
    visited: set[str] = set()

    def walk(uid: str, depth: int, is_master: bool) -> None:
        node = by_uid.get(uid)
        if node is None or uid in visited:
            return
        visited.add(uid)
        rows.append(_table_row(node, depth, is_master))
        for child in children.get(uid, []):
            walk(child.uid, depth + 1, False)

    master_uid = next(
        (node.uid for node in nodes if node.mesh_role.lower() == "master"),
        None,
    )
    root_uid = master_uid or (nodes[0].uid if nodes else None)
    if root_uid:
        walk(root_uid, 0, root_uid == master_uid)

    offline = [node for node in nodes if not node.online or node.uid not in visited]
    offline.sort(key=lambda node: (node.device_name.lower(), node.uid))
    online = [node for node in nodes if node.online and node.uid in visited]
    summary = {
        "total": len(nodes),
        "online": len(online),
        "offline": len(offline),
        "plc": sum(1 for node in nodes if (node.connection_type or "").upper() == "PLC"),
    }
    return {
        "latest": latest,
        "rows": rows,
        "offline": offline,
        "offline_rows": [_table_row(node, 0, False) for node in offline],
        "online": online,
        "links": links,
        "master": by_uid.get(master_uid) if master_uid else None,
        "summary": summary,
    }


def hosts(
    conn: sqlite3.Connection,
    *,
    query: str = "",
    status: str = "",
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
    return [dict(row) for row in rows]


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
    }
