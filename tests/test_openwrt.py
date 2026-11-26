from __future__ import annotations

import pytest

from fritz_lan_dns.openwrt import OpenWrtClient, OpenWrtError, parse_openwrt_hosts

LOGIN_OK = {"jsonrpc": "2.0", "id": 1, "result": [0, {"ubus_rpc_session": "sid"}]}
SESSION_EXPIRED = {
    "jsonrpc": "2.0",
    "id": 1,
    "error": {"code": -32002, "message": "Access denied"},
}


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


def _client(monkeypatch, responses) -> OpenWrtClient:
    client = OpenWrtClient("http://192.0.2.3", "root", "secret")
    queue = list(responses)

    def fake_post(*args, **kwargs):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(client.session, "post", fake_post)
    return client


def test_parse_leases_and_hints() -> None:
    leases = {
        "dhcp_leases": [
            {
                "macaddr": "AA:BB:CC:00:00:01",
                "ipaddr": "192.0.2.150",
                "hostname": "nas",
                "expires": 100,
            },
            {
                "macaddr": "AA:BB:CC:00:00:02",
                "ipaddr": "192.0.2.151",
                "hostname": "*",
                "expires": 100,
            },
            {
                "macaddr": "AA:BB:CC:00:00:03",
                "ipaddr": "192.0.2.152",
                "hostname": "expired",
                "expires": 0,
            },
        ],
        "dhcp6_leases": [],
    }
    hints = {
        "AA:BB:CC:00:00:01": {"name": "nas.lan", "ipaddrs": ["192.0.2.150"]},
        "AA:BB:CC:00:00:04": {
            "name": "printer",
            "ipaddrs": ["192.0.2.153", "192.0.2.154"],
        },
        "AA:BB:CC:00:00:05": {"ipaddrs": ["192.0.2.155"]},
        "AA:BB:CC:00:00:06": {"name": "no-ip", "ipaddrs": []},
    }

    hosts = parse_openwrt_hosts(leases, hints)
    by_name = {host.hostname: host for host in hosts}

    assert set(by_name) == {"nas", "printer"}
    assert by_name["nas"].active is True
    assert by_name["nas"].ip == "192.0.2.150"
    assert by_name["nas"].source == "openwrt"
    assert by_name["nas"].address_source == "dhcp"
    assert by_name["nas"].key == "mac:aa:bb:cc:00:00:01"
    assert by_name["printer"].active is False
    assert by_name["printer"].ip == "192.0.2.153"
    assert by_name["printer"].address_source == ""


def test_parse_ipv6_is_optional() -> None:
    leases = {
        "dhcp_leases": [],
        "dhcp6_leases": [
            {
                "macaddr": "AA:BB:CC:00:00:07",
                "ip6addr": "fd00::10",
                "hostname": "v6host",
                "expires": 100,
            }
        ],
    }
    assert parse_openwrt_hosts(leases, {}) == []
    hosts = parse_openwrt_hosts(leases, {}, include_ipv6=True)
    assert len(hosts) == 1
    assert hosts[0].ip == "fd00::10"
    assert hosts[0].active is True


def test_client_login_and_lease_call(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        [
            FakeResponse(LOGIN_OK),
            FakeResponse({"jsonrpc": "2.0", "id": 1, "result": [0, {"dhcp_leases": []}]}),
        ],
    )
    assert client.get_dhcp_leases() == {"dhcp_leases": []}
    assert client.endpoint == "http://192.0.2.3/ubus"


def test_client_mwan3_status(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        [
            FakeResponse(LOGIN_OK),
            FakeResponse({"jsonrpc": "2.0", "id": 1, "result": [0, {"interfaces": {}}]}),
        ],
    )
    assert client.get_mwan3_status() == {"interfaces": {}}


def test_client_network_and_wireless_calls(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        [
            FakeResponse(LOGIN_OK),
            FakeResponse({"jsonrpc": "2.0", "id": 1, "result": [0, {"interface": []}]}),
            FakeResponse({"jsonrpc": "2.0", "id": 1, "result": [0, {"signal": -42}]}),
        ],
    )
    assert client.get_network_interfaces() == {"interface": []}
    assert client.get_wireless_info("phy1-sta0") == {"signal": -42}


