from __future__ import annotations

import json
from pathlib import Path

from fritz_lan_dns.fritz.mesh import parse_mesh

FIXTURE = Path(__file__).parent / "fixtures" / "mesh.json"


def test_parse_mesh_topology() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mesh = parse_mesh(raw, {"repeater": "Hall", "client": "Notebook"})
    by_name = {node.device_name: node for node in mesh.nodes}

    assert mesh.master_uid == "uuid-master"
    assert by_name["router"].online is True
    assert by_name["repeater"].online is True
    assert by_name["repeater"].parent_uid == "uuid-master"
    assert by_name["repeater"].connection_type == "LAN"
    assert by_name["repeater"].description == "Hall"
    assert by_name["client"].connection_type == "WLAN 5G"
    assert by_name["powerline"].connection_type == "PLC"
    assert by_name["powerline"].parent_uid == "uuid-repeater"
    assert by_name["powerline"].plc_rx_kbps == 180000
    assert by_name["powerline"].plc_tx_kbps == 200000
    assert by_name["offline"].online is False
    assert by_name["offline"].parent_uid is None


def test_parse_mesh_deduplicates_links() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mesh = parse_mesh(raw)
    pairs = [tuple(sorted((link.node_a_uid, link.node_b_uid))) for link in mesh.links]
    assert pairs.count(("uuid-master", "uuid-repeater")) == 1
    assert pairs.count(("uuid-master", "uuid-wlan")) == 1
    assert pairs.count(("uuid-powerline", "uuid-repeater")) == 1


def _raw_wlan_link(master_freq: int, client_freq: int) -> dict:
    return {
        "nodes": [
            {
                "uid": "master",
                "device_name": "router",
                "device_model": "",
                "mesh_role": "master",
                "ip_addresses": [],
                "node_interfaces": [
                    {
                        "type": "WLAN",
                        "name": "",
                        "current_channel": 0,
                        "current_channel_info": {"primary_freq": master_freq},
                        "node_links": [
                            {
                                "node_1_uid": "master",
                                "node_2_uid": "client",
                                "state": "CONNECTED",
                            }
                        ],
                    }
                ],
            },
            {
                "uid": "client",
                "device_name": "client",
                "device_model": "",
                "mesh_role": "client",
                "ip_addresses": [],
                "node_interfaces": [
                    {
                        "type": "WLAN",
                        "name": "",
                        "current_channel": 0,
                        "current_channel_info": {"primary_freq": client_freq},
                        "node_links": [
                            {
                                "node_1_uid": "client",
                                "node_2_uid": "master",
                                "state": "CONNECTED",
                            }
                        ],
                    }
                ],
            },
        ]
    }


def test_parse_mesh_derives_wlan_band_from_frequency() -> None:
    mesh_2g = parse_mesh(_raw_wlan_link(2437000, 2462000))
    by_name = {node.device_name: node for node in mesh_2g.nodes}
    assert by_name["client"].connection_type == "WLAN 2G"

    mesh_5g = parse_mesh(_raw_wlan_link(5180000, 5540000))
    by_name = {node.device_name: node for node in mesh_5g.nodes}
    assert by_name["client"].connection_type == "WLAN 5G"


def test_parse_mesh_derives_offline_band_from_frequency() -> None:
    raw = {
        "nodes": [
            {
                "uid": "master",
                "device_name": "router",
                "device_model": "",
                "mesh_role": "master",
                "ip_addresses": [],
                "node_interfaces": [],
            },
            {
                "uid": "lost",
                "device_name": "lost",
                "device_model": "",
                "mesh_role": "unknown",
                "ip_addresses": [],
                "node_interfaces": [
                    {
                        "type": "WLAN",
                        "name": "",
                        "current_channel": 0,
                        "current_channel_info": {"primary_freq": 2462000},
                    }
                ],
            },
        ]
    }
    mesh = parse_mesh(raw)
    lost = next(node for node in mesh.nodes if node.device_name == "lost")
    assert lost.online is False
    assert lost.connection_type == "WLAN 2G"


def _raw_with_addresses(addresses: list[dict]) -> dict:
    return {
        "nodes": [
            {
                "uid": "node-1",
                "device_name": "node",
                "device_model": "",
                "mesh_role": "client",
                "ip_addresses": addresses,
                "node_interfaces": [],
            }
        ]
    }


def test_parse_mesh_prefers_management_ip_over_link_local() -> None:
    raw = _raw_with_addresses(
        [
            {
                "version": "V4",
                "value": "169.254.1.1/16",
                "attributes": ["LINK_LOCAL", "AUTO_IP", "MANAGEMENT"],
            },
            {
                "version": "V4",
                "value": "192.0.2.2/24",
                "attributes": ["NEIGHBOUR_LIST"],
            },
            {
                "version": "V4",
                "value": "192.0.2.14/24",
                "attributes": ["NEIGHBOUR_LIST", "DHCP", "STATIC", "MANAGEMENT"],
            },
        ]
    )
    assert parse_mesh(raw).nodes[0].ip == "192.0.2.14"


def test_parse_mesh_prefers_lan_ip_over_guest_ip() -> None:
    raw = _raw_with_addresses(
        [
            {
                "version": "V4",
                "value": "169.254.1.1/16",
                "attributes": ["LINK_LOCAL", "AUTO_IP", "MANAGEMENT"],
            },
            {
                "version": "V4",
                "value": "192.0.2.1/24",
                "attributes": ["NEIGHBOUR_LIST", "MANAGEMENT"],
            },
            {
                "version": "V4",
                "value": "198.51.100.1/24",
                "attributes": ["NEIGHBOUR_LIST", "GUEST"],
            },
        ]
    )
    assert parse_mesh(raw).nodes[0].ip == "192.0.2.1"


def test_parse_mesh_prefers_static_over_dhcp_ip() -> None:
    raw = _raw_with_addresses(
        [
            {
                "version": "V4",
                "value": "192.0.2.65/24",
                "attributes": ["NEIGHBOUR_LIST", "DHCP"],
            },
            {
                "version": "V4",
                "value": "192.0.2.70/24",
                "attributes": ["NEIGHBOUR_LIST", "DHCP", "STATIC"],
            },
        ]
    )
    assert parse_mesh(raw).nodes[0].ip == "192.0.2.70"


def test_parse_mesh_falls_back_to_only_link_local_ip() -> None:
    raw = _raw_with_addresses(
        [
            {
                "version": "V4",
                "value": "169.254.154.224/16",
                "attributes": ["NEIGHBOUR_LIST", "LINK_LOCAL", "AUTO_IP"],
            }
        ]
    )
    assert parse_mesh(raw).nodes[0].ip == "169.254.154.224"


def test_parse_mesh_uses_neighbour_only_ip_as_fallback() -> None:
    raw = _raw_with_addresses(
        [
            {
                "version": "V4",
                "value": "192.0.2.22/24",
                "attributes": ["NEIGHBOUR_LIST"],
            }
        ]
    )
    assert parse_mesh(raw).nodes[0].ip == "192.0.2.22"
