from __future__ import annotations

from pathlib import Path

from fritz_lan_dns import db
from fritz_lan_dns.fetch import run_fetch
from fritz_lan_dns.fritz.client import FritzBoxError, HostFetch
from fritz_lan_dns.openwrt import OpenWrtNotFound
from tests.helpers import make_settings

FIXTURE = Path(__file__).parent / "fixtures" / "mesh.json"
LOG_FIXTURE = Path(__file__).parent / "fixtures" / "device-log.xml"


class FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_security_port(self) -> int:
        return 49443

    def get_mesh_list_path(self) -> str:
        return "/meshlist.lua?sid=test"

    def fetch_mesh_text(self, path: str, security_port: int) -> str:
        return FIXTURE.read_text(encoding="utf-8")

    def get_hosts(self) -> HostFetch:
        return HostFetch(
            entries=[
                {
                    "NewHostName": "router",
                    "NewIPAddress": "192.0.2.1",
                    "NewMACAddress": "AA:BB:CC:00:00:01",
                    "NewActive": "1",
                    "NewInterfaceType": "LAN",
                },
                {
                    "NewHostName": "printer",
                    "NewIPAddress": "192.0.2.50",
                    "NewMACAddress": "AA:BB:CC:00:00:02",
                    "NewActive": "1",
                    "NewInterfaceType": "LAN",
                },
            ],
            raw_xml="{}",
            errors=[],
        )

    def get_device_info(self) -> dict[str, str]:
        return {"NewModelName": "FRITZ!Repeater 2400"}

    def get_wlan_info(self, index: int) -> dict[str, str]:
        if index != 1:
            raise FritzBoxError("kein Dienst")
        return {
            "NewSSID": "example",
            "NewChannel": "36",
            "NewX_AVM-DE_FrequencyBand": "5000",
            "NewStandard": "ax",
            "NewBeaconType": "11iandWPA3",
        }

    def get_wlan_associations(self, index: int) -> list[dict[str, str]]:
        if index != 1:
            return []
        return [
            {
                "NewAssociatedDeviceMACAddress": "AA:BB:CC:00:00:02",
                "NewAssociatedDeviceIPAddress": "192.0.2.50",
                "NewX_AVM-DE_Speed": "866",
                "NewX_AVM-DE_SignalStrength": "70",
            }
        ]


class FakeOpenWrt:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_dhcp_leases(self) -> dict:
        return {
            "dhcp_leases": [
                {
                    "macaddr": "AA:BB:CC:00:00:10",
                    "ipaddr": "192.0.2.150",
                    "hostname": "owrt-nas",
                    "expires": 100,
                },
                {
                    "macaddr": "AA:BB:CC:00:00:02",
                    "ipaddr": "192.0.2.152",
                    "hostname": "printer",
                    "expires": 100,
                },
            ],
            "dhcp6_leases": [],
        }

    def get_host_hints(self) -> dict:
        return {
            "AA:BB:CC:00:00:11": {
                "name": "owrt-printer",
                "ipaddrs": ["192.0.2.151"],
            }
        }

    def get_network_interfaces(self) -> dict:
        return {
            "interface": [
                {"interface": "wan", "device": "eth0", "l3_device": "eth0"},
                {"interface": "wwan", "device": "phy1-sta0", "l3_device": "phy1-sta0"},
            ]
        }

    def get_wireless_info(self, device: str) -> dict:
        if device != "phy1-sta0":
            raise OpenWrtNotFound("kein WLAN")
        return {
            "device": device,
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

    def get_mwan3_status(self) -> dict:
        return {
            "interfaces": {
                "wan": {"status": "online", "enabled": True, "running": True, "online": 3600, "uptime": 7200, "score": 10, "lost": 0, "track_ip": []},
                "wwan": {"status": "offline", "enabled": True, "running": False, "online": 0, "uptime": 0, "score": 0, "lost": 2, "track_ip": []},
            },
            "policies": {
                "ipv4": {"default": [{"interface": "wan", "percent": 100}]},
                "ipv6": {"default": []},
            },
            "connected": {"ipv4": ["192.0.2.0/24"], "ipv6": []},
        }


def _openwrt_settings(tmp_path: Path):
    return make_settings(
        tmp_path,
        openwrt_enabled=True,
        openwrt_url="http://192.0.2.3",
        openwrt_user="root",
        openwrt_password="secret",
    )


def test_run_fetch_includes_openwrt_hosts(tmp_path: Path, monkeypatch) -> None:
    settings = _openwrt_settings(tmp_path)
    settings.device_names_file.write_text("owrt-nas OpenWrt NAS\n", encoding="utf-8")
    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FakeClient)
    monkeypatch.setattr("fritz_lan_dns.fetch.OpenWrtClient", FakeOpenWrt)

    result = run_fetch(settings)

    assert result["openwrt_hosts"] == 3
    hosts_text = settings.hosts_file.read_text(encoding="utf-8")
    assert "owrt-nas.example.test" in hosts_text
    assert "owrt-printer.example.test" in hosts_text
    assert "192.0.2.50 printer.example.test" in hosts_text
    assert "192.0.2.152 printer.example.test" in hosts_text
    with db.connect(settings.db_path) as conn:
        loaded_hosts = db.load_hosts(conn)
        hosts = {host.hostname: host for host in loaded_hosts}
        assert hosts["owrt-nas"].source == "openwrt"
        assert hosts["owrt-nas"].description == "OpenWrt NAS"
        printers = [host for host in loaded_hosts if host.hostname == "printer"]
        assert {host.source for host in printers} == {"fritz", "openwrt"}
        assert {host.ip for host in printers} == {"192.0.2.50", "192.0.2.152"}

        printer_records = [
            record
            for record in db.load_dns_records(conn)
            if record.hostname == "printer"
        ]
        assert {record.ip for record in printer_records} == {
            "192.0.2.50",
            "192.0.2.152",
        }
        assert {record.status for record in printer_records} == {"active"}
        records = {record.hostname: record for record in db.load_dns_records(conn)}
        assert records["owrt-nas"].source == "openwrt"
        assert records["owrt-printer"].status == "stale"

        assert result["mwan3_interfaces"] == 2
        mwan3 = db.latest_mwan3(conn)
        assert mwan3 is not None
        assert mwan3["started_at"]
        assert set(mwan3["data"]["interfaces"]) == {"wan", "wwan"}
        assert mwan3["data"]["wireless"]["wwan"]["ssid"] == "TestWLAN"
        assert mwan3["data"]["wireless"]["wwan"]["signal"] == -42
        assert "wan" not in mwan3["data"]["wireless"]


