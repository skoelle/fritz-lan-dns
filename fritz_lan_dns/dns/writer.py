from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

from ..config import Settings
from ..db import get_setting, set_setting
from ..models import DnsRecord


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def hosts_file_content(records: list[DnsRecord]) -> str:
    lines = [
        f"{record.ip} {record.fqdn}"
        for record in records
        if record.writable
    ]
    return "\n".join(sorted(set(lines))) + "\n"


def zone_content(
    records: list[DnsRecord],
    domain: str,
    ttl: int,
    serial: int,
    ns_ip: str = "",
) -> str:
    origin = f"{domain}."
    lines = [
        f"$ORIGIN {origin}",
        f"$TTL {ttl}",
        f"@ IN SOA ns1.{origin} hostmaster.{origin} (",
        f"    {serial}",
        "    3600",
        "    600",
        "    86400",
        f"    {ttl}",
        ")",
        f"@ IN NS ns1.{origin}",
    ]
    if ns_ip:
        lines.append(f"ns1 IN A {ns_ip}")
    for record in records:
        if not record.writable:
            continue
        record_type = "AAAA" if ":" in record.ip else "A"
        lines.append(f"{record.hostname} IN {record_type} {record.ip}")
    return "\n".join(lines) + "\n"


def corefile_content(settings: Settings) -> str:
    domain = settings.dns_domain
    escaped_domain = domain.replace(".", "\\.")
    hosts_path = f"{settings.dns_container_root.rstrip('/')}/hosts"
    ttl = settings.dns_zone_ttl
    head = []
    if settings.dns_fallback_enabled:
        if settings.dns_fallback_mode == "fritz.box":
            replacement = "{1}.fritz.box."
            answer_pattern = r"^([^.]+)\.fritz\.box\.$"
        else:
            replacement = "{1}."
            answer_pattern = r"^([^.]+)\.$"
        fallback = [
            f"{domain}:53 {{",
            "    errors",
            "    reload 30s",
            "    health :8080",
            "    ready :8181",
            f"    hosts {hosts_path} {domain} {{",
            f"        ttl {ttl}",
            "        reload 5s",
            "        no_reverse",
            "        fallthrough",
            "    }",
            "    forward . 127.0.0.1:5354",
            "    cache 30",
            "}",
            "",
            ".:5354 {",
            "    bind 127.0.0.1",
            "    errors",
            '    log . "{name} {type} {rcode}"',
            "    rewrite stop {",
            f"        name regex ^([^.]+)\\.{escaped_domain}\\.$ {replacement}",
            f"        answer name {answer_pattern} {{1}}.{domain}.",
            "    }",
            f"    forward . {settings.dns_fallback_target}",
            "}",
        ]
        return "\n".join(head + fallback) + "\n"

    disabled = [
        f"{domain}:53 {{",
        "    errors",
        "    reload 30s",
        "    health :8080",
        "    ready :8181",
        f"    hosts {hosts_path} {domain} {{",
        f"        ttl {ttl}",
        "        reload 5s",
        "        no_reverse",
        "    }",
        "}",
    ]
    return "\n".join(head + disabled) + "\n"


def _next_serial(conn: sqlite3.Connection, signature: str) -> int:
    old_signature = get_setting(conn, "dns_signature")
    old_serial = int(get_setting(conn, "zone_serial", "0") or "0")
    serial = old_serial if old_signature == signature else old_serial + 1
    if serial <= 0:
        serial = 1
    set_setting(conn, "dns_signature", signature)
    set_setting(conn, "zone_serial", str(serial))
    return serial


def write_dns_files(
    settings: Settings, conn: sqlite3.Connection, records: list[DnsRecord]
) -> dict:
    settings.require_dns()
    settings.ensure_dirs()
    hosts_text = hosts_file_content(records)
    corefile_text = corefile_content(settings)
    signature_source = "\0".join(
        (
            hosts_text,
            corefile_text,
            settings.dns_domain,
            str(settings.dns_zone_ttl),
            settings.dns_ns_ip,
        )
    )
    signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()
    serial = _next_serial(conn, signature)
    zone_text = zone_content(
        records,
        settings.dns_domain,
        settings.dns_zone_ttl,
        serial,
        settings.dns_ns_ip,
    )
    atomic_write(settings.hosts_file, hosts_text)
    atomic_write(settings.corefile, corefile_text)
    atomic_write(settings.zone_file, zone_text)
    return {
        "hosts": str(settings.hosts_file),
        "zone": str(settings.zone_file),
        "corefile": str(settings.corefile),
        "serial": serial,
        "records": sum(1 for record in records if record.writable),
        "conflicts": sum(1 for record in records if record.status == "conflict"),
    }


def ensure_dns_files(settings: Settings, conn: sqlite3.Connection) -> None:
    settings.require_dns()
    settings.ensure_dirs()
    if not settings.hosts_file.exists():
        atomic_write(settings.hosts_file, "")
    atomic_write(settings.corefile, corefile_content(settings))
    if not settings.zone_file.exists():
        serial = int(get_setting(conn, "zone_serial", "0") or "0") or 1
        atomic_write(
            settings.zone_file,
            zone_content([], settings.dns_domain, settings.dns_zone_ttl, serial, settings.dns_ns_ip),
        )
