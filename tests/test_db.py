from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from fritz_lan_dns import db
from fritz_lan_dns.dns import build_dns_records, ensure_dns_files, write_dns_files
from fritz_lan_dns.fritz.mesh import parse_mesh
from fritz_lan_dns.fritz.wlan import parse_wlan
from fritz_lan_dns.models import HostRecord, MeshNode, MeshSnapshot, WlanEvent
from fritz_lan_dns.util import iso_now
from fritz_lan_dns.web import data as web_data
from tests.helpers import make_settings

FIXTURE = Path(__file__).parent / "fixtures" / "mesh.json"


def _settings(tmp_path: Path, **overrides):
    return make_settings(tmp_path, **overrides)


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

        topology = web_data.topology(conn, settings)
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


def test_hosts_keep_both_ips_for_shared_mac(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    mac = "aa:bb:cc:99:00:10"
    fritz_host = HostRecord(
        key=f"mac:{mac}",
        hostname="balkonpi",
        ip="192.0.2.54",
        mac=mac,
        active=True,
        source="fritz",
        last_seen=iso_now(),
    )
    openwrt_host = HostRecord(
        key=f"openwrt:mac:{mac}",
        hostname="balkonpi",
        ip="192.0.2.154",
        mac=mac,
        active=True,
        source="openwrt",
        last_seen=iso_now(),
    )
    with db.connect(settings.db_path) as conn:
        _store(conn, _mesh(), [fritz_host, openwrt_host])
        loaded = [host for host in db.load_hosts(conn) if host.mac == mac]
        assert {host.source for host in loaded} == {"fritz", "openwrt"}
        assert {host.ip for host in loaded} == {"192.0.2.54", "192.0.2.154"}

        records = build_dns_records(db.load_hosts(conn), [], settings.dns_domain)
        db.replace_dns_records(conn, records)
        balkon = [
            record
            for record in db.load_dns_records(conn)
            if record.hostname == "balkonpi"
        ]
        assert {record.ip for record in balkon} == {"192.0.2.54", "192.0.2.154"}
        assert all(record.writable for record in balkon)
        assert all(record.conflict is None for record in balkon)


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


def test_topology_attaches_openwrt_hosts(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        openwrt_enabled=True,
        openwrt_topology_parent_ip="192.0.2.80",
    )
    db.init_schema(settings.db_path)
    nodes = [
        _node("master", "router", "192.0.2.1", role="master"),
        _node("parent", "OpenWrt", "192.0.2.80", parent="master"),
    ]
    mesh = MeshSnapshot(nodes=nodes, links=[], master_uid="master", raw={})
    active = HostRecord(
        key="mac:ow-active",
        hostname="ow-active",
        ip="192.0.2.150",
        mac="aa:bb:cc:99:00:01",
        active=True,
        source="openwrt",
        description="OpenWrt NAS",
    )
    inactive = HostRecord(
        key="mac:ow-offline",
        hostname="ow-offline",
        ip="192.0.2.151",
        mac="aa:bb:cc:99:00:02",
        active=False,
        source="openwrt",
    )
    with db.connect(settings.db_path) as conn:
        _store(conn, mesh, [active, inactive])
        result = web_data.topology(conn, settings)

    rows = {row["node"].device_name: row for row in result["rows"]}
    parent_row = rows["OpenWrt"]
    child_row = rows["ow-active"]
    assert child_row["node"].parent_uid == "parent"
    assert child_row["depth"] == parent_row["depth"] + 1
    assert child_row["source"] == "openwrt"
    assert child_row["node"].description == "OpenWrt NAS"
    assert "ow-offline" not in rows

    offline_names = [row["node"].device_name for row in result["offline_rows"]]
    assert "ow-offline" in offline_names
    assert "ow-active" not in offline_names
    off_row = next(
        row for row in result["offline_rows"] if row["node"].device_name == "ow-offline"
    )
    assert off_row["depth"] == 0
    assert off_row["source"] == "openwrt"

    assert result["summary"]["total"] == 2
    assert result["summary"]["offline"] == 0
    assert result["openwrt_note"] == ""


def test_topology_openwrt_parent_missing(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        openwrt_enabled=True,
        openwrt_topology_parent_ip="192.0.2.99",
    )
    db.init_schema(settings.db_path)
    mesh = MeshSnapshot(
        nodes=[_node("master", "router", "192.0.2.1", role="master")],
        links=[],
        master_uid="master",
        raw={},
    )
    active = HostRecord(
        key="mac:ow-active",
        hostname="ow-active",
        ip="192.0.2.150",
        mac="aa:bb:cc:99:00:01",
        active=True,
        source="openwrt",
    )
    inactive = HostRecord(
        key="mac:ow-offline",
        hostname="ow-offline",
        ip="192.0.2.151",
        mac="aa:bb:cc:99:00:02",
        active=False,
        source="openwrt",
    )
    with db.connect(settings.db_path) as conn:
        _store(conn, mesh, [active, inactive])
        result = web_data.topology(conn, settings)

    assert "192.0.2.99" in result["openwrt_note"]
    assert all(row["source"] != "openwrt" for row in result["rows"])
    offline_names = [row["node"].device_name for row in result["offline_rows"]]
    assert offline_names == ["ow-offline"]


def test_topology_openwrt_disabled_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path, openwrt_topology_parent_ip="192.0.2.80")
    db.init_schema(settings.db_path)
    mesh = MeshSnapshot(
        nodes=[
            _node("master", "router", "192.0.2.1", role="master"),
            _node("parent", "OpenWrt", "192.0.2.80", parent="master"),
        ],
        links=[],
        master_uid="master",
        raw={},
    )
    active = HostRecord(
        key="mac:ow-active",
        hostname="ow-active",
        ip="192.0.2.150",
        mac="aa:bb:cc:99:00:01",
        active=True,
        source="openwrt",
    )
    with db.connect(settings.db_path) as conn:
        _store(conn, mesh, [active])
        result = web_data.topology(conn, settings)

    assert result["openwrt_note"] == ""
    assert all(row["source"] != "openwrt" for row in result["rows"])


