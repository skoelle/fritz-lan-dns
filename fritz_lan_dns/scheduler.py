from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import db
from .config import Settings
from .fetch import run_fetch

LOGGER = logging.getLogger("fritz_lan_dns.scheduler")


class FetchScheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._task: asyncio.Task | None = None
        self._trigger = asyncio.Event()
        self._lock = asyncio.Lock()
        self.running = False
        self.last_started: str | None = None
        self.last_finished: str | None = None
        self.last_error: str | None = None
        self.last_result: dict | None = None
        self.next_run: datetime | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="fritz-lan-dns-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def trigger(self) -> bool:
        if self.running:
            return False
        self._trigger.set()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_once(), name="fritz-lan-dns-fetch")
        return True

    def status(self) -> dict:
        return {
            "enabled": self.settings.scheduler_enabled,
            "running": self.running,
            "last_started": self.last_started,
            "last_finished": self.last_finished,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "interval_minutes": self.settings.fetch_interval_minutes,
        }

    def _last_success_at(self) -> datetime | None:
        with db.connect(self.settings.db_path) as conn:
            snapshot = db.latest_snapshot(conn, only_successful=True)
        if not snapshot or not snapshot.get("started_at"):
            return None
        try:
            value = datetime.fromisoformat(snapshot["started_at"])
        except ValueError:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def _is_due(self) -> bool:
        last = self._last_success_at()
        if last is None:
            return True
        interval = timedelta(minutes=max(1, self.settings.fetch_interval_minutes))
        return datetime.now(timezone.utc) >= last + interval

    async def _loop(self) -> None:
        while True:
            try:
                if self._is_due() or self._trigger.is_set():
                    self._trigger.clear()
                    await self._run_once()
                self.next_run = datetime.now(timezone.utc) + timedelta(
                    minutes=max(1, self.settings.fetch_interval_minutes)
                )
                try:
                    await asyncio.wait_for(self._trigger.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Scheduler darf nicht sterben
                LOGGER.exception("Scheduler-Schleife fehlgeschlagen")
                await asyncio.sleep(30)

    async def _run_once(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            self.running = True
            self.last_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                result = await asyncio.to_thread(run_fetch, self.settings)
                self.last_result = result
                self.last_error = None
            except Exception as exc:  # noqa: BLE001 - Fehler im Status anzeigen
                self.last_error = str(exc)
                LOGGER.exception("Geplanter Abruf fehlgeschlagen")
            finally:
                self.running = False
                self.last_finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
