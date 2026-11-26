from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime

from ..models import WlanEvent

EVENT_SEVERITY = {
    674: "info",
    719: "info",
    783: "critical",
    787: "warning",
    791: "warning",
    794: "warning",
    796: "critical",
}

EVENT_LABELS = {
    674: "2,4-GHz-Bandbreite reduziert",
    719: "Repeater-Anmeldung fehlgeschlagen",
    783: "Starke Störquelle, Kanalwechsel",
    787: "Temporärer DFS-Kanalwechsel",
    791: "Störquelle erkannt",
    794: "DFS-Prüfung (Kanal gesperrt)",
    796: "Radar erkannt, Kanalwechsel",
}

SEVERITY_LABELS = {
    "critical": "Kritisch",
    "warning": "Warnung",
    "info": "Info",
}

_AGGREGATE_RE = re.compile(
    r"\s*\[(\d+)\s+Meldungen seit\s+"
    r"([0-9]{2}\.[0-9]{2}\.[0-9]{2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})\]\s*$"
)
_NODE_RE = re.compile(r"^\[([^\]]+)\]\s*")
_CHANNEL_RE = re.compile(r"\bKanal\s+([0-9]+(?:-[0-9]+)?)")
_DATE_FORMAT = "%d.%m.%y %H:%M:%S"
_STORAGE_FORMAT = "%Y-%m-%d %H:%M:%S"


class DeviceLogParseError(ValueError):
    pass


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _DATE_FORMAT)
    except ValueError:
        return None


def _storage_timestamp(value: datetime) -> str:
    return value.strftime(_STORAGE_FORMAT)


def parse_device_log(xml_text: str) -> list[WlanEvent]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise DeviceLogParseError(f"Ungültiges Ereignisprotokoll: {exc}") from exc

    events: list[WlanEvent] = []
    for element in root.findall("Event"):
        if (element.findtext("group") or "").strip() != "wlan":
            continue
        raw_id = (element.findtext("id") or "").strip()
        try:
            event_id = int(raw_id)
        except ValueError:
            continue
        date = (element.findtext("date") or "").strip()
        time = (element.findtext("time") or "").strip()
        parsed_at = _parse_datetime(f"{date} {time}")
        if parsed_at is None:
            continue

        message = re.sub(r"\s+", " ", element.findtext("msg") or "").strip()
        occurrences = 1
        first_seen = _storage_timestamp(parsed_at)
        aggregate = _AGGREGATE_RE.search(message)
        if aggregate:
            occurrences = int(aggregate.group(1))
            aggregate_at = _parse_datetime(aggregate.group(2))
            if aggregate_at is not None:
                first_seen = _storage_timestamp(aggregate_at)
            message = message[: aggregate.start()].rstrip()

        node = ""
        node_match = _NODE_RE.match(message)
        if node_match:
            node = node_match.group(1).strip()
            message = message[node_match.end() :].strip()

        channel = ""
        channel_match = _CHANNEL_RE.search(message)
        if channel_match:
            channel = channel_match.group(1)

        events.append(
            WlanEvent(
                event_id=event_id,
                group="wlan",
                severity=EVENT_SEVERITY.get(event_id, "info"),
                node=node,
                channel=channel,
                message=message,
                event_at=_storage_timestamp(parsed_at),
                first_seen=first_seen,
                occurrences=occurrences,
                fingerprint=f"{event_id}|{node}|{channel}|{message}",
            )
        )

    events.sort(key=lambda event: event.event_at)
    return events
