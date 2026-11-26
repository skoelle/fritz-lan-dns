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
    "fritz-repeater": "FRITZ-Repeater",
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


def ip_mode_meta(
    address_source: str | None,
    static_dhcp: bool | int | str | None,
    source: str | None = "",
) -> dict[str, str]:
    value = (address_source or "").strip().lower()
    reservation: bool | None
    if isinstance(static_dhcp, str):
        reservation = static_dhcp.strip().lower() in ("1", "true", "yes", "on")
    elif static_dhcp is None:
        reservation = None
    else:
        reservation = bool(static_dhcp)

    if value == "static":
        return {
            "label": "🔒 statisch",
            "css": "ip-static",
            "title": "IP ist am Gerät fest eingestellt",
        }
    if value == "dhcp":
        if reservation is True:
            router = "OpenWrt" if (source or "").strip().lower() == "openwrt" else "FRITZ!Box"
            return {
                "label": "📌 DHCP",
                "css": "ip-reserved",
                "title": f"DHCP, {router} weist die IP dauerhaft zu",
            }
        if reservation is False:
            return {
                "label": "🔄 DHCP",
                "css": "ip-dhcp",
                "title": "DHCP ohne dauerhafte Zuweisung",
            }
        title = "DHCP, Reservierung unbekannt"
        if (source or "").strip().lower() == "openwrt":
            title += " (OpenWrt)"
        return {"label": "🔄 DHCP", "css": "ip-dhcp", "title": title}
    return {
        "label": "–",
        "css": "ip-unknown",
        "title": "Zuweisung unbekannt",
    }


def format_duration(value: object) -> str:
    if value is None or value == "":
        return "-"
    try:
        total = int(float(str(value)))
    except (TypeError, ValueError):
        return "-"
    if total <= 0:
        return "0m"
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def device_description(
    hostname: str | None, descriptions: dict[str, str]
) -> str:
    value = (hostname or "").strip().lower()
    if not value:
        return ""
    description = descriptions.get(value)
    if description:
        return description
    return descriptions.get(value.split(".", 1)[0], "")


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
