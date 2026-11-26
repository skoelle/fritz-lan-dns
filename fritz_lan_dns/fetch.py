from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import Settings
from .dns import build_dns_records, ensure_dns_files, write_dns_files
from .fritz import (
    FritzClient,
    load_mesh_json,
    parse_device_log,
    parse_host_entries,
    parse_mesh,
    parse_wlan,
)
from .fritz.repeater import fetch_repeater_wlan
from .merge import merge_hosts
from .models import HostRecord, MeshSnapshot, WlanEvent, WlanSnapshot
from .openwrt import OpenWrtClient, OpenWrtNotFound, parse_openwrt_hosts
from .portscan import scan_ports
from .util import iso_now, load_device_names

LOGGER = logging.getLogger("fritz_lan_dns.fetch")


def _write_raw(settings: Settings, kind: str, content: str) -> Path:
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = settings.raw_dir / f"{stamp}-{kind}.json"
    path.write_text(content, encoding="utf-8")
    return path


def _openwrt_client(settings: Settings) -> tuple[OpenWrtClient | None, str | None]:
    if not settings.openwrt_enabled:
        return None, None
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
        return None, "OpenWrt-Konfiguration unvollständig: " + ", ".join(missing)
    try:
        client = OpenWrtClient(
            settings.openwrt_url,
            settings.openwrt_user,
            settings.openwrt_password,
            timeout=settings.openwrt_timeout,
            verify_tls=settings.openwrt_verify_tls,
        )
    except Exception as exc:  # noqa: BLE001 - optionaler Abruf darf Fetch nicht stoppen
        LOGGER.warning("OpenWrt-Client konnte nicht erstellt werden: %s", exc)
        return None, f"OpenWrt-Abruf fehlgeschlagen: {exc}"
    return client, None


