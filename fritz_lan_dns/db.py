from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .models import DnsRecord, HostRecord, MeshLink, MeshNode, MeshSnapshot
from .util import iso_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    error TEXT,
    warning TEXT,
    duration_ms INTEGER,
    raw_mesh TEXT,
    raw_hosts TEXT,
    node_count INTEGER NOT NULL DEFAULT 0,
    online_count INTEGER NOT NULL DEFAULT 0,
    host_count INTEGER NOT NULL DEFAULT 0,
    dns_count INTEGER NOT NULL DEFAULT 0,
    zone_serial INTEGER
);

CREATE TABLE IF NOT EXISTS mesh_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    uid TEXT NOT NULL,
    device_name TEXT,
    device_model TEXT,
    mesh_role TEXT,
    ip TEXT,
    mac TEXT,
    online INTEGER NOT NULL DEFAULT 0,
    parent_uid TEXT,
    connection_type TEXT,
    plc_rx_kbps INTEGER,
    plc_tx_kbps INTEGER,
    description TEXT
);
CREATE INDEX IF NOT EXISTS idx_mesh_nodes_snapshot ON mesh_nodes(snapshot_id);

CREATE TABLE IF NOT EXISTS mesh_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    node_a_uid TEXT NOT NULL,
    node_b_uid TEXT NOT NULL,
    interface_type TEXT,
    interface_name TEXT,
    state TEXT,
    rx_kbps INTEGER,
    tx_kbps INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mesh_links_snapshot ON mesh_links(snapshot_id);

CREATE TABLE IF NOT EXISTS hosts (
    key TEXT PRIMARY KEY,
    hostname TEXT,
    ip TEXT,
    mac TEXT,
    active INTEGER NOT NULL DEFAULT 0,
    interface_type TEXT,
    last_seen TEXT,
    first_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source TEXT,
    description TEXT
);
CREATE INDEX IF NOT EXISTS idx_hosts_hostname ON hosts(hostname);

CREATE TABLE IF NOT EXISTS host_snapshots (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    host_key TEXT NOT NULL,
    hostname TEXT,
    ip TEXT,
    mac TEXT,
    active INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT,
    PRIMARY KEY (snapshot_id, host_key)
);

CREATE TABLE IF NOT EXISTS dns_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL,
    fqdn TEXT NOT NULL,
    ip TEXT NOT NULL,
    mac TEXT,
    status TEXT NOT NULL,
    conflict TEXT,
    last_seen TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(hostname, ip)
);