def test_run_fetch_mwan3_not_available_is_ignored(tmp_path: Path, monkeypatch) -> None:
    settings = _openwrt_settings(tmp_path)
    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FakeClient)

    class NoMwan3OpenWrt(FakeOpenWrt):
        def get_mwan3_status(self) -> dict:
            raise OpenWrtNotFound("mwan3 nicht installiert")

    monkeypatch.setattr("fritz_lan_dns.fetch.OpenWrtClient", NoMwan3OpenWrt)

    result = run_fetch(settings)

    assert result["mwan3_interfaces"] == 0
    assert result["warning"] is None
    with db.connect(settings.db_path) as conn:
        assert db.latest_mwan3(conn) is None


def test_run_fetch_mwan3_failure_is_warning(tmp_path: Path, monkeypatch) -> None:
    settings = _openwrt_settings(tmp_path)
    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FakeClient)

    class FailingMwan3OpenWrt(FakeOpenWrt):
        def get_mwan3_status(self) -> dict:
            raise RuntimeError("mwan3 kaputt")

    monkeypatch.setattr("fritz_lan_dns.fetch.OpenWrtClient", FailingMwan3OpenWrt)

    result = run_fetch(settings)

    assert result["mwan3_interfaces"] == 0
    assert "MWAN3" in (result["warning"] or "")
    with db.connect(settings.db_path) as conn:
        latest = db.latest_snapshot(conn)
        assert latest is not None
        assert latest["status"] == "ok"


def test_run_fetch_openwrt_failure_is_warning(tmp_path: Path, monkeypatch) -> None:
    settings = _openwrt_settings(tmp_path)
    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FakeClient)

    class FailingOpenWrt:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("router down")

    monkeypatch.setattr("fritz_lan_dns.fetch.OpenWrtClient", FailingOpenWrt)

    result = run_fetch(settings)

    assert result["openwrt_hosts"] == 0
    assert "OpenWrt" in (result["warning"] or "")
    assert "router.example.test" in settings.hosts_file.read_text(encoding="utf-8")
    with db.connect(settings.db_path) as conn:
        latest = db.latest_snapshot(conn)
        assert latest is not None
        assert latest["status"] == "ok"


def test_run_fetch_includes_extra_repeater_wlan(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(
        tmp_path,
        fritz_repeater_ips="192.0.2.60",
        fritz_repeater_user="repeater-user",
        fritz_repeater_pw="repeater-password",
    )
    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FakeClient)

    result = run_fetch(settings)

    assert result["wlan_interfaces"] == 5
    assert result["wlan_clients"] == 2
    with db.connect(settings.db_path) as conn:
        entry = db.latest_wlan(conn)
        assert entry is not None
        interfaces = entry["data"]["interfaces"]
        assert any(item["source"] == "fritz-repeater" for item in interfaces)
        assert len(entry["data"]["clients"]) == 2


def test_run_fetch_repeater_wlan_failure_is_warning(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, fritz_repeater_ips="192.0.2.60")

    class FailingRepeaterClient(FakeClient):
        def get_wlan_info(self, index: int) -> dict[str, str]:
            raise RuntimeError("repeater nicht erreichbar")

    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FailingRepeaterClient)

    result = run_fetch(settings)

    assert "FRITZ-Repeater 192.0.2.60" in (result["warning"] or "")
    assert result["wlan_interfaces"] == 4
    with db.connect(settings.db_path) as conn:
        latest = db.latest_snapshot(conn)
        assert latest is not None
        assert latest["status"] == "ok"
        entry = db.latest_wlan(conn)
        assert entry is not None
        assert all(
            item["source"] != "fritz-repeater"
            for item in entry["data"]["interfaces"]
        )


