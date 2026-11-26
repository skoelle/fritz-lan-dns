from __future__ import annotations

from pathlib import Path

import pytest

from fritz_lan_dns.fritz.events import DeviceLogParseError, parse_device_log

FIXTURE = Path(__file__).parent / "fixtures" / "device-log.xml"


def _events():
    return parse_device_log(FIXTURE.read_text(encoding="utf-8"))


def test_parse_device_log_filters_wlan_and_classifies() -> None:
    events = _events()
    assert len(events) == 8
    assert {event.event_id for event in events} == {
        674,
        719,
        783,
        787,
        791,
        794,
        796,
    }
    assert all(event.group == "wlan" for event in events)

    by_id = {event.event_id: event for event in events}
    radar = by_id[796]
    assert radar.severity == "critical"
    assert radar.channel == "124"
    assert radar.node == ""
    assert "Radar wurde auf Kanal 124" in radar.message
    assert radar.occurrences == 1

    cac = by_id[794]
    assert cac.severity == "warning"
    assert cac.node == "repeater-a"
    assert cac.channel == "100-128"
    assert cac.occurrences == 2
    assert cac.event_at == "2026-11-26 16:42:59"
    assert cac.first_seen == "2026-11-26 16:42:58"
    assert "[" not in cac.message
    assert "Meldungen seit" not in cac.message

    interference = by_id[791]
    assert interference.severity == "warning"
    assert interference.node == "repeater-a"
    assert interference.channel == "108"
    assert interference.occurrences == 1290
    assert interference.first_seen == "2026-10-17 18:15:12"

    assert by_id[783].severity == "critical"
    assert by_id[787].severity == "warning"
    assert by_id[674].severity == "info"
    assert by_id[719].severity == "info"
    assert by_id[674].occurrences == 16
    assert by_id[719].occurrences == 7


def test_parse_device_log_unknown_wlan_event_is_info() -> None:
    xml = """
    <DeviceLog>
      <Event>
        <id>799</id>
        <group>wlan</group>
        <date>26.11.26</date>
        <time>12:00:00</time>
        <msg>Neues WLAN-Ereignis auf Kanal 44.</msg>
      </Event>
    </DeviceLog>
    """
    events = parse_device_log(xml)
    assert len(events) == 1
    assert events[0].severity == "info"
    assert events[0].channel == "44"


def test_parse_device_log_rejects_invalid_xml() -> None:
    with pytest.raises(DeviceLogParseError):
        parse_device_log("<DeviceLog>")
