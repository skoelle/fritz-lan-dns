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
        "http_scan_enabled": True,
        "http_scan_timeout": 0.05,
        "http_scan_workers": 4,
        "http_scan_fail_threshold": 2,
        "web_host": "127.0.0.1",
        "web_port": 8346,
        "scheduler_enabled": False,
        "dns_domain": "example.test",
        "dns_zone_ttl": 60,
        "dns_include_ipv6": False,
        "dns_fallback_enabled": True,
        "dns_fallback_mode": "bare",
        "dns_ns_ip": "192.0.2.10",
        "dns_container_root": "/zones",
        "fritz_timeout": 25,
        "fritz_verify_tls": False,
        "fritz_repeater_ips": "",
        "fritz_repeater_user": "",
        "fritz_repeater_pw": "",
        "fritz_repeater_timeout": 25,
        "openwrt_enabled": False,
        "openwrt_url": "",
        "openwrt_user": "",
        "openwrt_password": "",
        "openwrt_timeout": 25,
        "openwrt_verify_tls": False,
        "openwrt_topology_parent_ip": "",
        "openwrt_mwan3_enabled": True,
        "log_level": "INFO",
    }
    values.update(overrides)
    return Settings(**values)
