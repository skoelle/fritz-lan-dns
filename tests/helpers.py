"""Shared test helpers with generic, non-private settings."""

from __future__ import annotations

from pathlib import Path

from fritz_lan_dns.config import ROOT, Settings


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "root": ROOT,
        "box_ip": "192.0.2.1",
        "box_user": "fritz-user",
        "box_pw": "fritz-password",
        "data_root": tmp_path / "data",
        "db_root": tmp_path / "db",
        "dns_root": tmp_path / "dns",
        "device_names_file": tmp_path / "device-names.conf",
        "fetch_interval_minutes": 60,
        "snapshot_retention_days": 90,
        "raw_retention_days": 30,
        "host_retention_days": 90,
        "web_host": "127.0.0.1",
        "web_port": 8346,
        "scheduler_enabled": False,
        "dns_domain": "example.test",
        "dns_zone_ttl": 60,
        "dns_include_ipv6": False,
        "dns_primary_upstream": "192.0.2.2:53",
        "dns_fallback_enabled": True,
        "dns_fallback_mode": "bare",
        "dns_fallback_upstream": "192.0.2.1:53",
        "dns_ns_ip": "192.0.2.10",
        "dns_container_root": "/zones",
        "fritz_timeout": 25,
        "fritz_verify_tls": False,
        "log_level": "INFO",
    }
    values.update(overrides)
    return Settings(**values)
