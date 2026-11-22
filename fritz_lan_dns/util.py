from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def parse_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def http_url(ip: str | None) -> str | None:
    if not ip:
        return None
    if ":" in ip and not ip.startswith("["):
        return f"http://[{ip}]/"
    return f"http://{ip}/"


SOURCE_LABELS = {
    "fritz": "FritzBox",
    "mesh": "FritzBox",
    "openwrt": "OpenWrt",
    "static": "Statisch",
}

SOURCE_FILTER_GROUPS = {
    "fritz": ("fritz", "mesh"),
}


def source_label(source: str | None) -> str:
    value = (source or "").strip().lower()
    return SOURCE_LABELS.get(value, source or "-")


def source_filter_values(source: str | None) -> tuple[str, ...]:
    value = (source or "").strip().lower()
    return SOURCE_FILTER_GROUPS.get(value, (value,) if value else ())


def load_device_names(path: Path) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    if not path.exists():
        return descriptions
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return descriptions
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            descriptions[parts[0].lower()] = parts[1].strip()
    return descriptions


def strip_host_domain(name: str, domain: str) -> str:
    value = (name or "").strip().lower().rstrip(".")
    for suffix in (f".{domain}", ".fritz.box", ".lan"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value


def host_sort_key(hostname: str | None) -> str:
    return (hostname or "").lower()


_SAFE_HOST = re.compile(r"[^a-z0-9-]+")


def sanitize_hostname(raw: str | None, domain: str) -> str | None:
    value = strip_host_domain(raw or "", domain)
    if not value:
        return None
    value = value.replace(".", "-")
    value = _SAFE_HOST.sub("-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    if not value or value in {"localhost", "broadcast", "all-hosts"}:
        return None
    if len(value) > 63:
        value = value[:63].rstrip("-")
    return value or None
