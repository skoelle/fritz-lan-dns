from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    """Raised when required configuration is missing."""


def load_env(path: Path | None = None) -> Path | None:
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)
    return env_path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value in (None, ""):
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _env_path(name: str, default: Path) -> Path:
    value = _env(name)
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Settings:
    root: Path
    box_ip: str
    box_user: str
    box_pw: str
    data_root: Path
    db_root: Path
    dns_root: Path
    device_names_file: Path
    fetch_interval_minutes: int
    snapshot_retention_days: int
    raw_retention_days: int
    host_retention_days: int
    http_scan_enabled: bool
    http_scan_timeout: float
    http_scan_workers: int
    http_scan_fail_threshold: int
    web_host: str
    web_port: int
    scheduler_enabled: bool
    dns_domain: str
    dns_zone_ttl: int
    dns_include_ipv6: bool
    dns_fallback_enabled: bool
    dns_fallback_mode: str
    dns_fallback_upstream: str
    dns_ns_ip: str
    dns_container_root: str
    fritz_timeout: int
    fritz_verify_tls: bool
    openwrt_enabled: bool
    openwrt_url: str
    openwrt_user: str
    openwrt_password: str
    openwrt_timeout: int
    openwrt_verify_tls: bool
    log_level: str

    @property
    def db_path(self) -> Path:
        return self.db_root / "fritz_lan_dns.db"

    @property
    def hosts_file(self) -> Path:
        return self.dns_root / "hosts"

    @property
    def zone_file(self) -> Path:
        return self.dns_root / f"{self.dns_domain}.zone"

    @property
    def corefile(self) -> Path:
        return self.dns_root / "Corefile"

    @property
    def raw_dir(self) -> Path:
        return self.data_root / "raw"

    def ensure_dirs(self) -> None:
        for directory in (self.data_root, self.db_root, self.dns_root, self.raw_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def require_fetch(self) -> None:
        missing = [
            name
            for name, value in (
                ("BoxIP", self.box_ip),
                ("BoxUSER", self.box_user),
                ("BoxPW", self.box_pw),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Fehlende Fritz!Box-Konfiguration: "
                + ", ".join(missing)
                + ". Bitte .env anlegen (siehe .env.example)."
            )

    def require_dns(self) -> None:
        missing = []
        if not self.dns_domain:
            missing.append("DNS_DOMAIN")
        if self.dns_fallback_enabled and not self.dns_fallback_upstream:
            missing.append("DNS_FALLBACK_UPSTREAM")
        if missing:
            raise ConfigError(
                "Fehlende DNS-Konfiguration: "
                + ", ".join(missing)
                + ". Bitte .env anlegen (siehe .env.example)."
            )


def build_settings(env_file: Path | None = None) -> Settings:
    load_env(env_file)
    box_ip = _env("BoxIP") or _env("BOX_IP") or ""
    device_names = _env_path("DEVICE_NAMES_FILE", ROOT / "config" / "device-names.conf")
    return Settings(
        root=ROOT,
        box_ip=box_ip,
        box_user=_env("BoxUSER") or _env("BOX_USER") or "",
        box_pw=_env("BoxPW") or _env("BOX_PW") or "",
        data_root=_env_path("FRITZ_LAN_DNS_DATA_ROOT", ROOT / "data"),
        db_root=_env_path("FRITZ_LAN_DNS_DB_ROOT", ROOT / "db"),
        dns_root=_env_path("FRITZ_LAN_DNS_DNS_ROOT", ROOT / "dns"),
        device_names_file=device_names,
        fetch_interval_minutes=_env_int("FETCH_INTERVAL_MINUTES", 60),
        snapshot_retention_days=_env_int("SNAPSHOT_RETENTION_DAYS", 90),
        raw_retention_days=_env_int("RAW_RETENTION_DAYS", 30),
        host_retention_days=_env_int("DNS_HOST_RETENTION_DAYS", 90),
        http_scan_enabled=_env_bool("HTTP_SCAN_ENABLED", True),
        http_scan_timeout=_env_float("HTTP_SCAN_TIMEOUT", 1.0),
        http_scan_workers=_env_int("HTTP_SCAN_WORKERS", 16),
        http_scan_fail_threshold=_env_int("HTTP_SCAN_FAIL_THRESHOLD", 2),
        web_host=_env("WEB_HOST") or "0.0.0.0",
        web_port=_env_int("WEB_PORT", 8346),
        scheduler_enabled=_env_bool("SCHEDULER_ENABLED", True),
        dns_domain=(_env("DNS_DOMAIN") or "").strip("."),
        dns_zone_ttl=_env_int("DNS_ZONE_TTL", 60),
        dns_include_ipv6=_env_bool("DNS_INCLUDE_IPV6", False),
        dns_fallback_enabled=_env_bool("DNS_FALLBACK_ENABLED", True),
        dns_fallback_mode=(_env("DNS_FALLBACK_MODE") or "fritz.box").lower(),
        dns_fallback_upstream=_env("DNS_FALLBACK_UPSTREAM")
        or (f"{box_ip}:53" if box_ip else ""),
        dns_ns_ip=_env("DNS_NS_IP") or "",
        dns_container_root=_env("DNS_CONTAINER_ROOT") or "/zones",
        fritz_timeout=_env_int("FRITZ_TIMEOUT", 25),
        fritz_verify_tls=_env_bool("FRITZ_VERIFY_TLS", False),
        openwrt_enabled=_env_bool("OPENWRT_ENABLED", False),
        openwrt_url=(_env("OPENWRT_URL") or "").rstrip("/"),
        openwrt_user=_env("OPENWRT_USER") or "",
        openwrt_password=_env("OPENWRT_PASSWORD") or "",
        openwrt_timeout=_env_int("OPENWRT_TIMEOUT", 25),
        openwrt_verify_tls=_env_bool("OPENWRT_VERIFY_TLS", False),
        log_level=(_env("LOG_LEVEL") or "INFO").upper(),
    )