def test_latest_mwan3_roundtrip_and_view(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    payload = {
        "interfaces": {
            "wan": {
                "status": "online",
                "enabled": True,
                "running": True,
                "online": 3600,
                "uptime": 7200,
                "score": 10,
                "lost": 0,
                "track_ip": [{"ip": "1.1.1.1", "status": "up"}],
            }
        },
        "policies": {"ipv4": {"default": [{"interface": "wan", "percent": 100}]}},
        "connected": {"ipv4": ["192.0.2.0/24"], "ipv6": []},
    }
    with db.connect(settings.db_path) as conn:
        snapshot_id = db.start_snapshot(conn, "2026-11-22T20:00:00+00:00")
        db.store_snapshot_mwan3(conn, snapshot_id, json.dumps(payload))

        entry = db.latest_mwan3(conn)
        assert entry is not None
        assert entry["started_at"] == "2026-11-22T20:00:00+00:00"
        assert entry["data"] == payload

        view = web_data.mwan3_view(entry)
        assert view is not None
        assert view["interfaces"][0]["name"] == "wan"
        assert view["interfaces"][0]["status_class"] == "ok"
        assert view["interfaces"][0]["online"] == "1h 0m"
        assert view["interfaces"][0]["track_up"] == 1
        assert view["policies"][0]["members"][0]["interface"] == "wan"
        assert view["connected_v4"] == 1


def test_mwan3_view_returns_none_without_data() -> None:
    assert web_data.mwan3_view(None) is None
    assert web_data.mwan3_view({"started_at": "x", "data": {}}) is not None


def test_mwan3_view_formats_wireless() -> None:
    entry = {
        "started_at": "2026-11-22T20:00:00+00:00",
        "data": {
            "interfaces": {
                "wwan": {
                    "status": "online",
                    "online": 3600,
                    "uptime": 3600,
                    "score": 10,
                    "lost": 0,
                    "track_ip": [],
                }
            },
            "policies": {},
            "connected": {},
            "wireless": {
                "wwan": {
                    "device": "phy1-sta0",
                    "mode": "Client",
                    "ssid": "TestWLAN",
                    "signal": -42,
                    "noise": -92,
                    "quality": 68,
                    "quality_max": 70,
                    "bitrate": 360000,
                    "frequency": 5540,
                    "channel": 108,
                }
            },
        },
    }

    view = web_data.mwan3_view(entry)
    wifi = view["interfaces"][0]["wifi"]
    assert wifi["ssid"] == "TestWLAN"
    assert wifi["mode"] == "Client"
    assert wifi["device"] == "phy1-sta0"
    assert wifi["signal"] == -42
    assert wifi["snr"] == 50
    assert wifi["quality_pct"] == 97
    assert wifi["rate_mbit"] == 360
    assert wifi["band"] == "5 GHz"
    assert wifi["channel"] == 108
    assert wifi["signal_class"] == "ok"
    assert web_data._wifi_band(2437) == "2.4 GHz"
    assert web_data._wifi_band(6115) == "6 GHz"


def test_init_schema_migrates_mwan3_json(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.execute(
        """
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    db.init_schema(settings.db_path)

    with db.connect(settings.db_path) as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")
        }
    assert "mwan3_json" in columns


def test_latest_wlan_roundtrip_and_view(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mesh = parse_mesh(raw)
    wlan = parse_wlan(raw, {node.uid: node for node in mesh.nodes})
    payload = {
        "interfaces": [asdict(item) for item in wlan.interfaces],
        "clients": [asdict(item) for item in wlan.clients],
    }
    with db.connect(settings.db_path) as conn:
        snapshot_id = db.start_snapshot(conn, "2026-11-22T20:00:00+00:00")
        db.store_snapshot_wlan(conn, snapshot_id, json.dumps(payload))

        entry = db.latest_wlan(conn)
        assert entry is not None
        assert entry["started_at"] == "2026-11-22T20:00:00+00:00"
        assert entry["data"] == payload

        view = web_data.wlan_view(entry, {"192.0.2.20"})
        assert view is not None
        assert view["summary"] == {
            "devices": 2,
            "online_devices": 2,
            "ap_radios": 3,
            "uplinks": 1,
            "clients": 1,
            "guest_radios": 1,
            "bands": {"2.4 GHz": 1, "5 GHz": 2, "6 GHz": 0},
            "primary_5g": {
                "device_name": "router",
                "channel": 36,
                "channel_width": "80 MHz",
                "utilization_label": "12 %",
                "utilization_class": "low",
            },
        }
        devices = {device["name"]: device for device in view["devices"]}
        assert devices["router"]["ap_count"] == 2
        assert devices["router"]["guest_count"] == 1
        master_5g = next(
            item
            for item in devices["router"]["interfaces"]
            if item["interface_name"] == "AP:5G:0"
        )
        assert master_5g["client_count"] == 1
        assert view["clients"][0]["signal_label"] == "-58 dBm"
        assert view["clients"][0]["signal_class"] == "good"
        assert view["clients"][0]["http_open"] is True
        assert view["clients"][0]["http_url"] == "http://192.0.2.20/"


def test_wlan_devices_sorted_by_ip() -> None:
    def interface(
        name: str, uid: str, ip: str | None, *, source: str = "mesh"
    ) -> dict:
        return {
            "node_uid": uid,
            "device_name": name,
            "device_model": "",
            "device_ip": ip,
            "online": True,
            "source": source,
            "description": "",
            "interface_name": "AP:5G:0",
            "ssid": "example",
            "band": "5 GHz",
            "channel": 36,
            "channel_width": "80 MHz",
            "channel_utilization": 10,
            "security": "WPA2PSK",
            "opmode": "AP",
            "phymodes": "ac",
            "streams_tx": 2,
            "streams_rx": 2,
            "steering_enabled": True,
            "is_guest": False,
            "is_uplink": False,
            "client_count": 0,
        }

    entry = {
        "started_at": "2026-11-22T20:00:00+00:00",
        "data": {
            "interfaces": [
                interface("zeta", "device-20", "192.0.2.20"),
                interface(
                    "omega",
                    "device-5",
                    "192.0.2.5",
                    source="fritz-repeater",
                ),
                interface("alpha", "device-10", "192.0.2.10"),
                interface("no-ip", "device-0", None),
            ],
            "clients": [],
        },
    }

    view = web_data.wlan_view(entry)
    assert view is not None
    assert [device["name"] for device in view["devices"]] == [
        "omega",
        "alpha",
        "zeta",
        "no-ip",
    ]


def test_init_schema_migrates_wlan_json(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.execute(
        """
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    db.init_schema(settings.db_path)

    with db.connect(settings.db_path) as conn:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")
        }
    assert "wlan_json" in columns


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
        topology = web_data.topology(conn, settings)

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

        topology = web_data.topology(conn, settings)
        assert "192.0.2.10" in topology["http_open_ips"]

        dns = web_data.dns(conn, settings)
        assert "192.0.2.50" in dns["http_open_ips"]


def _wlan_event(
    event_id: int,
    event_at: str,
    *,
    severity: str,
    message: str,
    node: str = "",
    channel: str = "",
    occurrences: int = 1,
) -> WlanEvent:
    return WlanEvent(
        event_id=event_id,
        group="wlan",
        severity=severity,
        node=node,
        channel=channel,
        message=message,
        event_at=event_at,
        first_seen=event_at,
        occurrences=occurrences,
        fingerprint=f"{event_id}|{node}|{channel}|{message}",
    )


def test_wlan_events_roundtrip_filter_and_prune(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    two_days = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
    radar_message = "Radar wurde auf Kanal 124 erkannt."
    events = [
        _wlan_event(
            796,
            now,
            severity="critical",
            message=radar_message,
            channel="124",
        ),
        _wlan_event(
            796,
            two_days,
            severity="critical",
            message=radar_message,
            channel="124",
        ),
        _wlan_event(
            794,
            now,
            severity="warning",
            message="DFS-Prüfung auf Kanal 100-128.",
            node="fritzmesh-TriBand",
            channel="100-128",
            occurrences=5,
        ),
        _wlan_event(
            674,
            now,
            severity="info",
            message="2,4-GHz-Bandbreite reduziert.",
            node="fritzmesh3OG-imac",
        ),
        _wlan_event(
            791,
            old,
            severity="warning",
            message="Störquelle auf Kanal 108.",
            channel="108",
        ),
    ]

    with db.connect(settings.db_path) as conn:
        assert db.upsert_wlan_events(conn, events) == 5
        assert db.upsert_wlan_events(conn, events[:1]) == 1

        all_events = db.load_wlan_events(conn, days=365, include_info=True)
        by_id = {event["event_id"]: event for event in all_events}
        assert set(by_id) == {674, 791, 794, 796}
        assert by_id[796]["entries"] == 2
        assert by_id[796]["occurrences"] == 1
        assert by_id[794]["occurrences"] == 5

        visible = db.load_wlan_events(conn, days=365)
        assert {event["event_id"] for event in visible} == {791, 794, 796}

        recent = db.load_wlan_events(conn, days=7)
        assert {event["event_id"] for event in recent} == {794, 796}

        counts = {
            row["event_id"]: row
            for row in db.load_wlan_event_counts(conn, hours=0)
        }
        assert counts[796]["entries"] == 2
        assert counts[794]["occurrences"] == 5

        latest = db.latest_wlan_event(conn, 796)
        assert latest is not None
        assert latest["event_at"] == now

        assert db.prune_wlan_events(conn, 90) == 1
        remaining = db.load_wlan_events(conn, days=365, include_info=True)
        assert 791 not in {event["event_id"] for event in remaining}


def test_web_data_wlan_events(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    events = [
        _wlan_event(
            796,
            now,
            severity="critical",
            message="Radar wurde auf Kanal 124 erkannt.",
            channel="124",
        ),
        _wlan_event(
            674,
            now,
            severity="info",
            message="2,4-GHz-Bandbreite reduziert.",
        ),
    ]

    with db.connect(settings.db_path) as conn:
        db.upsert_wlan_events(conn, events)
        wlan = web_data.wlan(conn, days=7)
        assert wlan["events"][0]["label"] == "Radar erkannt, Kanalwechsel"
        assert wlan["events"][0]["severity_label"] == "Kritisch"
        assert wlan["event_summary"]["radar"] == 1
        assert all(event["event_id"] != 674 for event in wlan["events"])

        all_events = web_data.wlan(conn, days=7, include_info=True)
        assert any(event["event_id"] == 674 for event in all_events["events"])

        dashboard = web_data.dashboard(conn, settings)
        assert dashboard["wlan_event_summary"]["radar"] == 1
        assert dashboard["wlan_events"][0]["event_id"] == 796


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


def test_ip_mode_roundtrip_filter_and_dashboard(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db.init_schema(settings.db_path)
    hosts = [
        HostRecord(
            key="mac:aa:00:00:00:01",
            hostname="static-host",
            ip="192.0.2.201",
            mac="aa:00:00:00:01",
            active=True,
            source="fritz",
            address_source="static",
        ),
        HostRecord(
            key="mac:aa:00:00:00:02",
            hostname="dhcp-host",
            ip="192.0.2.202",
            mac="aa:00:00:00:02",
            active=True,
            source="fritz",
            address_source="dhcp",
            static_dhcp=False,
        ),
        HostRecord(
            key="mac:aa:00:00:00:03",
            hostname="reserved-host",
            ip="192.0.2.203",
            mac="aa:00:00:00:03",
            active=True,
            source="fritz",
            address_source="dhcp",
            static_dhcp=True,
        ),
    ]
    with db.connect(settings.db_path) as conn:
        _store(conn, _mesh(), hosts)
        loaded = {host.hostname: host for host in db.load_hosts(conn)}
        assert loaded["static-host"].address_source == "static"
        assert loaded["static-host"].static_dhcp is None
        assert loaded["dhcp-host"].static_dhcp is False
        assert loaded["reserved-host"].static_dhcp is True

        rows = web_data.hosts(conn, ip_mode="reserved")
        assert len(rows) == 1
        assert rows[0]["hostname"] == "reserved-host"
        assert rows[0]["ip_mode"]["label"] == "📌 DHCP"

        dashboard = web_data.dashboard(conn, settings)
        assert dashboard["ip_modes"] == {
            "static": 1,
            "dhcp": 1,
            "reserved": 1,
            "unknown": 0,
        }
