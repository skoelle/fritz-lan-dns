from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import Settings
from .dns import build_dns_records, ensure_dns_files, write_dns_files
from .fritz import FritzClient, load_mesh_json, parse_host_entries, parse_mesh
from .merge import merge_hosts
from .models import HostRecord
from .openwrt import OpenWrtClient, parse_openwrt_hosts
from .portscan import scan_ports
from .util import iso_now, load_device_names

LOGGER = logging.getLogger("fritz_lan_dns.fetch")


def _write_raw(settings: Settings, kind: str, content: str) -> Path:
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = settings.raw_dir / f"{stamp}-{kind}.json"
    path.write_text(content, encoding="utf-8")
    return path


def _fetch_openwrt_hosts(settings: Settings) -> tuple[list[HostRecord], str | None]:
    if not settings.openwrt_enabled:
        return [], None
    missing = [
        name
        for name, value in (
            ("OPENWRT_URL", settings.openwrt_url),
            ("OPENWRT_USER", settings.openwrt_user),
            ("OPENWRT_PASSWORD", settings.openwrt_password),
        )
        if not value
    ]
    if missing:
        return [], "OpenWrt-Konfiguration unvollständig: " + ", ".join(missing)
    try:
        client = OpenWrtClient(
            settings.openwrt_url,
            settings.openwrt_user,
            settings.openwrt_password,
            timeout=settings.openwrt_timeout,
            verify_tls=settings.openwrt_verify_tls,
        )
        leases = client.get_dhcp_leases()
        hints = client.get_host_hints()
        _write_raw(settings, "openwrt-leases", json.dumps(leases, ensure_ascii=False))
        _write_raw(settings, "openwrt-hints", json.dumps(hints, ensure_ascii=False))
        hosts = parse_openwrt_hosts(
            leases, hints, include_ipv6=settings.dns_include_ipv6
        )
        return hosts, None
    except Exception as exc:  # noqa: BLE001 - optionaler Abruf darf Fetch nicht stoppen
        LOGGER.warning("OpenWrt-Abruf fehlgeschlagen: %s", exc)
        return [], f"OpenWrt-Abruf fehlgeschlagen: {exc}"


def prune_raw_files(settings: Settings) -> int:
    if settings.raw_retention_days <= 0 or not settings.raw_dir.exists():
        return 0
    cutoff = time.time() - settings.raw_retention_days * 86400
    removed = 0
    for path in settings.raw_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def run_fetch(settings: Settings, legacy_json: Path | None = None) -> dict:
    settings.require_fetch()
    settings.require_dns()
    settings.ensure_dirs()
    db.init_schema(settings.db_path)
    descriptions = load_device_names(settings.device_names_file)
    started = time.monotonic()
    started_at = iso_now()

    with db.connect(settings.db_path) as conn:
        ensure_dns_files(settings, conn)
        snapshot_id = db.start_snapshot(conn, started_at)
        try:
            client = FritzClient(
                settings.box_ip,
                settings.box_user,
                settings.box_pw,
                timeout=settings.fritz_timeout,
                verify_tls=settings.fritz_verify_tls,
            )
            security_port = client.get_security_port()
            mesh_path = client.get_mesh_list_path()
            mesh_text = client.fetch_mesh_text(mesh_path, security_port)
            mesh_raw = load_mesh_json(mesh_text)
            mesh = parse_mesh(mesh_raw, descriptions)

            host_fetch = client.get_hosts()
            host_entries = parse_host_entries(host_fetch.entries, descriptions)
            warning = None
            if host_fetch.errors:
                warning = f"{len(host_fetch.errors)} Host-Einträge konnten nicht gelesen werden"
            if not host_entries:
                warning = (
                    (warning + "; " if warning else "")
                    + "Hosts-API lieferte keine Einträge, nutze Mesh-Nodes"
                )

            extra_hosts, openwrt_error = _fetch_openwrt_hosts(settings)
            if openwrt_error:
                warning = (warning + "; " if warning else "") + openwrt_error
            hosts = merge_hosts(
                host_entries, mesh.nodes, descriptions, extra_hosts=extra_hosts
            )

            http_results: dict[str, bool] = {}
            if settings.http_scan_enabled:
                targets = {host.ip for host in hosts if host.active and host.ip}
                try:
                    http_results = scan_ports(
                        targets,
                        timeout=settings.http_scan_timeout,
                        workers=settings.http_scan_workers,
                    )
                    db.record_http_scan(
                        conn,
                        http_results,
                        fail_threshold=settings.http_scan_fail_threshold,
                    )
                except Exception as exc:  # noqa: BLE001 - Scan darf Fetch nicht stoppen
                    LOGGER.warning("Port-80-Scan fehlgeschlagen: %s", exc)
                    warning = (
                        (warning + "; " if warning else "")
                        + "Port-80-Scan fehlgeschlagen"
                    )
            http_open_count = sum(1 for value in http_results.values() if value)

            _write_raw(settings, "mesh", mesh_text)
            _write_raw(settings, "hosts", host_fetch.raw_xml)
            if legacy_json is not None:
                legacy_json.parent.mkdir(parents=True, exist_ok=True)
                legacy_json.write_text(mesh_text, encoding="utf-8")

            db.store_snapshot_data(
                conn,
                snapshot_id,
                mesh,
                hosts,
                mesh_text,
                host_fetch.raw_xml,
                warning,
            )

            all_hosts = db.load_hosts(conn)
            static = db.load_dns_static(conn)
            records = build_dns_records(
                all_hosts,
                static,
                settings.dns_domain,
                settings.dns_include_ipv6,
            )
            db.replace_dns_records(conn, records)
            dns_summary = write_dns_files(settings, conn, records)
            duration_ms = int((time.monotonic() - started) * 1000)
            db.finish_snapshot(
                conn,
                snapshot_id,
                status="ok",
                duration_ms=duration_ms,
                warning=warning,
                dns_count=dns_summary["records"],
                zone_serial=dns_summary["serial"],
            )
            db.prune_snapshots(conn, settings.snapshot_retention_days)
            db.prune_hosts(conn, settings.host_retention_days)
            db.prune_http_status(conn, settings.host_retention_days)
            prune_raw_files(settings)
            LOGGER.info(
                "Snapshot %s: %s Nodes (%s online), %s Hosts (%s OpenWrt), "
                "%s DNS-Records, %s HTTP-Ports",
                snapshot_id,
                len(mesh.nodes),
                sum(1 for node in mesh.nodes if node.online),
                len(hosts),
                len(extra_hosts),
                dns_summary["records"],
                http_open_count,
            )
            return {
                "snapshot_id": snapshot_id,
                "nodes": len(mesh.nodes),
                "online": sum(1 for node in mesh.nodes if node.online),
                "hosts": len(hosts),
                "openwrt_hosts": len(extra_hosts),
                "dns_records": dns_summary["records"],
                "dns_conflicts": dns_summary["conflicts"],
                "http_open": http_open_count,
                "zone_serial": dns_summary["serial"],
                "warning": warning,
                "duration_ms": duration_ms,
                "hosts_file": dns_summary["hosts"],
                "zone_file": dns_summary["zone"],
                "corefile": dns_summary["corefile"],
            }
        except Exception as exc:  # noqa: BLE001 - Snapshot muss als Fehler markiert werden
            duration_ms = int((time.monotonic() - started) * 1000)
            db.finish_snapshot(
                conn,
                snapshot_id,
                status="failed",
                duration_ms=duration_ms,
                error=str(exc)[:2000],
            )
            LOGGER.exception("Abruf fehlgeschlagen")
            raise
