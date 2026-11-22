from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db
from ..config import ConfigError, build_settings
from ..dns import ensure_dns_files
from ..scheduler import FetchScheduler
from . import data as web_data

LOGGER = logging.getLogger("fritz_lan_dns.web")
BASE = Path(__file__).resolve().parent

settings = build_settings()
settings.ensure_dirs()
db.init_schema(settings.db_path)
try:
    with db.connect(settings.db_path) as _conn:
        ensure_dns_files(settings, _conn)
except ConfigError as exc:
    raise SystemExit(f"Konfigurationsfehler: {exc}") from exc

scheduler = FetchScheduler(settings)


def _check_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin:
        host = urlsplit(origin).hostname
        if host and host != request.url.hostname:
            raise HTTPException(status_code=403, detail="Cross-Origin nicht erlaubt")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-Origin nicht erlaubt")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.scheduler_enabled:
        scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(title="Fritz!Box LAN DNS", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


def _conn():
    return db.connect(settings.db_path)


def _serialize_records(records):
    return [asdict(record) for record in records]


@app.get("/health")
def health() -> JSONResponse:
    with _conn() as conn:
        latest = db.latest_snapshot(conn)
    ok = bool(latest and latest.get("status") == "ok")
    return JSONResponse(
        {
            "status": "ok" if ok else "degraded",
            "latest_snapshot": latest.get("id") if latest else None,
            "latest_status": latest.get("status") if latest else None,
        },
        status_code=200,
    )


@app.get("/api/status")
def api_status() -> JSONResponse:
    with _conn() as conn:
        latest = db.latest_snapshot(conn)
        counts = db.counts(conn)
    return JSONResponse({"scheduler": scheduler.status(), "latest": latest, "counts": counts})


@app.post("/api/refresh")
async def api_refresh(request: Request) -> JSONResponse:
    _check_same_origin(request)
    if not scheduler.trigger():
        raise HTTPException(status_code=409, detail="Abruf läuft bereits")
    return JSONResponse({"status": "accepted"}, status_code=202)


@app.get("/api/topology")
def api_topology() -> JSONResponse:
    with _conn() as conn:
        result = web_data.topology(conn)
    return JSONResponse(
        {
            "latest": result["latest"],
            "nodes": [asdict(row["node"]) | {"depth": row["depth"]} for row in result["rows"]],
            "offline": [asdict(node) for node in result["offline"]],
            "links": [asdict(link) for link in result["links"]],
        }
    )


@app.get("/api/hosts")
def api_hosts(q: str = "", status: str = "") -> JSONResponse:
    with _conn() as conn:
        rows = web_data.hosts(conn, query=q, status=status)
    return JSONResponse({"hosts": rows})


@app.get("/api/dns")
def api_dns() -> JSONResponse:
    with _conn() as conn:
        result = web_data.dns(conn, settings)
    return JSONResponse(
        {
            "records": _serialize_records(result["records"]),
            "static": result["static"],
            "serial": result["serial"],
            "latest": result["latest"],
        }
    )


@app.get("/api/snapshots")
def api_snapshots(limit: int = 100) -> JSONResponse:
    with _conn() as conn:
        result = web_data.history(conn, limit=limit)
    return JSONResponse(result)


@app.get("/api/snapshots/{snapshot_id}")
def api_snapshot(snapshot_id: int) -> JSONResponse:
    with _conn() as conn:
        result = web_data.snapshot(conn, snapshot_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Snapshot nicht gefunden")
    return JSONResponse(
        {
            "snapshot": result["snapshot"],
            "nodes": [asdict(node) for node in result["nodes"]],
            "links": [asdict(link) for link in result["links"]],
        }
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    with _conn() as conn:
        context = web_data.dashboard(conn, settings)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"request": request, "active_page": "dashboard", **context},
    )


@app.get("/topology", response_class=HTMLResponse)
def topology_page(request: Request) -> HTMLResponse:
    with _conn() as conn:
        context = web_data.topology(conn)
    return templates.TemplateResponse(
        request,
        "topology.html",
        {"request": request, "active_page": "topology", **context},
    )


@app.get("/hosts", response_class=HTMLResponse)
def hosts_page(request: Request, q: str = "", status: str = "") -> HTMLResponse:
    with _conn() as conn:
        rows = web_data.hosts(conn, query=q, status=status)
        latest = db.latest_snapshot(conn)
    return templates.TemplateResponse(
        request,
        "hosts.html",
        {
            "request": request,
            "active_page": "hosts",
            "hosts": rows,
            "query": q,
            "status": status,
            "latest": latest,
        },
    )


@app.get("/dns", response_class=HTMLResponse)
def dns_page(request: Request) -> HTMLResponse:
    with _conn() as conn:
        context = web_data.dns(conn, settings)
    corefile = settings.corefile.read_text(encoding="utf-8") if settings.corefile.exists() else ""
    return templates.TemplateResponse(
        request,
        "dns.html",
        {
            "request": request,
            "active_page": "dns",
            "corefile": corefile,
            **context,
        },
    )


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    with _conn() as conn:
        context = web_data.history(conn)
    return templates.TemplateResponse(
        request,
        "history.html",
        {"request": request, "active_page": "history", **context},
    )


@app.get("/dns/hosts")
def download_hosts() -> FileResponse:
    if not settings.hosts_file.exists():
        raise HTTPException(status_code=404, detail="hosts-Datei fehlt")
    return FileResponse(settings.hosts_file, media_type="text/plain", filename="hosts")


@app.get("/dns/zone")
def download_zone() -> FileResponse:
    if not settings.zone_file.exists():
        raise HTTPException(status_code=404, detail="Zone-Datei fehlt")
    return FileResponse(
        settings.zone_file,
        media_type="text/plain",
        filename=f"{settings.dns_domain}.zone",
    )


@app.get("/dns/corefile")
def download_corefile() -> FileResponse:
    if not settings.corefile.exists():
        raise HTTPException(status_code=404, detail="Corefile fehlt")
    return FileResponse(settings.corefile, media_type="text/plain", filename="Corefile")
