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
