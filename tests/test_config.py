from __future__ import annotations

import pytest

from fritz_lan_dns.config import ConfigError, build_settings
from tests.helpers import make_settings


def test_require_dns_reports_missing_values(tmp_path) -> None:
    settings = make_settings(
        tmp_path,
        dns_domain="",
        dns_fallback_upstream="",
    )
    with pytest.raises(ConfigError) as exc:
        settings.require_dns()
    message = str(exc.value)
    assert "DNS_DOMAIN" in message
    assert "DNS_FALLBACK_UPSTREAM" in message


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
        "DNS_FALLBACK_UPSTREAM",
        "OPENWRT_ENABLED",
        "OPENWRT_URL",
        "OPENWRT_USER",
        "OPENWRT_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = build_settings(env_file=tmp_path / "missing.env")
    assert settings.box_ip == ""
    assert settings.box_user == ""
    assert settings.box_pw == ""
    assert settings.dns_domain == ""
    assert settings.dns_fallback_mode == "fritz.box"
    assert settings.dns_fallback_upstream == ""
    assert settings.openwrt_enabled is False
    assert settings.openwrt_url == ""
    assert settings.openwrt_user == ""
