from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fritz_lan_dns import db
from fritz_lan_dns.dns import build_dns_records, ensure_dns_files, write_dns_files
from fritz_lan_dns.fritz.mesh import parse_mesh
from fritz_lan_dns.models import HostRecord, MeshNode, MeshSnapshot
from fritz_lan_dns.util import iso_now
from fritz_lan_dns.web import data as web_data
from tests.helpers import make_settings

FIXTURE = Path(__file__).parent / "fixtures" / "mesh.json"


def _settings(tmp_path: Path):
    return make_settings(tmp_path)


def _mesh():
    return parse_mesh(json.loads(FIXTURE.read_text(encoding="utf-8")), {"router": "Router"})


def _host(name: str, ip: str, *, active: bool = True) -> HostRecord:
    return HostRecord(
        key=f"mac:{name}",
        hostname=name,
        ip=ip,
        mac=f"aa:bb:cc:00:00:{len(name):02x}",
        active=active,
        source="fritz",
        last_seen=iso_now(),
    )


def _node(
    uid: str,
    name: str,
    ip: str,
    *,
    parent: str | None = None,
    role: str = "client",
    online: bool = True,
) -> MeshNode:
    return MeshNode(
        uid=uid,
        device_name=name,
        device_model="",
        mesh_role=role,
        ip=ip,
        mac=None,
        online=online,
        parent_uid=parent,
    )


def _store(conn, mesh, hosts, raw_mesh: str = "{}", raw_hosts: str = "{}") -> int:
    snapshot_id = db.start_snapshot(conn)
    db.store_snapshot_data(conn, snapshot_id, mesh, hosts, raw_mesh, raw_hosts, None)
    db.finish_snapshot(
        conn,
        snapshot_id,
        status="ok",
        duration_ms=10,
        node_count=len(mesh.nodes),
        online_count=sum(1 for node in mesh.nodes if node.online),
        host_count=len(hosts),
    )
    return snapshot_id


def test_snapshot_and_hosts_roundtrip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    mesh = _mesh()
    with db.connect(settings.db_path) as conn:
        snapshot_id = _store(conn, mesh, [_host("printer", "192.0.2.50")])
        latest = db.latest_snapshot(conn)
        assert latest is not None
        assert latest["id"] == snapshot_id
        assert latest["node_count"] == len(mesh.nodes)
        hosts = db.load_hosts(conn)
        assert len(hosts) == 1
        assert hosts[0].hostname == "printer"
        assert db.counts(conn)["hosts"] == 1


