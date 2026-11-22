from __future__ import annotations

import sqlite3
from collections import defaultdict

from .. import db
from ..config import Settings
from ..models import MeshNode


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


def topology(conn: sqlite3.Connection) -> dict:
    latest = db.latest_snapshot(conn)
    if latest is None:
        return {"latest": None, "rows": [], "offline": [], "links": [], "master": None}
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

    def walk(uid: str, depth: int) -> None:
        node = by_uid.get(uid)
        if node is None or uid in visited:
            return
        visited.add(uid)
        rows.append(
            {
                "depth": depth,
                "node": node,
                "children": len(children.get(uid, [])),
            }
        )
        for child in children.get(uid, []):
            walk(child.uid, depth + 1)

    master_uid = latest.get("master_uid")
    if not master_uid:
        master_uid = next(
            (node.uid for node in nodes if node.mesh_role.lower() == "master"),
            None,
        )
    if master_uid:
        walk(master_uid, 0)
    for node in nodes:
        if node.uid not in visited:
            walk(node.uid, 0)

    offline = [node for node in nodes if not node.online]
    online = [node for node in nodes if node.online]
    return {
        "latest": latest,
        "rows": rows,
        "offline": offline,
        "online": online,
        "links": links,
        "master": by_uid.get(master_uid) if master_uid else None,
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
