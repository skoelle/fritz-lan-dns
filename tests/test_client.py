from __future__ import annotations

import pytest

from fritz_lan_dns.fritz.client import (
    FritzBoxError,
    FritzClient,
    _parse_new_values,
    load_mesh_json,
)


def test_parse_soap_values() -> None:
    xml = """
    <s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
      <s:Body>
        <u:GetSecurityPortResponse xmlns:u="urn:dslforum-org:service:DeviceInfo:1">
          <NewSecurityPort>49443</NewSecurityPort>
        </u:GetSecurityPortResponse>
      </s:Body>
    </s:Envelope>
    """
    values = _parse_new_values(xml)
    assert values["NewSecurityPort"] == "49443"


def test_load_mesh_json_tolerates_trailing_commas() -> None:
    raw = '{"nodes": [{"uid": "a", "device_name": "x",},],}'
    assert load_mesh_json(raw)["nodes"][0]["uid"] == "a"


class FakeSoapResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


def test_client_reads_wlan_configuration_and_associations(monkeypatch) -> None:
    responses = iter(
        [
            "<root><NewSSID>example</NewSSID><NewChannel>36</NewChannel></root>",
            "<root><NewTotalAssociations>2</NewTotalAssociations></root>",
            "<root><NewAssociatedDeviceMACAddress>aa:bb:cc:00:00:01</NewAssociatedDeviceMACAddress></root>",
            "<root><NewAssociatedDeviceMACAddress>aa:bb:cc:00:00:02</NewAssociatedDeviceMACAddress></root>",
        ]
    )
    client = FritzClient("192.0.2.10", "user", "password")
    monkeypatch.setattr(
        client.session,
        "post",
        lambda *args, **kwargs: FakeSoapResponse(next(responses)),
    )

    assert client.get_wlan_info(2) == {
        "NewSSID": "example",
        "NewChannel": "36",
    }
    associations = client.get_wlan_associations(2)
    assert len(associations) == 2
    assert associations[0]["NewAssociatedDeviceMACAddress"] == "aa:bb:cc:00:00:01"
    assert associations[1]["NewAssociatedDeviceMACAddress"] == "aa:bb:cc:00:00:02"


def test_client_reads_device_log_path(monkeypatch) -> None:
    client = FritzClient("192.0.2.10", "user", "password")
    monkeypatch.setattr(
        client,
        "soap",
        lambda *args, **kwargs: {"NewDeviceLogPath": "/devicelog.lua?sid=test"},
    )
    assert client.get_device_log_path() == "/devicelog.lua?sid=test"


def test_client_rejects_missing_device_log_path(monkeypatch) -> None:
    client = FritzClient("192.0.2.10", "user", "password")
    monkeypatch.setattr(client, "soap", lambda *args, **kwargs: {})
    with pytest.raises(FritzBoxError):
        client.get_device_log_path()


def test_client_fetches_device_log_text(monkeypatch) -> None:
    client = FritzClient("192.0.2.10", "user", "password")
    monkeypatch.setattr(
        client.session,
        "get",
        lambda *args, **kwargs: FakeSoapResponse("<DeviceLog></DeviceLog>"),
    )
    assert (
        client.fetch_device_log_text("/devicelog.lua?sid=test", 49443)
        == "<DeviceLog></DeviceLog>"
    )


class FakeJsonResponse:
    status_code = 200

    def json(self) -> dict:
        return {
            "network": [
                {"mac": "AA:BB:CC:00:00:01", "static_dhcp": "1"},
                {"mac": "AA:BB:CC:00:00:02", "static_dhcp": "0"},
            ]
        }


def test_client_reads_static_dhcp_map(monkeypatch) -> None:
    client = FritzClient("192.0.2.10", "user", "password")
    monkeypatch.setattr(
        client,
        "soap",
        lambda *args, **kwargs: {
            "NewX_AVM-DE_HostListPath": "/devicehostlist.lua?sid=test"
        },
    )
    monkeypatch.setattr(client, "_get", lambda url: FakeJsonResponse())
    assert client.get_static_dhcp_map() == {
        "aa:bb:cc:00:00:01": True,
        "aa:bb:cc:00:00:02": False,
    }
