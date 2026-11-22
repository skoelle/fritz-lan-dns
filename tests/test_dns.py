from __future__ import annotations

from dataclasses import replace

from tests.helpers import make_settings
from fritz_lan_dns.dns.records import build_dns_records
from fritz_lan_dns.dns.writer import corefile_content, hosts_file_content, zone_content
from fritz_lan_dns.models import HostRecord
from fritz_lan_dns.util import sanitize_hostname


def _host(hostname: str, ip: str, *, active: bool = True, mac: str = "", source: str = "fritz") -> HostRecord:
    return HostRecord(
        key=f"mac:{mac or hostname}",
        hostname=hostname,
        ip=ip,
        mac=mac or None,
        active=active,
        source=source,
        last_seen="2026-11-22T20:00:00Z" if active else "2026-11-01T10:00:00Z",
    )


def test_sanitize_hostname() -> None:
    assert sanitize_hostname("My Printer.fritz.box", "example.test") == "my-printer"
    assert sanitize_hostname("bad_name", "example.test") == "bad-name"
    assert sanitize_hostname("xxx.example.test", "example.test") == "xxx"
    assert sanitize_hostname("", "example.test") is None
    assert sanitize_hostname("localhost", "example.test") is None


def test_build_records_active_stale_and_conflicts() -> None:
    records = build_dns_records(
        [
            _host("printer", "192.0.2.50", active=True, mac="aa:01"),
            _host("old-tablet", "192.0.2.51", active=False, mac="aa:02"),
            _host("active-one", "192.0.2.60", active=True, mac="aa:03"),
            _host("active-two", "192.0.2.60", active=True, mac="aa:04"),
        ],
        [],
        "example.test",
    )
    by_fqdn = {record.fqdn: record for record in records}
    assert by_fqdn["printer.example.test"].status == "active"
    assert by_fqdn["printer.example.test"].source == "fritz"
    assert by_fqdn["old-tablet.example.test"].status == "stale"
    conflict = [record for record in records if record.status == "conflict"]
    assert len(conflict) == 1
    assert conflict[0].ip == "192.0.2.60"
    assert conflict[0].source == "fritz"
    assert "192.0.2.60" in (conflict[0].conflict or "")


def test_static_record_wins() -> None:
    records = build_dns_records(
        [_host("printer", "192.0.2.50")],
        [{"hostname": "printer", "ip": "192.0.2.99", "comment": "manual"}],
        "example.test",
    )
    assert len(records) == 1
    assert records[0].ip == "192.0.2.99"
    assert records[0].status == "static"
    assert records[0].source == "static"


def test_openwrt_source_is_kept() -> None:
    records = build_dns_records(
        [_host("camera", "192.0.2.160", source="openwrt")],
        [],
        "example.test",
    )
    assert len(records) == 1
    assert records[0].source == "openwrt"
    assert records[0].status == "active"


def test_ipv6_is_optional() -> None:
    hosts = [_host("v6host", "fd00::10")]
    assert build_dns_records(hosts, [], "example.test") == []
    records = build_dns_records(hosts, [], "example.test", include_ipv6=True)
    assert len(records) == 1
    assert records[0].ip == "fd00::10"


def test_ipv4_and_ipv6_can_coexist() -> None:
    records = build_dns_records(
        [_host("dual", "192.0.2.70"), _host("dual", "fd00::70")],
        [],
        "example.test",
        include_ipv6=True,
    )
    assert {record.ip for record in records} == {"192.0.2.70", "fd00::70"}
    assert {record.status for record in records} == {"active"}


def test_hosts_and_zone_content() -> None:
    records = build_dns_records([_host("printer", "192.0.2.50")], [], "example.test")
    hosts_text = hosts_file_content(records)
    assert hosts_text == "192.0.2.50 printer.example.test\n"
    zone = zone_content(records, "example.test", 60, 7, "192.0.2.2")
    assert "$ORIGIN example.test." in zone
    assert "printer IN A 192.0.2.50" in zone
    assert "    7" in zone
    assert "ns1 IN A 192.0.2.2" in zone


def test_corefile_fallback_and_disabled(tmp_path) -> None:
    settings = make_settings(tmp_path)
    enabled = corefile_content(settings)
    assert "example.test:53" in enabled
    assert "name regex ^([^.]+)\\.example\\.test\\.$ {1}." in enabled
    assert "answer name ^([^.]+)\\.$ {1}.example.test." in enabled
    assert 'log . "{name} {type} {rcode}"' in enabled
    assert "forward . 127.0.0.1:5354" in enabled
    assert ".:53 {" not in enabled
    assert "health :8080" in enabled
    assert "ready :8181" in enabled

    disabled = corefile_content(replace(settings, dns_fallback_enabled=False))
    assert "name regex" not in disabled
    assert "answer name" not in disabled
    assert "fallthrough" not in disabled
    assert ".:53 {" not in disabled


def test_corefile_fritz_box_answer_rewrite(tmp_path) -> None:
    settings = make_settings(tmp_path, dns_fallback_mode="fritz.box")
    corefile = corefile_content(settings)
    assert "name regex ^([^.]+)\\.example\\.test\\.$ {1}.fritz.box." in corefile
    assert "answer name ^([^.]+)\\.fritz\\.box\\.$ {1}.example.test." in corefile
