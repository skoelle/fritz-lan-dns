from __future__ import annotations

import json
from pathlib import Path

import pytest

from fritz_lan_dns.fritz.client import FritzBoxError
from fritz_lan_dns.fritz.mesh import parse_mesh
from fritz_lan_dns.fritz.repeater import fetch_repeater_wlan
from fritz_lan_dns.fritz.wlan import (
    band_from_channel,
    band_from_frequency,
    band_from_interface_name,
    parse_wlan,
)
from fritz_lan_dns.models import HostRecord

FIXTURE = Path(__file__).parent / "fixtures" / "mesh.json"


def _wlan():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mesh = parse_mesh(raw)
    return parse_wlan(raw, {node.uid: node for node in mesh.nodes})


def test_parse_wlan_interfaces_and_clients() -> None:
    wlan = _wlan()
    by_iface = {
        (interface.node_uid, interface.interface_name): interface
        for interface in wlan.interfaces
    }

    master = by_iface[("uuid-master", "AP:5G:0")]
    assert master.band == "5 GHz"
    assert master.channel == 36
    assert master.channel_width == "80 MHz"
    assert master.channel_utilization == 12
    assert master.ssid == "example"
    assert master.security == "WPA2_WPA3_MIXED"
    assert master.streams_tx == 2
    assert master.streams_rx == 2
    assert master.is_uplink is False
    assert master.client_count == 1

    guest = by_iface[("uuid-master", "AP_GUEST:2G:0")]
    assert guest.is_guest is True
    assert guest.band == "2.4 GHz"
    assert guest.client_count == 0

    repeater_uplink = by_iface[("uuid-repeater", "UPLINK:5G:0")]
    assert repeater_uplink.is_uplink is True
    assert repeater_uplink.band == "5 GHz"

    assert len(wlan.clients) == 1
    client = wlan.clients[0]
    assert client.client_node_uid == "uuid-wlan"
    assert client.ap_device_name == "router"
    assert client.ap_interface_name == "AP:5G:0"
    assert client.signal == -58
    assert client.signal_unit == "dBm"
    assert client.rx_kbps == 866000
    assert client.tx_kbps == 866000


def test_wlan_band_helpers() -> None:
    assert band_from_frequency(2462000) == "2.4 GHz"
    assert band_from_frequency(5180000) == "5 GHz"
    assert band_from_frequency(6115000) == "6 GHz"
    assert band_from_frequency(None) == ""
    assert band_from_channel(6) == "2.4 GHz"
    assert band_from_channel(36) == "5 GHz"
    assert band_from_channel(0) == ""
    assert band_from_interface_name("AP:6G:0") == "6 GHz"


class FakeRepeaterClient:
    def __init__(self) -> None:
        self.index_calls: list[int] = []

    def get_device_info(self) -> dict[str, str]:
        return {"NewModelName": "FRITZ!Repeater 2400"}

    def get_wlan_info(self, index: int) -> dict[str, str]:
        self.index_calls.append(index)
        if index == 1:
            return {
                "NewSSID": "example",
                "NewChannel": "36",
                "NewX_AVM-DE_FrequencyBand": "5000",
                "NewStandard": "ax",
                "NewBeaconType": "11iandWPA3",
                "NewX_AVM-DE_APType": "normal",
            }
        if index == 2:
            return {
                "NewSSID": "example-guest",
                "NewChannel": "6",
                "NewX_AVM-DE_FrequencyBand": "2400",
                "NewStandard": "n",
                "NewBeaconType": "11i",
                "NewX_AVM-DE_APType": "guest",
            }
        raise FritzBoxError("kein Dienst")

    def get_wlan_associations(self, index: int) -> list[dict[str, str]]:
        if index != 1:
            return []
        return [
            {
                "NewAssociatedDeviceMACAddress": "AA:BB:CC:00:00:01",
                "NewAssociatedDeviceIPAddress": "192.0.2.150",
                "NewX_AVM-DE_Speed": "866",
                "NewX_AVM-DE_SignalStrength": "72",
            }
        ]


def test_fetch_repeater_wlan() -> None:
    client = FakeRepeaterClient()
    wlan = fetch_repeater_wlan(
        client,  # type: ignore[arg-type]
        ip="192.0.2.10",
        device_name="repeater",
        host_by_mac={
            "aa:bb:cc:00:00:01": HostRecord(
                key="mac:aa:bb:cc:00:00:01",
                hostname="tablet",
                ip="192.0.2.150",
                mac="aa:bb:cc:00:00:01",
                active=True,
                description="Tablet",
            )
        },
    )

    assert client.index_calls[:2] == [1, 2]
    assert len(wlan.interfaces) == 2
    assert wlan.interfaces[0].device_model == "FRITZ!Repeater 2400"
    assert wlan.interfaces[0].band == "5 GHz"
    assert wlan.interfaces[0].client_count == 1
    assert wlan.interfaces[1].is_guest is True
    assert len(wlan.clients) == 1
    assert wlan.clients[0].client_name == "tablet"
    assert wlan.clients[0].description == "Tablet"
    assert wlan.clients[0].signal == 72
    assert wlan.clients[0].signal_unit == "%"
    assert wlan.clients[0].rx_kbps == 866000


def test_fetch_repeater_wlan_without_configuration() -> None:
    class EmptyClient(FakeRepeaterClient):
        def get_device_info(self) -> dict[str, str]:
            raise FritzBoxError("kein DeviceInfo")

        def get_wlan_info(self, index: int) -> dict[str, str]:
            raise FritzBoxError("kein WLAN")

    with pytest.raises(FritzBoxError):
        fetch_repeater_wlan(EmptyClient(), ip="192.0.2.10")  # type: ignore[arg-type]
