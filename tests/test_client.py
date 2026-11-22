from __future__ import annotations

from fritz_lan_dns.fritz.client import _parse_new_values, load_mesh_json


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