def _fetch_openwrt_hosts(
    settings: Settings, client: OpenWrtClient
) -> tuple[list[HostRecord], str | None]:
    try:
        leases = client.get_dhcp_leases()
        hints = client.get_host_hints()
        _write_raw(settings, "openwrt-leases", json.dumps(leases, ensure_ascii=False))
        _write_raw(settings, "openwrt-hints", json.dumps(hints, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - optionaler Abruf darf Fetch nicht stoppen
        LOGGER.warning("OpenWrt-Abruf fehlgeschlagen: %s", exc)
        return [], f"OpenWrt-Abruf fehlgeschlagen: {exc}"

    static_leases: list[dict] | None = None
    warning = None
    try:
        static_leases = client.get_static_leases()
        _write_raw(
            settings,
            "openwrt-static-hosts",
            json.dumps(static_leases, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001 - optionale Anzeige
        LOGGER.warning("OpenWrt static_dhcp-Abruf fehlgeschlagen: %s", exc)
        warning = f"OpenWrt static_dhcp-Abruf fehlgeschlagen: {exc}"

    hosts = parse_openwrt_hosts(
        leases,
        hints,
        include_ipv6=settings.dns_include_ipv6,
        static_leases=static_leases,
    )
    return hosts, warning


def _fetch_openwrt_mwan3(
    settings: Settings, client: OpenWrtClient
) -> tuple[dict | None, str | None]:
    if not settings.openwrt_mwan3_enabled:
        return None, None
    try:
        data = client.get_mwan3_status()
    except OpenWrtNotFound as exc:
        LOGGER.info("MWAN3 nicht verfügbar: %s", exc)
        return None, None
    except Exception as exc:  # noqa: BLE001 - optionaler Abruf darf Fetch nicht stoppen
        LOGGER.warning("MWAN3-Abruf fehlgeschlagen: %s", exc)
        return None, f"MWAN3-Abruf fehlgeschlagen: {exc}"
    if not isinstance(data, dict):
        return None, "MWAN3-Abruf lieferte keine Daten"
    return data, None


def _attach_wireless_info(client: OpenWrtClient, mwan3: dict) -> None:
    try:
        interfaces = client.get_network_interfaces().get("interface") or []
    except Exception as exc:  # noqa: BLE001 - optionale Anreicherung
        LOGGER.warning("Netzwerk-Interfaces konnten nicht gelesen werden: %s", exc)
        return

    devices: dict[str, str] = {}
    for item in interfaces:
        if not isinstance(item, dict) or not item.get("interface"):
            continue
        device = item.get("l3_device") or item.get("device")
        if device:
            devices[item["interface"]] = device

    wireless: dict[str, dict] = {}
    for ifname in (mwan3.get("interfaces") or {}):
        device = devices.get(ifname)
        if not device:
            continue
        try:
            info = client.get_wireless_info(device)
        except OpenWrtNotFound:
            continue
        except Exception as exc:  # noqa: BLE001 - optionale Anreicherung
            LOGGER.warning("WLAN-Info für %s (%s) fehlgeschlagen: %s", ifname, device, exc)
            continue
        if not isinstance(info, dict) or info.get("signal") is None:
            continue
        wireless[ifname] = {
            "device": device,
            "mode": info.get("mode"),
            "ssid": info.get("ssid"),
            "signal": info.get("signal"),
            "noise": info.get("noise"),
            "quality": info.get("quality"),
            "quality_max": info.get("quality_max"),
            "bitrate": info.get("bitrate"),
            "frequency": info.get("frequency"),
            "channel": info.get("channel"),
        }
    if wireless:
        mwan3["wireless"] = wireless


def _parse_mesh_wlan(
    mesh_raw: dict, mesh: MeshSnapshot
) -> tuple[WlanSnapshot, str | None]:
    try:
        nodes = {node.uid: node for node in mesh.nodes}
        return parse_wlan(mesh_raw, nodes), None
    except Exception as exc:  # noqa: BLE001 - WLAN darf den Fetch nicht stoppen
        LOGGER.warning("WLAN-Parsing fehlgeschlagen: %s", exc)
        return WlanSnapshot(interfaces=[], clients=[]), (
            f"WLAN-Parsing fehlgeschlagen: {exc}"
        )


def _fetch_repeater_wlans(
    settings: Settings, hosts: list[HostRecord]
) -> tuple[WlanSnapshot, list[str]]:
    targets = [
        ip for ip in settings.fritz_repeater_ip_list if ip != settings.box_ip
    ]
    if not targets:
        return WlanSnapshot(interfaces=[], clients=[]), []

    host_by_ip = {host.ip: host for host in hosts if host.ip}
    host_by_mac = {host.mac.lower(): host for host in hosts if host.mac}
    interfaces = []
    clients = []
    warnings: list[str] = []
    for ip in targets:
        host = host_by_ip.get(ip)
        try:
            client = FritzClient(
                ip,
                settings.fritz_repeater_user,
                settings.fritz_repeater_pw,
                timeout=settings.fritz_repeater_timeout,
                verify_tls=settings.fritz_verify_tls,
            )
            wlan = fetch_repeater_wlan(
                client,
                ip=ip,
                device_name=host.hostname if host else None,
                description=host.description if host else "",
                host_by_mac=host_by_mac,
                host_by_ip=host_by_ip,
            )
        except Exception as exc:  # noqa: BLE001 - optionaler Abruf
            LOGGER.warning("FRITZ-Repeater %s: WLAN-Abruf fehlgeschlagen: %s", ip, exc)
            warnings.append(f"FRITZ-Repeater {ip}: WLAN-Abruf fehlgeschlagen: {exc}")
            continue
        interfaces.extend(wlan.interfaces)
        clients.extend(wlan.clients)
    return WlanSnapshot(interfaces=interfaces, clients=clients), warnings


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
            mesh_wlan, wlan_parse_error = _parse_mesh_wlan(mesh_raw, mesh)

            warning = None
            if wlan_parse_error:
                warning = wlan_parse_error

            static_dhcp_by_mac: dict[str, bool] | None = None
            try:
                static_dhcp_by_mac = client.get_static_dhcp_map()
            except Exception as exc:  # noqa: BLE001 - optionale Anzeige
                LOGGER.warning("static_dhcp-Abruf fehlgeschlagen: %s", exc)
                warning = (
                    (warning + "; " if warning else "")
                    + f"static_dhcp-Abruf fehlgeschlagen: {exc}"
                )

            host_fetch = client.get_hosts()
            host_entries = parse_host_entries(
                host_fetch.entries, descriptions, static_dhcp_by_mac
            )
            if host_fetch.errors:
                host_error = (
                    f"{len(host_fetch.errors)} Host-Einträge konnten nicht gelesen werden"
                )
                warning = (warning + "; " if warning else "") + host_error
            if not host_entries:
                warning = (
                    (warning + "; " if warning else "")
                    + "Hosts-API lieferte keine Einträge, nutze Mesh-Nodes"
                )

            wlan_events: list[WlanEvent] = []
            if settings.fritz_eventlog_enabled:
                try:
                    log_path = client.get_device_log_path()
                    log_text = client.fetch_device_log_text(log_path, security_port)
                    wlan_events = parse_device_log(log_text)
                except Exception as exc:  # noqa: BLE001 - optionaler Abruf
                    LOGGER.warning(
                        "Ereignisprotokoll-Abruf fehlgeschlagen (%s)",
                        type(exc).__name__,
                    )
                    warning = (
                        (warning + "; " if warning else "")
                        + "Ereignisprotokoll-Abruf fehlgeschlagen"
                    )

            extra_hosts: list[HostRecord] = []
            mwan3: dict | None = None
            openwrt_client, openwrt_error = _openwrt_client(settings)
            if openwrt_client is not None:
                extra_hosts, openwrt_error = _fetch_openwrt_hosts(
                    settings, openwrt_client
                )
                if openwrt_error:
                    warning = (warning + "; " if warning else "") + openwrt_error
                mwan3, mwan3_error = _fetch_openwrt_mwan3(settings, openwrt_client)
                if mwan3_error:
                    warning = (warning + "; " if warning else "") + mwan3_error
                if mwan3 is not None:
                    _attach_wireless_info(openwrt_client, mwan3)
                    _write_raw(
                        settings, "mwan3", json.dumps(mwan3, ensure_ascii=False)
                    )
            elif openwrt_error:
                warning = (warning + "; " if warning else "") + openwrt_error
            hosts = merge_hosts(
                host_entries, mesh.nodes, descriptions, extra_hosts=extra_hosts
            )
            extra_wlan, wlan_warnings = _fetch_repeater_wlans(settings, hosts)
            for wlan_warning in wlan_warnings:
                warning = (warning + "; " if warning else "") + wlan_warning
            mesh_wlan_ips = {
                item.device_ip for item in mesh_wlan.interfaces if item.device_ip
            }
            extra_interfaces = []
            skipped_uids: set[str] = set()
            for item in extra_wlan.interfaces:
                if item.device_ip and item.device_ip in mesh_wlan_ips:
                    skipped_uids.add(item.node_uid)
                    continue
                extra_interfaces.append(item)
            extra_clients = [
                client
                for client in extra_wlan.clients
                if client.ap_node_uid not in skipped_uids
            ]
            wlan = WlanSnapshot(
                interfaces=[*mesh_wlan.interfaces, *extra_interfaces],
                clients=[*mesh_wlan.clients, *extra_clients],
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
            if mwan3 is not None:
                db.store_snapshot_mwan3(
                    conn, snapshot_id, json.dumps(mwan3, ensure_ascii=False)
                )
            db.store_snapshot_wlan(
                conn,
                snapshot_id,
                json.dumps(
                    {
                        "interfaces": [asdict(item) for item in wlan.interfaces],
                        "clients": [asdict(item) for item in wlan.clients],
                    },
                    ensure_ascii=False,
                ),
            )
            if wlan_events:
                db.upsert_wlan_events(conn, wlan_events, snapshot_id)

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
            db.prune_wlan_events(conn, settings.event_retention_days)
            prune_raw_files(settings)
            mwan3_interfaces = len(mwan3.get("interfaces") or {}) if mwan3 else 0
            LOGGER.info(
                "Snapshot %s: %s Nodes (%s online), %s Hosts (%s OpenWrt), "
                "%s MWAN3-Interfaces, %s WLAN-Radios, %s WLAN-Clients, "
                "%s WLAN-Ereignisse, %s DNS-Records, %s HTTP-Ports",
                snapshot_id,
                len(mesh.nodes),
                sum(1 for node in mesh.nodes if node.online),
                len(hosts),
                len(extra_hosts),
                mwan3_interfaces,
                len(wlan.interfaces),
                len(wlan.clients),
                len(wlan_events),
                dns_summary["records"],
                http_open_count,
            )
            return {
                "snapshot_id": snapshot_id,
                "nodes": len(mesh.nodes),
                "online": sum(1 for node in mesh.nodes if node.online),
                "hosts": len(hosts),
                "openwrt_hosts": len(extra_hosts),
                "mwan3_interfaces": mwan3_interfaces,
                "wlan_interfaces": len(wlan.interfaces),
                "wlan_clients": len(wlan.clients),
                "wlan_events": len(wlan_events),
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