def test_client_retries_after_session_expired(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        [
            FakeResponse(LOGIN_OK),
            FakeResponse(SESSION_EXPIRED),
            FakeResponse(LOGIN_OK),
            FakeResponse({"jsonrpc": "2.0", "id": 1, "result": [0, {"dhcp_leases": []}]}),
        ],
    )
    assert client.get_dhcp_leases() == {"dhcp_leases": []}


def test_client_falls_back_to_luci_object(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        [
            FakeResponse(LOGIN_OK),
            FakeResponse({"jsonrpc": "2.0", "id": 1, "result": [4]}),
            FakeResponse({"jsonrpc": "2.0", "id": 1, "result": [0, {"00:11": {}}]}),
        ],
    )
    assert client.get_host_hints() == {"00:11": {}}


def test_client_login_failure_raises(monkeypatch) -> None:
    client = _client(
        monkeypatch, [FakeResponse({"jsonrpc": "2.0", "id": 1, "result": [6]})]
    )
    with pytest.raises(OpenWrtError):
        client.get_dhcp_leases()


def test_parse_static_leases_marks_and_adds_hosts() -> None:
    leases = {
        "dhcp_leases": [
            {
                "macaddr": "AA:BB:CC:00:00:01",
                "ipaddr": "192.0.2.150",
                "hostname": "nas",
                "expires": 100,
            },
            {
                "macaddr": "AA:BB:CC:00:00:02",
                "ipaddr": "192.0.2.151",
                "hostname": "dynamic",
                "expires": 100,
            },
        ],
        "dhcp6_leases": [],
    }
    hints = {
        "AA:BB:CC:00:00:04": {
            "name": "static-hint",
            "ipaddrs": ["192.0.2.152"],
        }
    }
    static_leases = [
        {
            "name": "nas",
            "ip": "192.0.2.150",
            "macs": ["aa:bb:cc:00:00:01"],
        },
        {
            "name": "static-hint",
            "ip": "192.0.2.152",
            "macs": ["aa:bb:cc:00:00:04"],
        },
        {
            "name": "offline-static",
            "ip": "192.0.2.160",
            "macs": ["aa:bb:cc:00:00:05"],
        },
    ]

    hosts = parse_openwrt_hosts(leases, hints, static_leases=static_leases)
    by_name = {host.hostname: host for host in hosts}

    assert by_name["nas"].active is True
    assert by_name["nas"].address_source == "dhcp"
    assert by_name["nas"].static_dhcp is True
    assert by_name["dynamic"].static_dhcp is False
    assert by_name["static-hint"].active is False
    assert by_name["static-hint"].address_source == "dhcp"
    assert by_name["static-hint"].static_dhcp is True
    assert by_name["offline-static"].active is False
    assert by_name["offline-static"].ip == "192.0.2.160"
    assert by_name["offline-static"].static_dhcp is True


def test_client_reads_static_leases(monkeypatch) -> None:
    client = OpenWrtClient("http://192.0.2.3", "root", "secret")
    monkeypatch.setattr(
        client,
        "call",
        lambda *args, **kwargs: {
            "values": {
                "cfg1": {
                    ".type": "host",
                    "name": "nas",
                    "ip": "192.0.2.150",
                    "mac": ["AA:BB:CC:00:00:01"],
                },
                "cfg2": {
                    ".type": "host",
                    "hostname": "printer",
                    "mac": "AA:BB:CC:00:00:02",
                },
                "lan": {".type": "dhcp", "interface": "lan"},
            }
        },
    )
    assert client.get_static_leases() == [
        {"name": "nas", "ip": "192.0.2.150", "macs": ["aa:bb:cc:00:00:01"]},
        {"name": "printer", "ip": "", "macs": ["aa:bb:cc:00:00:02"]},
    ]


def test_static_lease_keeps_ip_when_hint_has_other_ip() -> None:
    hints = {
        "AA:BB:CC:00:00:01": {
            "name": "backuppi",
            "ipaddrs": ["192.0.2.87"],
        }
    }
    static_leases = [
        {
            "name": "backuppi",
            "ip": "192.0.2.58",
            "macs": ["aa:bb:cc:00:00:01"],
        }
    ]
    hosts = parse_openwrt_hosts({}, hints, static_leases=static_leases)
    backuppi = [host for host in hosts if host.hostname == "backuppi"]
    assert {host.ip for host in backuppi} == {"192.0.2.87", "192.0.2.58"}
    assert all(host.static_dhcp is True for host in backuppi)
