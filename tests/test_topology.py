from __future__ import annotations

from fritz_lan_dns.web.data import connection_meta, display_role, format_kbps, speed_display


def test_display_role_hides_default_roles() -> None:
    assert display_role("unknown") == ""
    assert display_role("UNKNOWN") == ""
    assert display_role("client") == ""
    assert display_role("") == ""
    assert display_role(None) == ""
    assert display_role("master") == "Master"
    assert display_role("repeater") == "Repeater"
    assert display_role("bridge") == "Bridge"


def test_connection_meta() -> None:
    assert connection_meta("LAN") == ("🔌", "LAN", "lan")
    assert connection_meta("PLC") == ("⚡", "PLC", "plc")
    assert connection_meta("WLAN 5G") == ("📶", "WLAN 5G", "wlan")
    assert connection_meta("") == ("", "", "")
    assert connection_meta("something") == ("", "", "")


def test_speed_display() -> None:
    assert format_kbps(None) == "-"
    assert format_kbps(180000) == "180.0"
    assert speed_display(None, None) == {"label": "", "icon": "", "css": ""}
    assert speed_display(180000, 200000) == {
        "label": "180.0 / 200.0 Mbit/s",
        "icon": "🟢",
        "css": "ok",
    }
    assert speed_display(40000, 60000)["css"] == "slow"
    assert speed_display(0, 0)["css"] == "offline"
