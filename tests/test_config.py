from __future__ import annotations

import pytest

from fritz_lan_dns.config import ConfigError, build_settings
from tests.helpers import make_settings


def test_require_dns_reports_missing_values(tmp_path) -> None:
    settings = make_settings(
        tmp_path,
        dns_domain="",
        box_ip="",
    )
    with pytest.raises(ConfigError) as exc:
        settings.require_dns()
    message = str(exc.value)
    assert "DNS_DOMAIN" in message
    assert "BoxIP" in message


def test_require_fetch_reports_missing_values(tmp_path) -> None:
    settings = make_settings(tmp_path, box_ip="", box_user="", box_pw="")
    with pytest.raises(ConfigError) as exc:
        settings.require_fetch()
    message = str(exc.value)
    assert "BoxIP" in message
    assert "BoxUSER" in message
    assert "BoxPW" in message


def test_require_dns_accepts_complete_settings(tmp_path) -> None:
    settings = make_settings(tmp_path)
    settings.require_dns()
    settings.require_fetch()


def test_dns_fallback_target_derives_from_box_ip(tmp_path) -> None:
    settings = make_settings(tmp_path, box_ip="192.0.2.1")
    assert settings.dns_fallback_target == "192.0.2.1:53"


def test_build_settings_has_no_private_defaults(tmp_path, monkeypatch) -> None:
    for key in (
        "BoxIP",
        "BOX_IP",
        "BoxUSER",
        "BOX_USER",
        "BoxPW",
        "BOX_PW",
        "DNS_DOMAIN",
        "DNS_FALLBACK_MODE",
        "OPENWRT_ENABLED",
        "OPENWRT_URL",
        "OPENWRT_USER",
        "OPENWRT_PASSWORD",
        "OPENWRT_TOPOLOGY_PARENT_IP",
        "OPENWRT_MWAN3_ENABLED",
        "FRITZ_REPEATER_IPS",
        "FRITZ_REPEATER_USER",
        "FRITZ_REPEATER_PW",
        "FRITZ_REPEATER_TIMEOUT",
        "FRITZ_EVENTLOG_ENABLED",
        "EVENT_RETENTION_DAYS",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = build_settings(env_file=tmp_path / "missing.env")
    assert settings.box_ip == ""
    assert settings.box_user == ""
    assert settings.box_pw == ""
    assert settings.dns_domain == ""
    assert settings.dns_fallback_mode == "fritz.box"
    assert settings.dns_fallback_target == ""
    assert settings.openwrt_enabled is False
    assert settings.openwrt_url == ""
    assert settings.openwrt_user == ""
    assert settings.openwrt_topology_parent_ip == ""
    assert settings.openwrt_mwan3_enabled is True
    assert settings.fritz_repeater_ips == ""
    assert settings.fritz_repeater_ip_list == ()
    assert settings.fritz_repeater_timeout == 25
    assert settings.fritz_eventlog_enabled is True
    assert settings.event_retention_days == 90


def test_repeater_ip_list_splits_and_deduplicates(tmp_path) -> None:
    settings = make_settings(
        tmp_path,
        fritz_repeater_ips="192.0.2.10, 192.0.2.11;192.0.2.10\n192.0.2.12",
    )
    assert settings.fritz_repeater_ip_list == (
        "192.0.2.10",
        "192.0.2.11",
        "192.0.2.12",
    )