CREATE TABLE IF NOT EXISTS dns_static (
    hostname TEXT PRIMARY KEY,
    ip TEXT NOT NULL,
    comment TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
    finally:
        conn.close()


def init_schema(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def start_snapshot(conn: sqlite3.Connection, started_at: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO snapshots (started_at, status) VALUES (?, 'running')",
        (started_at or iso_now(),),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: int,
    *,
    status: str,
    duration_ms: int | None = None,
    error: str | None = None,
    warning: str | None = None,
    node_count: int | None = None,
    online_count: int | None = None,
    host_count: int | None = None,
    dns_count: int | None = None,
    zone_serial: int | None = None,
) -> None:
    fields = ["finished_at = ?", "status = ?"]
    params: list[object] = [iso_now(), status]
    for column, value in (
        ("duration_ms", duration_ms),
        ("error", error),
        ("warning", warning),
        ("node_count", node_count),
        ("online_count", online_count),
        ("host_count", host_count),
        ("dns_count", dns_count),
        ("zone_serial", zone_serial),
    ):
        if value is not None:
            fields.append(f"{column} = ?")
            params.append(value)
    params.append(snapshot_id)
    conn.execute(f"UPDATE snapshots SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()


def store_snapshot_data(
    conn: sqlite3.Connection,
    snapshot_id: int,
    mesh: MeshSnapshot,
    hosts: list[HostRecord],
    raw_mesh: str,
    raw_hosts: str,
    warning: str | None,
) -> None:
    conn.execute("DELETE FROM mesh_nodes WHERE snapshot_id = ?", (snapshot_id,))
    conn.execute("DELETE FROM mesh_links WHERE snapshot_id = ?", (snapshot_id,))
    conn.executemany(
        """
        INSERT INTO mesh_nodes (
            snapshot_id, uid, device_name, device_model, mesh_role, ip, mac,
            online, parent_uid, connection_type, plc_rx_kbps, plc_tx_kbps,
            description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_id,
                node.uid,
                node.device_name,
                node.device_model,
                node.mesh_role,
                node.ip,
                node.mac,
                1 if node.online else 0,
                node.parent_uid,
                node.connection_type,
                node.plc_rx_kbps,
                node.plc_tx_kbps,
                node.description,
            )
            for node in mesh.nodes
        ],
    )
    conn.executemany(
        """
        INSERT INTO mesh_links (
            snapshot_id, node_a_uid, node_b_uid, interface_type,
            interface_name, state, rx_kbps, tx_kbps
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_id,
                link.node_a_uid,
                link.node_b_uid,
                link.interface_type,
                link.interface_name,
                link.state,
                link.rx_kbps,
                link.tx_kbps,
            )
            for link in mesh.links
        ],
    )
    _upsert_hosts(conn, snapshot_id, hosts)
    conn.execute(
        """
        UPDATE snapshots SET raw_mesh = ?, raw_hosts = ?, warning = ?,
            node_count = ?, online_count = ?, host_count = ?
        WHERE id = ?
        """,
        (
            raw_mesh,
            raw_hosts,
            warning,
            len(mesh.nodes),
            sum(1 for node in mesh.nodes if node.online),
            len(hosts),
            snapshot_id,
        ),
    )
    conn.commit()


def _upsert_hosts(
    conn: sqlite3.Connection, snapshot_id: int, hosts: Iterable[HostRecord]
) -> None:
    now = iso_now()
    conn.execute("UPDATE hosts SET active = 0")
    for host in hosts:
        conn.execute(
            """
            INSERT INTO hosts (
                key, hostname, ip, mac, active, interface_type, last_seen,
                first_seen, updated_at, source, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                hostname = COALESCE(NULLIF(excluded.hostname, ''), hosts.hostname),
                ip = COALESCE(NULLIF(excluded.ip, ''), hosts.ip),
                mac = COALESCE(NULLIF(excluded.mac, ''), hosts.mac),
                active = excluded.active,
                interface_type = COALESCE(
                    NULLIF(excluded.interface_type, ''), hosts.interface_type
                ),
                last_seen = COALESCE(
                    NULLIF(excluded.last_seen, ''), hosts.last_seen
                ),
                updated_at = excluded.updated_at,
                source = excluded.source,
                description = COALESCE(
                    NULLIF(excluded.description, ''), hosts.description
                )
            """,
            (
                host.key,
                host.hostname,
                host.ip,
                host.mac,
                1 if host.active else 0,
                host.interface_type,
                host.last_seen,
                now,
                now,
                host.source,
                host.description,
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO host_snapshots (
                snapshot_id, host_key, hostname, ip, mac, active, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                host.key,
                host.hostname,
                host.ip,
                host.mac,
                1 if host.active else 0,
                host.last_seen,
            ),
        )


def load_hosts(conn: sqlite3.Connection) -> list[HostRecord]:
    rows = conn.execute(
        """
        SELECT key, hostname, ip, mac, active, interface_type, last_seen,
               source, description
        FROM hosts
        ORDER BY lower(COALESCE(hostname, '')), key
        """
    ).fetchall()
    return [
        HostRecord(
            key=row["key"],
            hostname=row["hostname"] or "",
            ip=row["ip"],
            mac=row["mac"],
            active=bool(row["active"]),
            interface_type=row["interface_type"] or "",
            last_seen=row["last_seen"],
            source=row["source"] or "",
            description=row["description"] or "",
        )
        for row in rows
    ]


def replace_dns_records(conn: sqlite3.Connection, records: Iterable[DnsRecord]) -> None:
    now = iso_now()
    conn.execute("DELETE FROM dns_records")
    conn.executemany(
        """
        INSERT INTO dns_records (
            hostname, fqdn, ip, mac, status, conflict, last_seen, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                record.hostname,
                record.fqdn,
                record.ip,
                record.mac,
                record.status,
                record.conflict,
                record.last_seen,
                now,
            )
            for record in records
        ],
    )
    conn.commit()


def load_dns_records(conn: sqlite3.Connection) -> list[DnsRecord]:
    rows = conn.execute(
        """
        SELECT hostname, fqdn, ip, mac, status, conflict, last_seen
        FROM dns_records
        ORDER BY lower(fqdn), ip
        """
    ).fetchall()
    return [
        DnsRecord(
            hostname=row["hostname"],
            fqdn=row["fqdn"],
            ip=row["ip"],
            mac=row["mac"],
            status=row["status"],
            conflict=row["conflict"],
            last_seen=row["last_seen"],
        )
        for row in rows
    ]


def load_dns_static(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT hostname, ip, comment FROM dns_static ORDER BY hostname"
    ).fetchall()
    return [dict(row) for row in rows]


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def latest_snapshot(conn: sqlite3.Connection, only_successful: bool = False) -> dict | None:
    where = "WHERE status = 'ok'" if only_successful else ""
    row = conn.execute(
        "SELECT id, started_at, finished_at, status, error, warning, duration_ms, "
        "node_count, online_count, host_count, dns_count, zone_serial "
        f"FROM snapshots {where} ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return _row_to_dict(row)


def snapshot_history(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, started_at, finished_at, status, error, warning, duration_ms,
               node_count, online_count, host_count, dns_count, zone_serial
        FROM snapshots ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def snapshot_detail(conn: sqlite3.Connection, snapshot_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, started_at, finished_at, status, error, warning, duration_ms, "
        "node_count, online_count, host_count, dns_count, zone_serial "
        "FROM snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    return _row_to_dict(row)


def nodes_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[MeshNode]:
    rows = conn.execute(
        """
        SELECT uid, device_name, device_model, mesh_role, ip, mac, online,
               parent_uid, connection_type, plc_rx_kbps, plc_tx_kbps, description
        FROM mesh_nodes WHERE snapshot_id = ? ORDER BY lower(device_name), uid
        """,
        (snapshot_id,),
    ).fetchall()
    return [
        MeshNode(
            uid=row["uid"],
            device_name=row["device_name"] or "",
            device_model=row["device_model"] or "",
            mesh_role=row["mesh_role"] or "",
            ip=row["ip"],
            mac=row["mac"],
            online=bool(row["online"]),
            parent_uid=row["parent_uid"],
            connection_type=row["connection_type"],
            plc_rx_kbps=row["plc_rx_kbps"],
            plc_tx_kbps=row["plc_tx_kbps"],
            description=row["description"] or "",
        )
        for row in rows
    ]


def links_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[MeshLink]:
    rows = conn.execute(
        """
        SELECT node_a_uid, node_b_uid, interface_type, interface_name, state,
               rx_kbps, tx_kbps
        FROM mesh_links WHERE snapshot_id = ? ORDER BY id
        """,
        (snapshot_id,),
    ).fetchall()
    return [
        MeshLink(
            node_a_uid=row["node_a_uid"],
            node_b_uid=row["node_b_uid"],
            interface_type=row["interface_type"] or "",
            interface_name=row["interface_name"] or "",
            state=row["state"] or "",
            rx_kbps=row["rx_kbps"],
            tx_kbps=row["tx_kbps"],
        )
        for row in rows
    ]


def prune_snapshots(conn: sqlite3.Connection, retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    cur = conn.execute("DELETE FROM snapshots WHERE started_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def prune_hosts(conn: sqlite3.Connection, retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    cur = conn.execute(
        "DELETE FROM hosts WHERE active = 0 AND updated_at < ?", (cutoff,)
    )
    conn.commit()
    return cur.rowcount


def counts(conn: sqlite3.Connection) -> dict:
    latest = latest_snapshot(conn)
    if latest is None:
        return {
            "nodes": 0,
            "online": 0,
            "hosts": 0,
            "active_hosts": 0,
            "dns_records": 0,
            "dns_conflicts": 0,
        }
    snapshot_id = latest["id"]
    return {
        "nodes": int(latest["node_count"] or 0),
        "online": int(latest["online_count"] or 0),
        "hosts": conn.execute("SELECT COUNT(*) FROM hosts").fetchone()[0],
        "active_hosts": conn.execute("SELECT COUNT(*) FROM hosts WHERE active = 1").fetchone()[0],
        "dns_records": conn.execute(
            "SELECT COUNT(*) FROM dns_records WHERE status != 'conflict'"
        ).fetchone()[0],
        "dns_conflicts": conn.execute(
            "SELECT COUNT(*) FROM dns_records WHERE status = 'conflict'"
        ).fetchone()[0],
        "snapshot_id": snapshot_id,
    }
