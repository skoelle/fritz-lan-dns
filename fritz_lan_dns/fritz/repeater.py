from __future__ import annotations

from ..models import HostRecord, WlanClient, WlanInterface, WlanSnapshot
from ..util import parse_int
from .client import FritzBoxError, FritzClient
from .wlan import band_from_channel, band_from_frequency, format_channel_width

_MAX_CONFIGURATIONS = 6


def _band_from_tr064(value: object, channel: int | None) -> str:
    raw = str(value or "").strip()
    if raw in {"2400", "5000", "6000"}:
        return band_from_frequency(raw)
    return band_from_channel(channel)


def _clean_ip(value: object) -> str | None:
    text = str(value or "").strip()
    if not text or text in {"0.0.0.0", "::"}:
        return None
    return text


def _client_name(
    host_by_mac: dict[str, HostRecord],
    host_by_ip: dict[str, HostRecord],
    mac: str | None,
    ip: str | None,
) -> tuple[str, str]:
    host = (host_by_mac.get(mac.lower()) if mac else None) or (
        host_by_ip.get(ip) if ip else None
    )
    if host is None:
        return ip or mac or "-", ""
    return host.hostname or ip or mac or "-", host.description


def fetch_repeater_wlan(
    client: FritzClient,
    *,
    ip: str,
    device_name: str | None = None,
    description: str = "",
    host_by_mac: dict[str, HostRecord] | None = None,
    host_by_ip: dict[str, HostRecord] | None = None,
) -> WlanSnapshot:
    host_by_mac = host_by_mac or {}
    host_by_ip = host_by_ip or {}
    model = ""
    friendly = ""
    try:
        info = client.get_device_info()
        model = str(info.get("NewModelName") or "").strip()
        friendly = str(info.get("NewFriendlyName") or "").strip()
    except Exception:  # noqa: BLE001 - DeviceInfo ist optional
        pass

    name = (device_name or friendly or ip).strip() or ip
    interfaces: list[WlanInterface] = []
    clients: list[WlanClient] = []
    last_error: FritzBoxError | None = None
    failures = 0
    for index in range(1, _MAX_CONFIGURATIONS + 1):
        try:
            values = client.get_wlan_info(index)
        except FritzBoxError as exc:
            last_error = exc
            failures += 1
            if interfaces and failures >= 2:
                break
            continue
        failures = 0
        channel = parse_int(values.get("NewChannel"))
        band = _band_from_tr064(values.get("NewX_AVM-DE_FrequencyBand"), channel)
        ssid = str(values.get("NewSSID") or "").strip()
        guest = str(values.get("NewX_AVM-DE_APType") or "").strip().lower() == "guest"
        security = (
            str(values.get("NewX_AVM-DE_EncryptionMode") or "").strip()
            or str(values.get("NewBeaconType") or "").strip()
        )
        try:
            associations = client.get_wlan_associations(index)
        except FritzBoxError:
            associations = []
        interface = WlanInterface(
            node_uid=f"repeater:{ip}",
            device_name=name,
            device_model=model,
            device_ip=ip,
            online=True,
            source="fritz-repeater",
            description=description,
            interface_name=f"WLAN {index}",
            ssid=ssid,
            band=band,
            channel=channel,
            channel_width=format_channel_width(
                values.get("NewX_AVM-DE_ChannelWidth")
            ),
            channel_utilization=None,
            security=security,
            opmode="AP_GUEST" if guest else "AP",
            phymodes=str(values.get("NewStandard") or "").strip(),
            streams_tx=None,
            streams_rx=None,
            steering_enabled=False,
            is_guest=guest,
            is_uplink=False,
            client_count=len(associations),
        )
        interfaces.append(interface)
        for position, entry in enumerate(associations):
            mac = str(entry.get("NewAssociatedDeviceMACAddress") or "").strip().lower()
            mac = mac or None
            address = _clean_ip(entry.get("NewAssociatedDeviceIPAddress"))
            client_name, client_description = _client_name(
                host_by_mac, host_by_ip, mac, address
            )
            speed = parse_int(entry.get("NewX_AVM-DE_Speed"))
            kbps = speed * 1000 if speed is not None else None
            signal = parse_int(entry.get("NewX_AVM-DE_SignalStrength"))
            clients.append(
                WlanClient(
                    ap_node_uid=interface.node_uid,
                    ap_interface_name=interface.interface_name,
                    ap_device_name=interface.device_name,
                    ap_ssid=interface.ssid,
                    ap_band=interface.band,
                    client_node_uid=f"repeater:{ip}:{mac or position}",
                    client_name=client_name,
                    client_ip=address,
                    client_mac=mac,
                    online=True,
                    signal=signal,
                    signal_unit="%",
                    rx_kbps=kbps,
                    tx_kbps=kbps,
                    last_connected=None,
                    source="fritz-repeater",
                    description=client_description,
                )
            )

    if not interfaces:
        raise last_error or FritzBoxError("Keine WLAN-Konfiguration gefunden")

    clients.sort(
        key=lambda item: (
            item.ap_interface_name.lower(),
            item.client_name.lower(),
            item.client_mac or "",
        )
    )
    return WlanSnapshot(interfaces=interfaces, clients=clients)
