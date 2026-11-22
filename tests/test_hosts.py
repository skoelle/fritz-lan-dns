from __future__ import annotations

import json
from pathlib import Path

from fritz_lan_dns.fritz.hosts import parse_host_entries
from fritz_lan_dns.fritz.mesh import parse_mesh
from fritz_lan_dns.merge import merge_hosts
from fritz_lan_dns.models import HostRecord
from fritz_lan_dns.util import device_description

FIXTURE = Path(__file__).parent / "fixtures" / "mesh.json"


def _entries() -> list[dict]:
    return [
        {
            "NewHostName": "router",
            "NewIPAddress": "192.0.2.1",
            "NewMACAddress": "AA:BB:CC:00:00:01",
            "NewActive": "1",
            "NewInterfaceType": "LAN",
            "NewX_AVM-DE_LastSeen": "2026-11-22T20:00:00Z",
        },
        {
            "NewHostName": "repeater",
            "NewIPAddress": "192.0.2.10",
            "NewMACAddress": "AA:BB:CC:00:00:02",
            "NewActive": "0",
            "NewInterfaceType": "LAN",
            "NewX_AVM-DE_LastSeen": "2026-11-20T10:00:00Z",
        },
    ]


def test_parse_host_entries() -> None:
    hosts = parse_host_entries(_entries(), {"router": "Router"})
    assert hosts[0].hostname == "router"
    assert hosts[0].key == "mac:aa:bb:cc:00:00:01"
    assert hosts[0].active is True
    assert hosts[0].description == "Router"
    assert hosts[1].active is False


def test_merge_fritz_and_mesh_hosts() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mesh = parse_mesh(raw, {"client": "Notebook"})
    hosts = merge_hosts(parse_host_entries(_entries()), mesh.nodes)
    by_name = {host.hostname: host for host in hosts}

    assert by_name["router"].source == "fritz"
    assert by_name["repeater"].ip == "192.0.2.10"
    assert by_name["repeater"].source == "fritz"
    assert by_name["client"].source == "mesh"
    assert by_name["client"].description == "Notebook"
    assert by_name["offline"].active is False
    assert len({host.key for host in hosts}) == len(hosts)


def test_merge_extra_openwrt_hosts() -> None:
    extra = [
        HostRecord(
            key="mac:aa:bb:cc:00:00:10",
            hostname="owrt-nas",
            ip="192.0.2.150",
            mac="AA:BB:CC:00:00:10",
            active=True,
            source="openwrt",
        )
    ]
    hosts = merge_hosts(
        parse_host_entries(_entries()),
        [],
        {"owrt-nas": "OpenWrt NAS"},
        extra_hosts=extra,
    )
    by_name = {host.hostname: host for host in hosts}

    assert by_name["owrt-nas"].source == "openwrt"
    assert by_name["owrt-nas"].ip == "192.0.2.150"
    assert by_name["owrt-nas"].description == "OpenWrt NAS"
    assert by_name["router"].source == "fritz"
    assert len({host.key for host in hosts}) == len(hosts)


def test_merge_extra_openwrt_uses_base_name_description() -> None:
    extra = [
        HostRecord(
            key="mac:aa:bb:cc:00:00:12",
            hostname="nas.lan",
            ip="192.0.2.152",
            mac="AA:BB:CC:00:00:12",
            active=True,
            source="openwrt",
        )
    ]
    hosts = merge_hosts(
        parse_host_entries(_entries()),
        [],
        {"nas": "NAS"},
        extra_hosts=extra,
    )
    nas = next(host for host in hosts if host.hostname == "nas.lan")
    assert nas.description == "NAS"


def test_device_description_lookup() -> None:
    names = {"nas": "NAS", "printer.lan": "Printer LAN"}

    assert device_description("nas", names) == "NAS"
    assert device_description("nas.lan", names) == "NAS"
    assert device_description("Printer.LAN", names) == "Printer LAN"
    assert device_description("unknown", names) == ""
    assert device_description("", names) == ""


def test_merge_keeps_openwrt_ip_for_shared_mac() -> None:
    extra = [
        HostRecord(
            key="mac:aa:bb:cc:00:00:01",
            hostname="router-openwrt",
            ip="192.0.2.200",
            mac="AA:BB:CC:00:00:01",
            active=True,
            source="openwrt",
        )
    ]
    hosts = merge_hosts(parse_host_entries(_entries()), [], extra_hosts=extra)
    by_name = {host.hostname: host for host in hosts}

    assert by_name["router"].source == "fritz"
    assert by_name["router"].ip == "192.0.2.1"
    assert by_name["router-openwrt"].source == "openwrt"
    assert by_name["router-openwrt"].ip == "192.0.2.200"
    assert by_name["router-openwrt"].key.startswith("openwrt:")
    assert len({host.key for host in hosts}) == len(hosts)


def test_merge_deduplicates_openwrt_host_with_same_ip() -> None:
    extra = [
        HostRecord(
            key="mac:aa:bb:cc:00:00:01",
            hostname="router",
            ip="192.0.2.1",
            mac="AA:BB:CC:00:00:01",
            active=True,
            source="openwrt",
        )
    ]
    hosts = merge_hosts(parse_host_entries(_entries()), [], extra_hosts=extra)
    routers = [
        host
        for host in hosts
        if host.mac and host.mac.lower() == "aa:bb:cc:00:00:01"
    ]

    assert len(routers) == 1
    assert routers[0].source == "fritz"
    assert len({host.key for host in hosts}) == len(hosts)


def test_merge_marks_mesh_node_online_even_if_host_entry_inactive() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mesh = parse_mesh(raw)
    hosts = merge_hosts(parse_host_entries(_entries()), mesh.nodes)
    repeater = next(host for host in hosts if host.hostname == "repeater")
    assert repeater.active is True
