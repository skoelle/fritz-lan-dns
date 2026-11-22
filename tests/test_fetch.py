from __future__ import annotations

from pathlib import Path

from fritz_lan_dns import db
from fritz_lan_dns.fetch import run_fetch
from fritz_lan_dns.fritz.client import HostFetch
from tests.helpers import make_settings

FIXTURE = Path(__file__).parent / "fixtures" / "mesh.json"


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
    assert "rewrite name regex" in settings.corefile.read_text(encoding="utf-8")

    with db.connect(settings.db_path) as conn:
        latest = db.latest_snapshot(conn)
        assert latest is not None
        assert latest["status"] == "ok"
        assert latest["zone_serial"] == 1


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
