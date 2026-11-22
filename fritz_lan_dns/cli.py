from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__, db
from .config import ConfigError, build_settings
from .dns import build_dns_records, ensure_dns_files, write_dns_files
from .fetch import run_fetch


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _cmd_fetch(args: argparse.Namespace) -> int:
    settings = build_settings(Path(args.env_file) if args.env_file else None)
    _configure_logging(settings.log_level)
    legacy = Path(args.legacy_json) if args.legacy_json else None
    try:
        result = run_fetch(settings, legacy_json=legacy)
    except Exception as exc:  # noqa: BLE001 - CLI meldet Fehler
        print(f"Abruf fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_init_db(args: argparse.Namespace) -> int:
    settings = build_settings(Path(args.env_file) if args.env_file else None)
    _configure_logging(settings.log_level)
    settings.ensure_dirs()
    db.init_schema(settings.db_path)
    try:
        with db.connect(settings.db_path) as conn:
            ensure_dns_files(settings, conn)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 1
    print(f"Datenbank: {settings.db_path}")
    print(f"DNS-Dateien: {settings.dns_root}")
    return 0


def _cmd_rebuild_dns(args: argparse.Namespace) -> int:
    settings = build_settings(Path(args.env_file) if args.env_file else None)
    _configure_logging(settings.log_level)
    settings.ensure_dirs()
    db.init_schema(settings.db_path)
    with db.connect(settings.db_path) as conn:
        hosts = db.load_hosts(conn)
        static = db.load_dns_static(conn)
        records = build_dns_records(
            hosts,
            static,
            settings.dns_domain,
            settings.dns_include_ipv6,
        )
        db.replace_dns_records(conn, records)
        try:
            summary = write_dns_files(settings, conn, records)
        except ConfigError as exc:
            print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
            return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_web(args: argparse.Namespace) -> int:
    if args.host:
        os.environ["WEB_HOST"] = args.host
    if args.port:
        os.environ["WEB_PORT"] = str(args.port)
    import uvicorn

    settings = build_settings(Path(args.env_file) if args.env_file else None)
    _configure_logging(settings.log_level)
    uvicorn.run(
        "fritz_lan_dns.web.server:app",
        host=args.host or settings.web_host,
        port=args.port or settings.web_port,
        log_level=settings.log_level.lower(),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fritz-lan-dns",
        description="Fritz!Box Mesh-/Host-Daten sammeln und als DNS bereitstellen",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--env-file", help="Pfad zur .env-Datei")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Einmalig Mesh- und Host-Daten abrufen")
    fetch.add_argument(
        "--legacy-json",
        help="Mesh-JSON zusätzlich als Datei ablegen",
    )
    fetch.set_defaults(func=_cmd_fetch)

    init_db = sub.add_parser("init-db", help="SQLite-Schema und DNS-Dateien anlegen")
    init_db.set_defaults(func=_cmd_init_db)

    rebuild = sub.add_parser("rebuild-dns", help="DNS-Dateien aus der Datenbank neu bauen")
    rebuild.set_defaults(func=_cmd_rebuild_dns)

    web = sub.add_parser("web", help="Webinterface starten")
    web.add_argument("--host", help="Bind-Adresse (Default: WEB_HOST aus .env)")
    web.add_argument("--port", type=int, help="Port (Default: WEB_PORT aus .env)")
    web.set_defaults(func=_cmd_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