def test_missing_host_becomes_inactive_and_stale(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    mesh = _mesh()
    with db.connect(settings.db_path) as conn:
        _store(conn, mesh, [_host("printer", "192.0.2.50")])
        _store(conn, mesh, [])
        hosts = db.load_hosts(conn)
        assert len(hosts) == 1
        assert hosts[0].active is False
        records = build_dns_records(hosts, [], settings.dns_domain)
        assert records[0].status == "stale"


def test_write_dns_files_serial_only_on_change(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    with db.connect(settings.db_path) as conn:
        ensure_dns_files(settings, conn)
        records = build_dns_records([_host("printer", "192.0.2.50")], [], settings.dns_domain)
        first = write_dns_files(settings, conn, records)
        second = write_dns_files(settings, conn, records)
        assert first["serial"] == second["serial"] == 1
        assert settings.hosts_file.read_text(encoding="utf-8") == (
            "192.0.2.50 printer.example.test\n"
        )
        changed = build_dns_records([_host("printer", "192.0.2.51")], [], settings.dns_domain)
        third = write_dns_files(settings, conn, changed)
        assert third["serial"] == 2
        assert "printer IN A 192.0.2.51" in settings.zone_file.read_text(encoding="utf-8")


def test_web_data_topology_hosts_and_dns(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    mesh = _mesh()
    with db.connect(settings.db_path) as conn:
        _store(conn, mesh, [_host("printer", "192.0.2.50")])
        records = build_dns_records(db.load_hosts(conn), [], settings.dns_domain)
        db.replace_dns_records(conn, records)

        topology = web_data.topology(conn)
        assert topology["master"] is not None
        assert topology["master"].device_name == "router"
        assert len(topology["offline"]) == 1
        assert topology["summary"] == {"total": 5, "online": 4, "offline": 1, "plc": 1}

        rows_by_name = {row["node"].device_name: row for row in topology["rows"]}
        assert rows_by_name["router"]["is_master"] is True
        assert rows_by_name["repeater"]["connection_icon"] == "🔌"
        assert rows_by_name["repeater"]["connection_label"] == "LAN"
        assert rows_by_name["repeater"]["role_label"] == "Repeater"
        assert rows_by_name["client"]["role_label"] == ""
        assert rows_by_name["powerline"]["plc_label"] == "180.0 / 200.0 Mbit/s"
        assert rows_by_name["powerline"]["plc_class"] == "ok"
        assert rows_by_name["client"]["indent"] == 0
        assert rows_by_name["repeater"]["indent"] == 0
        assert rows_by_name["powerline"]["indent"] == 1
        assert "prefix" not in rows_by_name["powerline"]
        assert "branch" not in rows_by_name["powerline"]
        assert topology["offline_rows"][0]["role_label"] == ""

        host_rows = web_data.hosts(conn, query="printer", status="active")
        assert len(host_rows) == 1
        assert host_rows[0]["hostname"] == "printer"

        dns = web_data.dns(conn, settings)
        assert len(dns["records"]) == 1
        assert dns["records"][0].fqdn == "printer.example.test"

        dashboard = web_data.dashboard(conn, settings)
        assert dashboard["counts"]["nodes"] == len(mesh.nodes)


def test_dns_source_roundtrip_and_filter(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    openwrt_host = HostRecord(
        key="mac:aa:bb:cc:99:00:01",
        hostname="camera",
        ip="192.0.2.160",
        mac="aa:bb:cc:99:00:01",
        active=True,
        source="openwrt",
        last_seen=iso_now(),
    )
    with db.connect(settings.db_path) as conn:
        _store(conn, _mesh(), [_host("printer", "192.0.2.50"), openwrt_host])
        records = build_dns_records(db.load_hosts(conn), [], settings.dns_domain)
        db.replace_dns_records(conn, records)

        by_name = {record.hostname: record for record in db.load_dns_records(conn)}
        assert by_name["printer"].source == "fritz"
        assert by_name["camera"].source == "openwrt"

        rows = web_data.hosts(conn, source="openwrt")
        assert len(rows) == 1
        assert rows[0]["hostname"] == "camera"
        assert rows[0]["source_label"] == "OpenWrt"


def test_hosts_source_filter_groups_mesh_as_fritz(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    mesh_host = HostRecord(
        key="mesh:abc",
        hostname="mesh-only",
        ip="192.0.2.170",
        mac=None,
        active=True,
        source="mesh",
        last_seen=iso_now(),
    )
    with db.connect(settings.db_path) as conn:
        _store(conn, _mesh(), [_host("printer", "192.0.2.50"), mesh_host])

        rows = web_data.hosts(conn, source="fritz")
        names = {row["hostname"] for row in rows}
        assert names == {"printer", "mesh-only"}
        assert {row["source_label"] for row in rows} == {"FritzBox"}

        openwrt_rows = web_data.hosts(conn, source="openwrt")
        assert openwrt_rows == []


def test_init_schema_migrates_dns_source(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.execute(
        """
        CREATE TABLE dns_records (
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
        )
        """
    )
    conn.commit()
    conn.close()

    db.init_schema(settings.db_path)

    with db.connect(settings.db_path) as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(dns_records)")
        }
    assert "source" in columns


def test_topology_groups_direct_devices_then_slaves_by_ip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    nodes = [
        _node("master", "router", "192.0.2.1", role="master"),
        _node("alpha", "alpha", "192.0.2.50", parent="master"),
        _node("alpha-zeta", "zeta", "192.0.2.60", parent="alpha"),
        _node("alpha-able", "Able", "192.0.2.70", parent="alpha"),
        _node("yankee", "yankee", "192.0.2.80", parent="master"),
        _node("zulu", "zulu", "192.0.2.2", parent="master", role="slave"),
        _node("zulu-able", "able-child", "192.0.2.5", parent="zulu"),
        _node("zulu-zeta", "zeta-child", "192.0.2.6", parent="zulu"),
        _node("bravo", "bravo", "192.0.2.4", parent="master", role="slave"),
    ]
    mesh = MeshSnapshot(nodes=nodes, links=[], master_uid="master", raw={})
    with db.connect(settings.db_path) as conn:
        _store(conn, mesh, [])
        topology = web_data.topology(conn)

    order = [row["node"].device_name for row in topology["rows"] if not row["is_master"]]
    assert order == [
        "alpha",
        "Able",
        "zeta",
        "yankee",
        "zulu",
        "able-child",
        "zeta-child",
        "bravo",
    ]
    assert [row["depth"] for row in topology["rows"]] == [0, 1, 2, 2, 1, 1, 2, 2, 1]


def test_http_status_latches_until_disproven(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    with db.connect(settings.db_path) as conn:
        db.record_http_scan(conn, {"192.0.2.50": True}, fail_threshold=2)
        assert db.load_http_open_ips(conn) == {"192.0.2.50"}

        db.record_http_scan(conn, {}, fail_threshold=2)
        assert db.load_http_open_ips(conn) == {"192.0.2.50"}

        db.record_http_scan(conn, {"192.0.2.50": False}, fail_threshold=2)
        assert db.load_http_open_ips(conn) == {"192.0.2.50"}

        db.record_http_scan(conn, {"192.0.2.50": False}, fail_threshold=2)
        assert db.load_http_open_ips(conn) == set()

        db.record_http_scan(conn, {"192.0.2.50": True}, fail_threshold=2)
        assert db.load_http_open_ips(conn) == {"192.0.2.50"}
        assert db.counts(conn)["http_open"] >= 1


def test_http_status_web_annotations(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    mesh = _mesh()
    with db.connect(settings.db_path) as conn:
        _store(conn, mesh, [_host("printer", "192.0.2.50")])
        db.replace_dns_records(
            conn, build_dns_records(db.load_hosts(conn), [], settings.dns_domain)
        )
        db.record_http_scan(conn, {"192.0.2.50": True, "192.0.2.10": True})

        rows = web_data.hosts(conn)
        printer = next(row for row in rows if row["ip"] == "192.0.2.50")
        assert printer["http_open"] is True
        assert printer["http_url"] == "http://192.0.2.50/"

        topology = web_data.topology(conn)
        assert "192.0.2.10" in topology["http_open_ips"]

        dns = web_data.dns(conn, settings)
        assert "192.0.2.50" in dns["http_open_ips"]


def test_prune_old_snapshots(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    with db.connect(settings.db_path) as conn:
        snapshot_id = _store(conn, _mesh(), [])
        conn.execute(
            "UPDATE snapshots SET started_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (snapshot_id,),
        )
        conn.commit()
        removed = db.prune_snapshots(conn, 1)
        assert removed == 1
        assert db.latest_snapshot(conn) is None