def test_run_fetch_end_to_end(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FakeClient)
    legacy = tmp_path / "legacy.json"
    result = run_fetch(settings, legacy_json=legacy)

    assert result["snapshot_id"] == 1
    assert result["nodes"] == 5
    assert result["online"] == 4
    assert result["dns_records"] >= 2
    assert legacy.exists()
    assert "printer.example.test" in settings.hosts_file.read_text(encoding="utf-8")
    assert "router.example.test" in settings.hosts_file.read_text(encoding="utf-8")
    assert "answer name" in settings.corefile.read_text(encoding="utf-8")

    with db.connect(settings.db_path) as conn:
        latest = db.latest_snapshot(conn)
        assert latest is not None
        assert latest["status"] == "ok"
        assert latest["zone_serial"] == 1


def test_run_fetch_scans_only_active_hosts(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FakeClient)
    scanned: list[set[str]] = []

    def fake_scan(ips, **kwargs):
        scanned.append(set(ips))
        return {ip: True for ip in ips}

    monkeypatch.setattr("fritz_lan_dns.fetch.scan_ports", fake_scan)
    result = run_fetch(settings)

    assert len(scanned) == 1
    assert "192.0.2.50" in scanned[0]
    assert "192.0.2.40" not in scanned[0]
    assert result["http_open"] == len(scanned[0])
    with db.connect(settings.db_path) as conn:
        open_ips = db.load_http_open_ips(conn)
    assert "192.0.2.50" in open_ips
    assert "192.0.2.40" not in open_ips


def test_run_fetch_disabled_scan_keeps_state(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, http_scan_enabled=False)
    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FakeClient)

    def fail_scan(*args, **kwargs):
        raise AssertionError("Scan darf nicht laufen")

    monkeypatch.setattr("fritz_lan_dns.fetch.scan_ports", fail_scan)
    result = run_fetch(settings)
    assert result["http_open"] == 0


class EventLogClient(FakeClient):
    def get_device_log_path(self) -> str:
        return "/devicelog.lua?sid=test"

    def fetch_device_log_text(self, path: str, security_port: int) -> str:
        return LOG_FIXTURE.read_text(encoding="utf-8")


def test_run_fetch_stores_wlan_events(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(
        tmp_path,
        fritz_eventlog_enabled=True,
        event_retention_days=3650,
        http_scan_enabled=False,
    )
    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", EventLogClient)

    result = run_fetch(settings)

    assert result["wlan_events"] == 8
    with db.connect(settings.db_path) as conn:
        events = db.load_wlan_events(conn, days=3650, include_info=True)
        by_id = {event["event_id"]: event for event in events}
        assert set(by_id) == {674, 719, 783, 787, 791, 794, 796}
        radar = by_id[796]
        assert radar["entries"] == 2
        assert radar["occurrences"] == 1
        assert radar["channel"] == "124"
        cac = by_id[794]
        assert cac["entries"] == 1
        assert cac["occurrences"] == 2
        assert cac["first_seen"] == "2026-11-26 16:42:58"

        visible = db.load_wlan_events(conn, days=3650)
        assert all(event["event_id"] not in (674, 719) for event in visible)

        summary = db.load_wlan_event_counts(conn, hours=0)
        summary_by_id = {row["event_id"]: row for row in summary}
        assert summary_by_id[796]["entries"] == 2


def test_run_fetch_eventlog_failure_is_warning(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(
        tmp_path,
        fritz_eventlog_enabled=True,
        http_scan_enabled=False,
    )

    class FailingEventLogClient(FakeClient):
        def get_device_log_path(self) -> str:
            raise FritzBoxError("kein Ereignisprotokoll")

    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FailingEventLogClient)

    result = run_fetch(settings)

    assert result["wlan_events"] == 0
    assert "Ereignisprotokoll" in (result["warning"] or "")
    with db.connect(settings.db_path) as conn:
        latest = db.latest_snapshot(conn)
        assert latest is not None
        assert latest["status"] == "ok"


def test_run_fetch_failure_keeps_old_dns_files(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FakeClient)
    run_fetch(settings)
    old_hosts = settings.hosts_file.read_text(encoding="utf-8")

    class FailingClient(FakeClient):
        def get_security_port(self) -> int:
            raise RuntimeError("FRITZ!Box nicht erreichbar")

    monkeypatch.setattr("fritz_lan_dns.fetch.FritzClient", FailingClient)
    try:
        run_fetch(settings)
    except RuntimeError:
        pass
    else:
        raise AssertionError("run_fetch hätte fehlschlagen müssen")

    assert settings.hosts_file.read_text(encoding="utf-8") == old_hosts
    with db.connect(settings.db_path) as conn:
        latest = db.latest_snapshot(conn)
        assert latest is not None
        assert latest["status"] == "failed"
        assert "nicht erreichbar" in (latest["error"] or "")
